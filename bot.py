import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
import logging
import re
import tempfile
import time
import urllib.parse
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from i18n import normalize_locale, translate
from agent_client import AgentClient, ConsoleUnavailable, DiscordActor
from adapters.console import ConsoleTailer, PlayerTracker
from adapters.engine import ChatOut, Dialect, parse_line, render_outgoing
from adapters.registry import catalog as adapter_catalog, resolve as resolve_adapter
from wings_console import ConsoleStream
from chat_relay import (
    ChatRelay, can_chat, chat_identity, chat_role_ids, discord_message_text, event_color, member_roles,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ptero-bot")

load_dotenv()

# ─── Config ────────────────────────────────────────────────────────────────
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
HEALTH_FILE = Path(os.getenv("HEALTH_FILE", "/tmp/ptero-bot-health"))
CONFIG_LOCK = asyncio.Lock()
API_CONCURRENCY = max(1, int(os.getenv("API_CONCURRENCY", "5")))
API_SEMAPHORE = asyncio.Semaphore(API_CONCURRENCY)
STATUS_CACHE: dict[str, tuple[float, dict]] = {}
HTTP_SESSION: aiohttp.ClientSession | None = None
POWER_LOCKS: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
CONSOLE_POLL_SECONDS = max(2.0, float(os.getenv("CONSOLE_POLL_SECONDS", "3")))
CONSOLE_MAX_EVENTS_PER_POLL = 25
CONSOLE_TAILERS: dict[str, ConsoleTailer] = {}
PLAYER_TRACKERS: dict[str, PlayerTracker] = {}
CONSOLE_RELAY_ACTIVE: set[str] = set()
CONSOLE_RELAY_PROBLEMS: dict[str, str] = {}
CONSOLE_RELAY_TARGETS: set[str] = set()
# Bots are never relayed into games, except these IDs (the soak test checker, see soak/README.md).
RELAY_BOT_IDS = {int(part) for part in re.findall(r"\d{1,20}", os.getenv("RELAY_BOT_IDS", ""))}
# auto: Wings websocket, polling while it is down; websocket: never poll; poll: never stream.
CONSOLE_MODE = os.getenv("CONSOLE_MODE", "auto").strip().lower()
if CONSOLE_MODE not in ("auto", "websocket", "poll"):
    logger.warning("Unknown CONSOLE_MODE %r; using auto", CONSOLE_MODE)
    CONSOLE_MODE = "auto"
CONSOLE_STREAMS: dict[str, ConsoleStream] = {}
CONSOLE_STREAM_BUDGETS: dict[str, list] = {}
WS_SESSION: aiohttp.ClientSession | None = None
PANEL_PUBLIC_URL = os.getenv("PANEL_PUBLIC_URL", "").rstrip("/")
DEFAULT_LOCALE = normalize_locale(os.getenv("DEFAULT_LOCALE", "en"))
PTERORELAY_AGENT_ID = os.getenv("PTERORELAY_AGENT_ID", "")
PTERORELAY_AGENT_SECRET = os.getenv("PTERORELAY_AGENT_SECRET", "")
AGENT_CLIENT = (
    AgentClient(PANEL_PUBLIC_URL, PTERORELAY_AGENT_ID, PTERORELAY_AGENT_SECRET)
    if PANEL_PUBLIC_URL and PTERORELAY_AGENT_ID and PTERORELAY_AGENT_SECRET else None
)
# Set by Wings when the agent runs as a Pterodactyl server; that server is never controlled.
SELF_SERVER_UUID = os.getenv("P_SERVER_UUID", "").strip().lower()
AGENT_BINDINGS: dict[str, list[dict]] = {}
AGENT_SERVER_UUIDS: dict[str, str] = {}
AGENT_SERVER_NAMES: dict[str, str] = {}
AGENT_GUILDS: dict[str, dict] = {}

# Local state of the agent: recorded uptime and the live status embeds it keeps updated.
# Everything else (servers, roles, channels) comes from the panel.
DEFAULT_CONFIG = {
    "status_messages": {},
    "uptime_data": {},
}

def load_config():
    path = Path(CONFIG_FILE)
    if not path.exists():
        save_config(DEFAULT_CONFIG.copy())
    try:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read the configuration: {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"The configuration must be a JSON object: {path}")
    # Older agents also stored servers, roles and channels here; those now come from the panel.
    return {key: config.get(key, default.copy()) for key, default in DEFAULT_CONFIG.items()}

def save_config(config):
    path = Path(CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


@asynccontextmanager
async def edit_config():
    """Serializes the whole read-modify-write cycle of the configuration."""
    async with CONFIG_LOCK:
        config = load_config()
        yield config
        save_config(config)

# ─── Panel API ──────────────────────────────────────────────────────────────
async def get_http_session() -> aiohttp.ClientSession:
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return HTTP_SESSION

def server_uuid(server_id: str) -> str:
    """The panel UUID of a linked server; unknown servers are refused before any request."""
    uuid = AGENT_SERVER_UUIDS.get(normalize_server_identifier(server_id))
    if uuid is None:
        raise PermissionError("This server is not linked to PteroRelay.")
    return uuid


async def get_server_status(server_id):
    return await AGENT_CLIENT.service_status(await get_http_session(), server_uuid(server_id))


async def send_power_action(server_id, action, actor: DiscordActor):
    await AGENT_CLIENT.power(await get_http_session(), server_uuid(server_id), action, actor)


async def send_console_command(server_id: str, command: str, actor: DiscordActor) -> None:
    command = command.strip()
    if not command or len(command) > 500 or "\n" in command or "\r" in command:
        raise ValueError("Command must contain 1–500 characters on one line.")
    await AGENT_CLIENT.command(await get_http_session(), server_uuid(server_id), command, actor)


def discord_actor(interaction: discord.Interaction) -> DiscordActor:
    if interaction.guild_id is None:
        raise PermissionError("This action is only available in a Discord server.")
    return DiscordActor(
        guild_id=str(interaction.guild_id),
        user_id=str(interaction.user.id),
        role_ids=tuple(str(role.id) for role in getattr(interaction.user, "roles", [])),
    )


SERVER_IDENTIFIER_RE = re.compile(r"^[0-9a-fA-F]{8}$")
SERVER_UUID_RE = re.compile(
    r"^(?P<identifier>[0-9a-fA-F]{8})-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def normalize_server_identifier(value: str) -> str:
    """Returns the Pterodactyl short identifier from an ID, UUID or panel URL."""
    candidate = value.strip().rstrip("/")
    if "/server/" in candidate:
        candidate = candidate.split("/server/", 1)[1].split("/", 1)[0]
    uuid_match = SERVER_UUID_RE.fullmatch(candidate)
    if uuid_match:
        return uuid_match.group("identifier").lower()
    if SERVER_IDENTIFIER_RE.fullmatch(candidate):
        return candidate.lower()
    raise ValueError(
        "The identifier must be an 8-character ID, a full UUID or a Pterodactyl server URL."
    )


# ─── Bot Setup ───────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True


class PteroBot(commands.Bot):
    async def close(self) -> None:
        global HTTP_SESSION
        if HTTP_SESSION and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()
        HTTP_SESSION = None
        await stop_console_streams()
        await super().close()


# The agent never reads cached messages; discord.py would otherwise keep the last 1000 in memory.
bot = PteroBot(command_prefix="!", intents=intents, max_messages=None)


CHAT_RELAY = ChatRelay(bot)


def binding_dialect(binding: dict) -> Dialect | None:
    return binding.get("dialect")


EVENT_FEATURES = {
    "chat": "chat_in", "join": "join_leave", "leave": "join_leave", "death": "death",
    "advancement": "advancements", "broadcast": "server_messages",
    "server_ready": "server_status", "server_stop": "server_status",
}


def feature_enabled(binding: dict | None, feature: str) -> bool:
    """Features are on unless switched off for the binding in the panel."""
    return binding is None or feature not in (binding.get("disabled_features") or [])


def chat_bindings(server_id: str) -> list[dict]:
    """Agent bindings of a server that relay chat into a configured channel."""
    return [
        binding for binding in AGENT_BINDINGS.get(server_id, [])
        if binding.get("chat_enabled") and binding.get("chat_channel_id") and binding_dialect(binding)
    ]


def chat_targets(guild_id: int, channel_id: int) -> list[tuple[str, dict, Dialect]]:
    """Servers whose chat is linked to a Discord channel: (server_id, binding, dialect)."""
    return [
        (server_id, binding, binding["dialect"])
        for server_id in AGENT_BINDINGS for binding in chat_bindings(server_id)
        if binding.get("discord_guild_id") == str(guild_id) and str(binding["chat_channel_id"]) == str(channel_id)
    ]


@bot.event
async def on_message(message: discord.Message):
    if message.webhook_id or not message.guild or (message.author.bot and message.author.id not in RELAY_BOT_IDS):
        return
    targets = chat_targets(message.guild.id, message.channel.id)
    if not targets:
        return
    text = discord_message_text(message)
    if not text:
        return
    roles = member_roles(message.author)
    role_ids = chat_role_ids(roles, message.guild.id)
    failed = False
    for server_id, binding, dialect in targets:
        mappings = binding.get("roles", [])
        if not can_chat(role_ids, mappings):
            logger.debug("Chat from %s to %s dropped: no chat role", message.author.id, server_id)
            continue
        if not feature_enabled(binding, "chat_out"):
            continue
        role, color = chat_identity(roles, mappings)
        if not feature_enabled(binding, "colors_out"):
            color = None
        commands = render_outgoing(dialect, ChatOut(message.author.display_name, text, role, color))
        if not commands:
            logger.debug("Chat to %s dropped: adapter %s cannot send chat", server_id, dialect.id)
            continue
        try:
            actor = DiscordActor(str(message.guild.id), str(message.author.id), tuple(sorted(role_ids)))
            await AGENT_CLIENT.chat(await get_http_session(), AGENT_SERVER_UUIDS[server_id], commands, actor)
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as exc:
            failed = True
            CHAT_OUT_PROBLEMS[server_id] = f"Discord to game chat failed: {type(exc).__name__}: {exc}"[:300]
            logger.warning("Chat relay to %s failed: %s", server_id, exc)
        else:
            CHAT_OUT_PROBLEMS.pop(server_id, None)
    if failed:
        try:
            await message.add_reaction("⚠️")
        except discord.HTTPException:
            pass


async def deliver_game_event(server_id: str, binding: dict, event) -> None:
    channel = bot.get_channel(int(binding["chat_channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        logger.warning("Game event for %s dropped: chat channel %s unavailable", server_id, binding["chat_channel_id"])
        return
    dialect: Dialect = binding["dialect"]
    guild = AGENT_GUILDS.get(str(binding.get("guild_id")), {})
    locale = guild.get("locale", DEFAULT_LOCALE)
    player = discord.utils.escape_markdown(event.player or "?")
    if event.kind == "chat":
        avatar = (
            dialect.avatar_url.replace("{player}", urllib.parse.quote(event.player, safe=""))
            if dialect.avatar_url and event.player else None
        )
        await CHAT_RELAY.post_chat(channel, event.player, event.message, rank=event.rank, avatar_url=avatar)
        return
    if event.kind in ("server_ready", "server_stop"):
        text = translate(f"chat.{event.kind}", locale, {"server": configured_servers().get(server_id, server_id)})
    elif event.kind == "broadcast":
        text = translate(
            "chat.broadcast_player" if event.player else "chat.broadcast", locale,
            {"player": player, "message": discord.utils.escape_markdown(event.message or "")},
        )
    elif event.kind == "advancement":
        text = translate("chat.advancement", locale, {"player": player, "message": discord.utils.escape_markdown(event.message or "")})
    elif event.kind == "death":
        message = discord.utils.escape_markdown(event.message or "")
        text = translate("chat.death" if message else "chat.death_plain", locale, {"player": player, "message": message})
    else:
        text = translate(f"chat.{event.kind}", locale, {"player": player})
    await CHAT_RELAY.post_event(channel, text, event_color(event.kind, binding.get("event_colors")))


async def relay_console_lines(
    server_id: str, bindings: list[dict], lines: list[str], limit: int = CONSOLE_MAX_EVENTS_PER_POLL
) -> tuple[int, bool]:
    """Parse console lines, polled or streamed, and deliver their events.

    Returns how many events were delivered and whether events above ``limit`` were skipped.
    """
    tracker = PLAYER_TRACKERS.setdefault(server_id, PlayerTracker())
    delivered = 0
    skipped = False
    for line in lines:
        for index, binding in enumerate(bindings):
            event = parse_line(binding["dialect"], line)
            logger.debug("Console %s line %r -> %s", server_id, line[:300], event.kind if event else "no match")
            if event is None:
                continue
            if index == 0:
                tracker.apply(event)
            if not feature_enabled(binding, EVENT_FEATURES.get(event.kind, event.kind)):
                continue
            if delivered >= limit:
                skipped = True
                continue
            delivered += 1
            try:
                await deliver_game_event(server_id, binding, event)
            except Exception:
                logger.exception("Could not relay a %s event for %s", event.kind, server_id)
    return delivered, skipped


async def relay_server_console(
    server_id: str, bindings: list[dict], fetched: list[str] | Exception | None = None
) -> None:
    """Relay new polled console lines; ``fetched`` holds this poll's batch result when there is one."""
    cached = STATUS_CACHE.get(server_id)
    tracker = PLAYER_TRACKERS.setdefault(server_id, PlayerTracker())
    tailer = CONSOLE_TAILERS.setdefault(server_id, ConsoleTailer())
    if cached and cached[1].get("current_state") == "offline":
        tracker.reset()
        tailer.restart()
        CONSOLE_RELAY_ACTIVE.discard(server_id)
        return
    if isinstance(fetched, Exception):
        raise fetched
    lines = fetched if fetched is not None else await AGENT_CLIENT.logs(await get_http_session(), AGENT_SERVER_UUIDS[server_id])
    if server_id not in CONSOLE_RELAY_ACTIVE or server_id in CONSOLE_RELAY_PROBLEMS:
        logger.info("Console relay for %s is reading the console (%d lines)", server_id, len(lines))
        CONSOLE_RELAY_PROBLEMS.pop(server_id, None)
    CONSOLE_RELAY_ACTIVE.add(server_id)
    new_lines = tailer.feed(lines)
    if new_lines:
        logger.debug("Console relay %s: %d new line(s)", server_id, len(new_lines))
    _, skipped = await relay_console_lines(server_id, bindings, new_lines)
    if skipped:
        logger.warning("Console relay for %s skipped events above %s per poll", server_id, CONSOLE_MAX_EVENTS_PER_POLL)


def console_relay_targets() -> dict[str, list[dict]]:
    """Servers whose console is relayed, with the bindings that read it."""
    return {
        server_id: bindings for server_id in list(AGENT_BINDINGS)
        if (bindings := [b for b in chat_bindings(server_id) if b["dialect"].reads_console])
    }


async def get_ws_session() -> aiohttp.ClientSession:
    # Separate from the API session: its total timeout would cut every websocket after 15 s.
    global WS_SESSION
    if WS_SESSION is None or WS_SESSION.closed:
        WS_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
    return WS_SESSION


async def relay_streamed_lines(server_id: str, lines: list[str]) -> None:
    bindings = console_relay_targets().get(server_id)
    if not bindings:
        return
    # The event budget of one poll per poll interval, so a flooding console cannot flood Discord.
    now = time.monotonic()
    budget = CONSOLE_STREAM_BUDGETS.get(server_id)
    if budget is None or now - budget[0] >= CONSOLE_POLL_SECONDS:
        budget = CONSOLE_STREAM_BUDGETS[server_id] = [now, 0, False]
    delivered, skipped = await relay_console_lines(server_id, bindings, lines, CONSOLE_MAX_EVENTS_PER_POLL - budget[1])
    budget[1] += delivered
    if skipped and not budget[2]:
        budget[2] = True
        logger.warning(
            "Console relay for %s skipped events above %s per %g s", server_id, CONSOLE_MAX_EVENTS_PER_POLL, CONSOLE_POLL_SECONDS,
        )


def console_stream_changed(server_id: str, live: bool, reason: str | None) -> None:
    if live:
        CONSOLE_RELAY_ACTIVE.add(server_id)
        CONSOLE_RELAY_PROBLEMS.pop(server_id, None)
        # If the stream drops, polling starts from a fresh baseline instead of re-posting
        # what the stream already delivered.
        CONSOLE_TAILERS.pop(server_id, None)
        logger.info("Console relay for %s uses the Wings websocket", server_id)
        return
    CONSOLE_RELAY_ACTIVE.discard(server_id)
    if reason != "stopped":
        logger.info(
            "Console relay for %s lost the Wings websocket (%s); %s", server_id, reason,
            "polling the console until it reconnects" if CONSOLE_MODE == "auto" else "reconnecting",
        )


def make_console_stream(server_id: str) -> ConsoleStream:
    async def credentials() -> dict:
        return await AGENT_CLIENT.websocket(await get_http_session(), AGENT_SERVER_UUIDS[server_id])

    return ConsoleStream(
        server_id, credentials, lambda lines: relay_streamed_lines(server_id, lines),
        session=get_ws_session, origin=PANEL_PUBLIC_URL,
        on_live=lambda live, reason: console_stream_changed(server_id, live, reason),
    )


async def sync_console_streams(targets: dict[str, list[dict]]) -> None:
    """One Wings console websocket per relayed server."""
    for server_id in list(CONSOLE_STREAMS):
        if CONSOLE_MODE == "poll" or server_id not in targets or server_id not in AGENT_SERVER_UUIDS:
            CONSOLE_STREAM_BUDGETS.pop(server_id, None)
            await CONSOLE_STREAMS.pop(server_id).stop()
    if CONSOLE_MODE == "poll":
        return
    for server_id in targets:
        if server_id not in CONSOLE_STREAMS and server_id in AGENT_SERVER_UUIDS:
            stream = CONSOLE_STREAMS[server_id] = make_console_stream(server_id)
            stream.start()


async def stop_console_streams() -> None:
    global WS_SESSION
    streams = list(CONSOLE_STREAMS.values())
    CONSOLE_STREAMS.clear()
    await asyncio.gather(*(stream.stop() for stream in streams), return_exceptions=True)
    if WS_SESSION is not None and not WS_SESSION.closed:
        await WS_SESSION.close()
    WS_SESSION = None


def console_streamed(server_id: str) -> bool:
    """Whether the websocket covers the server, so polling must not read it as well."""
    if CONSOLE_MODE == "websocket":
        return True
    stream = CONSOLE_STREAMS.get(server_id)
    return stream is not None and stream.live


@tasks.loop(seconds=CONSOLE_POLL_SECONDS)
async def relay_console_events():
    """Keeps a console websocket per relayed server and polls the consoles no websocket covers."""
    targets = console_relay_targets()
    for server_id in set(CONSOLE_TAILERS) - set(targets):
        CONSOLE_TAILERS.pop(server_id, None)
        CONSOLE_RELAY_ACTIVE.discard(server_id)
    semaphore = asyncio.Semaphore(API_CONCURRENCY)

    global CONSOLE_RELAY_TARGETS
    if set(targets) != CONSOLE_RELAY_TARGETS:
        logger.info("Console relay servers: %s", ", ".join(sorted(targets)) or "none")
        enabled = {sid for sid in AGENT_BINDINGS if any(b.get("chat_enabled") for b in AGENT_BINDINGS[sid])}
        for server_id in sorted(enabled - set(targets)):
            logger.info(
                "Console relay for %s is off: chat is enabled but no chat channel or console-reading game is set",
                server_id,
            )
        CONSOLE_RELAY_TARGETS = set(targets)

    await sync_console_streams(targets)
    polled: dict[str, list[dict]] = {}
    for server_id, bindings in targets.items():
        if not console_streamed(server_id):
            polled[server_id] = bindings
            continue
        CONSOLE_TAILERS.pop(server_id, None)
        if (STATUS_CACHE.get(server_id) or (0, {}))[1].get("current_state") == "offline":
            PLAYER_TRACKERS.setdefault(server_id, PlayerTracker()).reset()

    fetched = await fetch_console_batches(list(polled))

    async def run(server_id: str, bindings: list[dict]) -> None:
        async with semaphore:
            try:
                await relay_server_console(server_id, bindings, fetched.get(server_id))
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ConsoleUnavailable) as exc:
                CONSOLE_RELAY_ACTIVE.discard(server_id)
                problem = f"{type(exc).__name__}: {exc}"
                if CONSOLE_RELAY_PROBLEMS.get(server_id) != problem:
                    logger.warning("Console relay for %s failed: %s", server_id, problem)
                CONSOLE_RELAY_PROBLEMS[server_id] = problem
            except Exception:
                # An escaping exception would stop the task loop for every server.
                CONSOLE_RELAY_ACTIVE.discard(server_id)
                logger.exception("Console relay for %s crashed", server_id)

    await asyncio.gather(*(run(server_id, bindings) for server_id, bindings in polled.items()))


CONSOLE_BATCH_SIZE = 25
CONSOLE_BATCH_SUPPORTED = True


async def fetch_console_batches(server_ids: list[str]) -> dict[str, list[str] | Exception]:
    """Read all running relayed consoles in as few requests as possible.

    Servers missing from the result (offline, or an older panel without the batch
    endpoint) are read one by one by relay_server_console.
    """
    global CONSOLE_BATCH_SUPPORTED
    wanted = [
        server_id for server_id in server_ids
        if server_id in AGENT_SERVER_UUIDS
        and (STATUS_CACHE.get(server_id) or (0, {}))[1].get("current_state") != "offline"
    ]
    results: dict[str, list[str] | Exception] = {}
    if not CONSOLE_BATCH_SUPPORTED or not wanted:
        return results
    session = await get_http_session()
    for start in range(0, len(wanted), CONSOLE_BATCH_SIZE):
        chunk = wanted[start:start + CONSOLE_BATCH_SIZE]
        try:
            by_uuid = await AGENT_CLIENT.logs_batch(session, [AGENT_SERVER_UUIDS[sid] for sid in chunk])
        except aiohttp.ClientResponseError as exc:
            if exc.status in (404, 405):
                CONSOLE_BATCH_SUPPORTED = False  # older panel: fall back to one request per server
                logger.info("The panel has no batched console endpoint; reading consoles one by one")
                return {}
            results.update({sid: exc for sid in chunk})
            continue
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            results.update({sid: exc for sid in chunk})
            continue
        results.update({sid: by_uuid[AGENT_SERVER_UUIDS[sid]] for sid in chunk})
    return results


def tracked_players(server_id: str) -> PlayerTracker | None:
    """Players seen in the console, when a join/leave capable adapter is active."""
    if not any("join_leave" in b["dialect"].capabilities for b in chat_bindings(server_id)):
        return None
    return PLAYER_TRACKERS.get(server_id)

# ─── Helpers ─────────────────────────────────────────────────────────────────
def status_emoji(state):
    return {"running": "🟢", "offline": "🔴", "starting": "🟡", "stopping": "🟠"}.get(state, "⚪")

def status_color(state):
    return {"running": discord.Color.green(), "offline": discord.Color.red()}.get(state, discord.Color.orange())

def simple_embed(message, color=discord.Color.blue()):
    return discord.Embed(description=message, color=color).set_footer(text="Pterodactyl Bot")


def interaction_locale(interaction: discord.Interaction) -> str:
    return normalize_locale(getattr(interaction, "locale", None), DEFAULT_LOCALE)


def tr(interaction: discord.Interaction, key: str, **values: object) -> str:
    return translate(key, interaction_locale(interaction), values)


def configured_servers() -> dict[str, str]:
    """Linked servers (identifier -> name), from the panel."""
    return dict(AGENT_SERVER_NAMES)


def has_server_access(interaction: discord.Interaction, server_id: str) -> bool:
    """A server is visible in a Discord server it is linked to, to roles with View."""
    guild_id = str(interaction.guild_id or "")
    user_roles = {str(role.id) for role in getattr(interaction.user, "roles", [])}
    return any(
        binding.get("discord_guild_id") == guild_id
        and any(
            str(role.get("discord_role_id")) in user_roles and bool(role.get("can_view"))
            for role in binding.get("roles", [])
        )
        for binding in AGENT_BINDINGS.get(server_id, [])
    )


async def server_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=name, value=sid)
        for sid, name in configured_servers().items()
        if current.lower() in name.lower() and has_server_access(interaction, sid)
    ][:25]

def error_msg(e: aiohttp.ClientResponseError, locale: str = DEFAULT_LOCALE):
    if e.status == 404:
        return translate("server.not_found", locale)
    if e.status == 401:
        return translate("error.unauthorized", locale)
    if e.status == 403:
        return translate("error.forbidden", locale)
    if e.status == 429:
        return translate("error.rate_limited", locale)
    return str(e.message)

# ─── Uptime helpers ───────────────────────────────────────────────────────────
def format_uptime(seconds: float) -> str:
    if seconds <= 0:
        return "0m"
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) or "< 1m"

def update_uptime(config, server_id: str, new_state: str):
    now = datetime.now(timezone.utc).timestamp()
    data = config["uptime_data"].setdefault(server_id, {
        "last_start": None, "total_seconds": 0, "state": "offline"
    })
    old_state = data.get("state", "offline")
    if new_state == "running" and old_state != "running":
        data["last_start"] = now
    if new_state == "offline" and old_state == "running":
        if data.get("last_start"):
            data["total_seconds"] += now - data["last_start"]
            data["last_start"] = None
    data["state"] = new_state

def get_current_uptime(config, server_id: str) -> float:
    now = datetime.now(timezone.utc).timestamp()
    data = config["uptime_data"].get(server_id, {})
    total = data.get("total_seconds", 0)
    if data.get("state") == "running" and data.get("last_start"):
        total += now - data["last_start"]
    return total

# ─── Live Status Embed builder ────────────────────────────────────────────────
async def build_status_embed(
    server_id: str, name: str, config: dict, status_data: dict | None = None,
    locale: str = DEFAULT_LOCALE,
) -> discord.Embed:
    try:
        status = status_data or await get_server_status(server_id)
        state = status["current_state"]
        res = status["resources"]
        uptime_secs = get_current_uptime(config, server_id)

        embed = discord.Embed(title=f"{status_emoji(state)} {name}", color=status_color(state))
        embed.add_field(name=f"📊 {translate('status.state', locale)}", value=state.upper(), inline=True)
        embed.add_field(name="💻 CPU", value=f"{res['cpu_absolute']:.1f}%", inline=True)
        embed.add_field(name="🧠 RAM", value=f"{res['memory_bytes'] / 1024 / 1024:.0f} MB", inline=True)
        embed.add_field(name="💾 Disk", value=f"{res['disk_bytes'] / 1024 / 1024:.0f} MB", inline=True)
        rx = res['network_rx_bytes'] / 1024
        tx = res['network_tx_bytes'] / 1024
        embed.add_field(name=f"🌐 {translate('status.network', locale)}", value=f"{rx:.0f} / {tx:.0f} KB", inline=True)
        embed.add_field(name=f"⏱️ {translate('status.uptime', locale)}", value=format_uptime(uptime_secs), inline=True)
        relays = chat_bindings(server_id)
        chat_channel = relays[0]["chat_channel_id"] if relays else None
        relay_active = server_id in CONSOLE_RELAY_ACTIVE
        relay_state = translate("status.connected" if relay_active else "status.disconnected", locale)
        embed.add_field(
            name=f"💬 {translate('status.chat_bridge', locale)}",
            value=f"{relay_state}\n{f'<#{chat_channel}>' if chat_channel else translate('status.channel_missing', locale)}",
            inline=True,
        )

        tracker = tracked_players(server_id)
        if tracker is not None and state == "running":
            names = tracker.names
            count = str(len(names)) if tracker.known else translate("status.players_partial", locale, {"count": len(names)})
            player_list = ", ".join(names) or "—"
            embed.add_field(
                name=f"👥 {translate('status.players', locale)} ({count})",
                value=player_list if len(player_list) <= 1024 else player_list[:1020] + "...",
                inline=False,
            )

        embed.set_footer(text=translate("status.updated", locale))
        embed.timestamp = datetime.now(timezone.utc)
        return embed
    except aiohttp.ClientResponseError as exc:
        logger.exception("Could not build the status for server %s", server_id)
        embed = discord.Embed(
            title=f"⚠️ {name}",
            description=translate("status.error", locale, {"message": error_msg(exc, locale)}),
            color=discord.Color.dark_gray(),
        )
        embed.timestamp = datetime.now(timezone.utc)
        return embed
    except Exception:
        logger.exception("Could not build the status for server %s", server_id)
        embed = discord.Embed(title=f"⚠️ {name}", description=translate("server.unreachable", locale), color=discord.Color.dark_gray())
        embed.timestamp = datetime.now(timezone.utc)
        return embed

# ─── Background tasks ────────────────────────────────────────────────────────
async def fetch_and_cache_status(server_id: str) -> tuple[str, dict | Exception]:
    try:
        status = await get_server_status(server_id)
        STATUS_CACHE[server_id] = (datetime.now(timezone.utc).timestamp(), status)
        return server_id, status
    except Exception as exc:
        return server_id, exc


@tasks.loop(seconds=30)
async def poll_server_statuses():
    """Updates the state and uptime of all servers, even without a live embed."""
    server_ids = list(configured_servers())
    if not server_ids:
        return
    results = await asyncio.gather(*(fetch_and_cache_status(sid) for sid in server_ids))
    transitions: list[tuple[str, str, str]] = []
    async with CONFIG_LOCK:
        config = load_config()
        changed = False
        for server_id, result in results:
            if isinstance(result, Exception):
                logger.warning("Polling server %s failed: %s", server_id, result)
                continue
            if server_id not in configured_servers():
                continue
            new_state = result["current_state"]
            had_state = server_id in config["uptime_data"]
            old_state = config["uptime_data"].get(server_id, {}).get("state", "offline")
            if old_state != new_state:
                update_uptime(config, server_id, new_state)
                changed = True
                if had_state:
                    transitions.append((server_id, old_state, new_state))
        if changed:
            save_config(config)
    await asyncio.gather(*(notify_state_transition(*transition) for transition in transitions))


async def notify_state_transition(server_id: str, old_state: str, new_state: str) -> None:
    name = configured_servers().get(server_id, server_id)
    for binding in AGENT_BINDINGS.get(server_id, []):
        guild = AGENT_GUILDS.get(str(binding.get("guild_id"))) or {}
        # The panel resolves the server's own channel and falls back to the Discord server's;
        # panels from before per-server channels do not send the field at all.
        if "notification_channel_id" in binding:
            channel_id = binding["notification_channel_id"]
        else:
            channel_id = guild.get("notification_channel_id")
        channel = bot.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    translate(
                        "notification.state_changed",
                        guild.get("locale", DEFAULT_LOCALE),
                        {"server": name, "old": old_state.upper(), "new": new_state.upper()},
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.exception("Could not deliver state notification for %s", server_id)


@tasks.loop(seconds=30)
async def update_status_messages():
    config = load_config()
    missing: list[str] = []

    async def update_one(server_id: str, msg_info: dict) -> None:
        try:
            channel = bot.get_channel(int(msg_info["channel_id"]))
            if not channel:
                return
            message = await channel.fetch_message(int(msg_info["message_id"]))
            name = configured_servers().get(server_id, server_id)
            cached = STATUS_CACHE.get(server_id)
            fresh = cached and datetime.now(timezone.utc).timestamp() - cached[0] < 90
            status = cached[1] if fresh else None
            embed = await build_status_embed(server_id, name, config, status)
            await message.edit(embed=embed)
        except discord.NotFound:
            missing.append(server_id)
        except Exception:
            logger.exception("Could not update the live status for server %s", server_id)

    # Servers unlinked since the embed was created keep their last state until removed.
    targets = [(server_id, info) for server_id, info in config["status_messages"].items() if server_id in AGENT_SERVER_UUIDS]
    await asyncio.gather(*(update_one(server_id, msg_info) for server_id, msg_info in targets))
    if missing:
        async with CONFIG_LOCK:
            latest = load_config()
            for server_id in missing:
                latest["status_messages"].pop(server_id, None)
            save_config(latest)

async def execute_power_and_wait(
    server_id: str, action: str, actor: DiscordActor, timeout: int = 60
) -> tuple[bool, str]:
    """Sends a power signal and waits for the server to reach its final state."""
    async with POWER_LOCKS[server_id]:
        before = (await get_server_status(server_id))["current_state"]
        if (action == "start" and before == "running") or (action == "stop" and before == "offline"):
            return True, before
        await send_power_action(server_id, action, actor)
        target = "offline" if action == "stop" else "running"
        transition_seen = action != "restart"
        deadline = asyncio.get_running_loop().time() + timeout
        last_state = before
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            status = await get_server_status(server_id)
            last_state = status["current_state"]
            STATUS_CACHE[server_id] = (datetime.now(timezone.utc).timestamp(), status)
            if action == "restart" and last_state != "running":
                transition_seen = True
            if last_state == target and transition_seen:
                return True, last_state
        return False, last_state


# ─── Confirmation View ────────────────────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, action: str, server_id: str, name: str, requester_id: int, locale: str = DEFAULT_LOCALE):
        super().__init__(timeout=30)
        self.action = action
        self.server_id = server_id
        self.name = name
        self.requester_id = requester_id
        self.locale = normalize_locale(locale, DEFAULT_LOCALE)
        self.confirm.label = translate("common.confirm", self.locale)
        self.cancel.label = translate("common.cancel", self.locale)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id or not has_server_access(interaction, self.server_id):
            await interaction.response.send_message(
                "❌ Only the user who ran the command and has access can use this button.",
                ephemeral=True,
            )
            return False
        return True

    async def _do_action(self, interaction: discord.Interaction, confirmed: bool):
        self.clear_items()
        if not confirmed:
            await interaction.response.edit_message(
                embed=simple_embed(f"❎ {translate('common.cancelled', self.locale)}", discord.Color.greyple()), view=self
            )
            return
        await interaction.response.edit_message(
            embed=simple_embed(f"⏳ {translate('power.executing', self.locale, {'action': self.action, 'name': self.name})}", discord.Color.blurple()), view=self
        )
        try:
            success, state = await execute_power_and_wait(
                self.server_id, self.action, actor=discord_actor(interaction)
            )
            if success:
                message = f"✅ {translate('power.finished', self.locale, {'name': self.name, 'state': state.upper()})}"
                color = discord.Color.green()
            else:
                message = f"⚠️ {translate('power.timeout', self.locale, {'state': state.upper()})}"
                color = discord.Color.yellow()
            await interaction.edit_original_response(
                embed=simple_embed(message, color), view=self
            )
        except aiohttp.ClientResponseError as e:
            await interaction.edit_original_response(
                embed=simple_embed(f"❌ {error_msg(e, self.locale)}", discord.Color.red())
            )

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_action(interaction, True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_action(interaction, False)

    async def on_timeout(self):
        self.clear_items()


class ManageServerSelect(discord.ui.Select):
    def __init__(self, servers: list[tuple[str, str]], selected: str, locale: str = DEFAULT_LOCALE):
        options = [
            discord.SelectOption(label=name[:100], value=sid, default=sid == selected)
            for sid, name in servers[:25]
        ]
        super().__init__(placeholder=translate("common.select_server", locale), options=options)

    async def callback(self, interaction: discord.Interaction):
        view: ServerManageView = self.view  # type: ignore[assignment]
        view.server_id = self.values[0]
        view.refresh_select()
        await interaction.response.defer()
        await view.render(interaction)


class ServerManageView(discord.ui.View):
    def __init__(self, requester_id: int, servers: list[tuple[str, str]], locale: str = DEFAULT_LOCALE):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.locale = normalize_locale(locale, DEFAULT_LOCALE)
        labels = {"refresh_button": "button.refresh", "settings_button": "button.settings"}
        for name, key in labels.items():
            getattr(self, name).label = translate(key, self.locale)
        self.servers = servers[:25]
        self.server_id: str | None = self.servers[0][0] if self.servers else None
        self.refresh_select()

    def refresh_select(self) -> None:
        for child in list(self.children):
            if isinstance(child, ManageServerSelect):
                self.remove_item(child)
        if self.servers and self.server_id:
            self.add_item(ManageServerSelect(self.servers, self.server_id, self.locale))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(f"❌ {translate('manage.not_owner', self.locale)}", ephemeral=True)
            return False
        if self.server_id is None:
            return False
        if not has_server_access(interaction, self.server_id):
            await interaction.response.send_message(f"❌ {translate('access.denied', self.locale)}", ephemeral=True)
            return False
        return True

    async def render(self, interaction: discord.Interaction) -> None:
        config = load_config()
        if self.server_id is None or self.server_id not in configured_servers():
            embed = discord.Embed(
                title=f"🖥️ {translate('manage.title', self.locale)}",
                description=translate("manage.empty", self.locale),
                color=discord.Color.blue(),
            )
            return await interaction.edit_original_response(embed=embed, view=self)
        name = configured_servers().get(self.server_id, self.server_id)
        embed = await build_status_embed(self.server_id, name, config, locale=self.locale)
        await interaction.edit_original_response(embed=embed, view=self)

    async def run_power(self, interaction: discord.Interaction, action: str) -> None:
        if self.server_id is None:
            return await interaction.response.send_message(f"❌ {translate('manage.add_first', self.locale)}", ephemeral=True)
        await interaction.response.defer()
        try:
            success, state = await execute_power_and_wait(
                self.server_id, action, actor=discord_actor(interaction)
            )
            name = configured_servers().get(self.server_id, self.server_id)
            embed = await build_status_embed(self.server_id, name, load_config(), locale=self.locale)
            embed.description = (
                f"✅ {translate('manage.action_done', self.locale, {'action': action})}"
                if success else f"⚠️ {translate('manage.action_timeout', self.locale, {'state': state.upper()})}"
            )
            await interaction.edit_original_response(embed=embed, view=self)
        except aiohttp.ClientResponseError as exc:
            await interaction.edit_original_response(embed=simple_embed(f"❌ {error_msg(exc, self.locale)}", discord.Color.red()), view=self)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Power action %s for %s failed: %s", action, self.server_id, exc)
            await interaction.edit_original_response(
                embed=simple_embed(f"❌ {translate('error.panel', self.locale)}", discord.Color.red()), view=self
            )

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.success, row=1)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_power(interaction, "start")

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_power(interaction, "stop")

    @discord.ui.button(label="Restart", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_power(interaction, "restart")

    @discord.ui.button(label="Refresh", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.render(interaction)

    @discord.ui.button(label="Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.server_id is None:
            return await interaction.response.send_message(
                f"❌ {translate('manage.add_first', self.locale)}", ephemeral=True
            )
        if not PANEL_PUBLIC_URL:
            return await interaction.response.send_message(
                translate("settings.unavailable", self.locale), ephemeral=True
            )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label=translate("settings.open", self.locale),
            url=f"{PANEL_PUBLIC_URL}/server/{self.server_id}/discord",
        ))
        await interaction.response.send_message(
            translate("settings.description", self.locale), view=view, ephemeral=True
        )


server_group = app_commands.Group(name="server", description="Manage Pterodactyl servers")


@server_group.command(name="manage", description="Open the interactive server control panel")
async def server_manage(interaction: discord.Interaction):
    servers = [(sid, name) for sid, name in configured_servers().items() if has_server_access(interaction, sid)]
    if not servers:
        return await interaction.response.send_message(f"❌ {tr(interaction, 'manage.empty')}", ephemeral=True)
    locale = interaction_locale(interaction)
    view = ServerManageView(interaction.user.id, servers, locale)
    await interaction.response.defer(ephemeral=True)
    embed = await build_status_embed(view.server_id, servers[0][1], load_config(), locale=locale)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


bot.tree.add_command(server_group)

# ─── Slash Commands ───────────────────────────────────────────────────────────

@bot.tree.command(name="start", description="Start a Pterodactyl server")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
async def start(interaction: discord.Interaction, server_id: str):
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    await interaction.response.defer()
    name = configured_servers().get(server_id, server_id)
    try:
        status = await get_server_status(server_id)
        if status["current_state"] == "running":
            return await interaction.followup.send(embed=simple_embed(f"🟢 {tr(interaction, 'power.running', name=name)}", discord.Color.yellow()))
        success, state = await execute_power_and_wait(
            server_id, "start", actor=discord_actor(interaction)
        )
        if success:
            await interaction.followup.send(embed=simple_embed(f"✅ {tr(interaction, 'power.started', name=name)}", discord.Color.green()))
        else:
            await interaction.followup.send(embed=simple_embed(
                f"⚠️ {tr(interaction, 'power.timeout', state=state.upper())}", discord.Color.yellow()
            ))
    except aiohttp.ClientResponseError as e:
        await interaction.followup.send(embed=simple_embed(f"❌ {error_msg(e, interaction_locale(interaction))}", discord.Color.red()))


@bot.tree.command(name="stop", description="Stop a Pterodactyl server")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
async def stop(interaction: discord.Interaction, server_id: str):
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    name = configured_servers().get(server_id, server_id)
    try:
        status = await get_server_status(server_id)
        if status["current_state"] == "offline":
            return await interaction.response.send_message(embed=simple_embed(f"🔴 {tr(interaction, 'power.offline', name=name)}", discord.Color.yellow()))
    except aiohttp.ClientResponseError as e:
        return await interaction.response.send_message(embed=simple_embed(f"❌ {error_msg(e, interaction_locale(interaction))}", discord.Color.red()))
    embed = discord.Embed(title=f"🛑 {tr(interaction, 'power.confirm_stop_title')}", description=tr(interaction, "power.confirm_stop", name=name), color=discord.Color.orange())
    await interaction.response.send_message(
        embed=embed, view=ConfirmView("stop", server_id, name, interaction.user.id, interaction_locale(interaction))
    )


@bot.tree.command(name="restart", description="Restart a Pterodactyl server")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
async def restart(interaction: discord.Interaction, server_id: str):
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    name = configured_servers().get(server_id, server_id)
    embed = discord.Embed(title=f"🔄 {tr(interaction, 'power.confirm_restart_title')}", description=tr(interaction, "power.confirm_restart", name=name), color=discord.Color.blue())
    await interaction.response.send_message(
        embed=embed, view=ConfirmView("restart", server_id, name, interaction.user.id, interaction_locale(interaction))
    )


def status_overview_lines(entries: list[tuple[str, str | None, int | None, bool]], locale: str) -> list[str]:
    """One line per server: state, name, players and whether chat is relayed."""
    lines = []
    for name, state, players, relayed in entries:
        parts = [f"{status_emoji(state)} **{discord.utils.escape_markdown(name)}**",
                 translate(f"status.state_{state}", locale) if state in ("running", "offline", "starting", "stopping")
                 else translate("status.state_unknown", locale)]
        if players is not None and state == "running":
            parts.append(f"👥 {players}")
        if relayed:
            parts.append(f"💬 {translate('status.chat_bridge', locale)}")
        lines.append(" · ".join(parts))
    return lines


@bot.tree.command(name="status", description="Show server status, or all servers without a choice")
@app_commands.describe(server_id="Select a server (leave empty to list all)")
@app_commands.autocomplete(server_id=server_autocomplete)
async def status_cmd(interaction: discord.Interaction, server_id: str | None = None):
    if server_id is None:
        locale = interaction_locale(interaction)
        servers = sorted(
            ((sid, name) for sid, name in configured_servers().items() if has_server_access(interaction, sid)),
            key=lambda item: item[1].lower(),
        )
        if not servers:
            return await interaction.response.send_message(
                embed=simple_embed(f"ℹ️ {tr(interaction, 'status.overview_empty')}", discord.Color.yellow()), ephemeral=True)
        lines = status_overview_lines(
            [(name, cached_state(sid), player_count(sid), sid in CONSOLE_RELAY_ACTIVE) for sid, name in servers], locale)
        description = ""
        for line in lines:
            if len(description) + len(line) + 1 > 3900:
                description += "\n…"
                break
            description += line + "\n"
        embed = discord.Embed(title=f"🖥️ {tr(interaction, 'status.overview_title')}", description=description.strip(),
                              color=discord.Color.blurple())
        return await interaction.response.send_message(embed=embed)
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    await interaction.response.defer()
    name = configured_servers().get(server_id, server_id)
    embed = await build_status_embed(server_id, name, load_config(), locale=interaction_locale(interaction))
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="uptime", description="Show the recorded server uptime")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
async def uptime_cmd(interaction: discord.Interaction, server_id: str):
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    await interaction.response.defer()
    name = configured_servers().get(server_id, server_id)
    config = load_config()
    total = get_current_uptime(config, server_id)
    state = config["uptime_data"].get(server_id, {}).get("state", "unknown")
    last_start = config["uptime_data"].get(server_id, {}).get("last_start")

    embed = discord.Embed(title=f"⏱️ {tr(interaction, 'uptime.title', name=name)}", color=discord.Color.teal())
    embed.add_field(name=f"📊 {tr(interaction, 'uptime.current')}", value=f"{status_emoji(state)} {state.upper()}", inline=True)
    embed.add_field(name=f"⏱️ {tr(interaction, 'status.uptime')}", value=format_uptime(total), inline=True)
    if last_start:
        dt = datetime.fromtimestamp(last_start, tz=timezone.utc)
        embed.add_field(name=f"🕐 {tr(interaction, 'uptime.last_start')}", value=discord.utils.format_dt(dt, style="R"), inline=True)
    embed.set_footer(text="Pterodactyl Bot")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="console", description="Send an authorized command to the game server console")
@app_commands.describe(server_id="Select a server", command="A single console command")
@app_commands.autocomplete(server_id=server_autocomplete)
async def console_cmd(interaction: discord.Interaction, server_id: str, command: str):
    # The panel checks the Console permission of the member's roles.
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(
            embed=simple_embed(tr(interaction, "access.denied"), discord.Color.red()), ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    try:
        await send_console_command(server_id, command, discord_actor(interaction))
        await interaction.followup.send(
            embed=simple_embed("✅ Command accepted by the server.", discord.Color.green()), ephemeral=True
        )
    except ValueError as exc:
        await interaction.followup.send(embed=simple_embed(f"❌ {exc}", discord.Color.red()), ephemeral=True)
    except (aiohttp.ClientError, asyncio.TimeoutError, PermissionError) as exc:
        logger.warning("Console command failed for %s: %s", server_id, exc)
        await interaction.followup.send(
            embed=simple_embed("❌ The console command was rejected.", discord.Color.red()), ephemeral=True
        )


@bot.tree.command(name="settings", description="Open PteroRelay settings in the Pterodactyl panel")
async def settings_cmd(interaction: discord.Interaction):
    """Return an authenticated panel link; the URL itself never grants access."""
    if not PANEL_PUBLIC_URL:
        return await interaction.response.send_message(
            tr(interaction, "settings.unavailable"), ephemeral=True
        )
    view = discord.ui.View(timeout=60)
    view.add_item(discord.ui.Button(
        label=tr(interaction, "settings.open"),
        url=f"{PANEL_PUBLIC_URL}/account/pterorelay",
        emoji="⚙️",
    ))
    embed = discord.Embed(
        title=tr(interaction, "settings.title"),
        description=tr(interaction, "settings.description"),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="link", description="Create a code to link a Pterodactyl server to this Discord server")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def link_cmd(interaction: discord.Interaction):
    """Only members with Manage Server may let a panel server join this Discord server."""
    permissions = getattr(interaction.user, "guild_permissions", None)
    if interaction.guild_id is None or not (permissions and permissions.manage_guild):
        return await interaction.response.send_message(
            "❌ Only members with the Manage Server permission can create link codes.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    try:
        data = await AGENT_CLIENT.link_code(await get_http_session(), str(interaction.guild_id), str(interaction.user.id))
    except aiohttp.ClientResponseError as exc:
        message = "The panel does not know this Discord server yet; try again in a minute." if exc.status == 404 else error_msg(exc)
        return await interaction.followup.send(f"❌ {message}", ephemeral=True)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return await interaction.followup.send("❌ The panel is unreachable.", ephemeral=True)
    await interaction.followup.send(
        f"🔗 Link code: **`{data['code']}`** (valid for 15 minutes, single use).\n"
        "Give it to the server owner: in the panel, open the server → **Discord** → **Link with a code**.",
        ephemeral=True,
    )


@bot.tree.command(name="setstatusmsg", description="Create a live status embed for a server in this channel")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def setstatusmsg(interaction: discord.Interaction, server_id: str):
    # Default permissions can be changed by the Discord server; the server itself must be visible here.
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    await interaction.response.defer()
    config = load_config()
    name = configured_servers().get(server_id, server_id)
    embed = await build_status_embed(server_id, name, config, locale=interaction_locale(interaction))
    msg = await interaction.channel.send(embed=embed)
    async with edit_config() as latest:
        latest["status_messages"][server_id] = {
            "channel_id": str(interaction.channel_id),
            "message_id": str(msg.id),
        }
    await interaction.followup.send(
        embed=simple_embed(f"✅ Live status for **{name}** is set! It updates every 30s.", discord.Color.green()),
        ephemeral=True
    )


@bot.tree.command(name="removestatusmsg", description="Remove the live status embed for a server")
@app_commands.describe(server_id="Select a server")
@app_commands.autocomplete(server_id=server_autocomplete)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def removestatusmsg(interaction: discord.Interaction, server_id: str):
    if not has_server_access(interaction, server_id):
        return await interaction.response.send_message(embed=simple_embed(f"❌ {tr(interaction, 'access.denied')}", discord.Color.red()), ephemeral=True)
    await interaction.response.defer()
    config = load_config()
    name = configured_servers().get(server_id, server_id)
    if server_id not in config["status_messages"]:
        return await interaction.followup.send(embed=simple_embed(f"ℹ️ **{name}** has no live status embed.", discord.Color.yellow()))
    async with edit_config() as latest:
        info = latest["status_messages"].pop(server_id, None)
    if not info:
        return await interaction.followup.send(embed=simple_embed(f"ℹ️ **{name}** has no live status embed.", discord.Color.yellow()))
    try:
        ch = bot.get_channel(int(info["channel_id"]))
        if ch:
            msg = await ch.fetch_message(int(info["message_id"]))
            await msg.delete()
    except Exception:
        pass
    await interaction.followup.send(embed=simple_embed(f"✅ Live status for **{name}** removed.", discord.Color.orange()))


# ─── Bot presence update ──────────────────────────────────────────────────────
_commands_synced = False
STATUS_FRESH_SECONDS = 90


def cached_state(server_id: str) -> str | None:
    """The last polled state, or None when it is unknown or stale."""
    cached = STATUS_CACHE.get(server_id)
    if not cached or datetime.now(timezone.utc).timestamp() - cached[0] > STATUS_FRESH_SECONDS:
        return None
    return cached[1].get("current_state")


def player_count(server_id: str) -> int | None:
    tracker = tracked_players(server_id)
    return len(tracker.names) if tracker is not None and tracker.known else None


def presence_text(server_ids: list[str], states: dict[str, str | None], players: dict[str, int | None]) -> str:
    """One line for every Discord server the bot is in, so it names no servers: other
    customers of the same panel would see them."""
    if not server_ids:
        return "🔧 no servers"
    online = sum(1 for sid in server_ids if states.get(sid) == "running")
    text = f"{len(server_ids)} server{'s' if len(server_ids) != 1 else ''} · {online} online"
    counts = [players[sid] for sid in server_ids if states.get(sid) == "running" and players.get(sid) is not None]
    if counts:
        total = sum(counts)
        text += f" · {total} player{'s' if total != 1 else ''}"
    return text


@tasks.loop(seconds=60)
async def update_bot_presence():
    server_ids = list(configured_servers())
    states = {sid: cached_state(sid) for sid in server_ids}
    text = presence_text(server_ids, states, {sid: player_count(sid) for sid in server_ids})
    healthy = server_ids and all(state is not None for state in states.values())
    await bot.change_presence(
        status=discord.Status.online if healthy else discord.Status.idle,
        activity=discord.Activity(type=discord.ActivityType.watching, name=text),
    )


@tasks.loop(seconds=30)
async def update_health_marker():
    if bot.is_ready():
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEALTH_FILE.touch()


HEARTBEAT_FAILURES = 0
# Latest Discord → game failure per server, reported to the panel until a message gets through.
CHAT_OUT_PROBLEMS: dict[str, str] = {}
PROBLEM_SINCE: dict[tuple[str, str, str], str] = {}


def bot_info() -> dict:
    return {
        "id": str(bot.user.id) if bot.user else None,
        "name": str(bot.user)[:100] if bot.user else None,
        "application_id": str(bot.application_id) if bot.application_id else None,
        # Login fails without the privileged intent, so a running bot has it.
        "message_content": bool(bot.intents.message_content),
        "guild_count": len(bot.guilds),
        "latency_ms": int(bot.latency * 1000) if bot.latency == bot.latency and bot.latency != float("inf") else None,
    }


def collect_diagnostics() -> dict:
    """Problems the panel shows in Admin → PteroRelay, so nobody has to read the agent log."""
    problems: list[dict] = []

    def add(kind: str, server_id: str | None, message: str, guild: str | None = None) -> None:
        key = (kind, server_id or "", message)
        since = PROBLEM_SINCE.setdefault(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        problems.append({
            "kind": kind, "server": AGENT_SERVER_UUIDS.get(server_id or "") or server_id,
            "guild": guild, "message": message[:300], "since": since,
        })

    for server_id, problem in CONSOLE_RELAY_PROBLEMS.items():
        add("console", server_id, f"Reading the console failed: {problem}")
    for server_id, problem in CHAT_OUT_PROBLEMS.items():
        add("chat_out", server_id, problem)
    for server_id, bindings in AGENT_BINDINGS.items():
        for binding in bindings:
            if not binding.get("chat_enabled"):
                continue
            guild = binding.get("discord_guild_id")
            if not binding.get("chat_channel_id"):
                add("setup", server_id, "Chat is enabled but no chat channel or integration channel is set.", guild)
                continue
            if binding.get("dialect") is None:
                add("setup", server_id, "Chat is enabled but no game is selected.", guild)
            channel = bot.get_channel(int(binding["chat_channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                add("channel", server_id, f"The chat channel {binding['chat_channel_id']} does not exist or the bot cannot see it.", guild)
                continue
            permissions = channel.permissions_for(channel.guild.me)
            if not (permissions.view_channel and permissions.send_messages):
                add("channel", server_id, f"The bot cannot send messages in #{channel.name}.", guild)
            elif not permissions.manage_webhooks:
                add("channel", server_id, f"The bot lacks Manage Webhooks in #{channel.name}; game chat is posted by the bot instead of under player names.", guild)
    current = {(p["kind"], next((sid for sid, uuid in AGENT_SERVER_UUIDS.items() if uuid == p["server"]), p["server"] or ""), p["message"]) for p in problems}
    for key in list(PROBLEM_SINCE):
        if key not in current:
            PROBLEM_SINCE.pop(key, None)
    websocket = sum(1 for stream in CONSOLE_STREAMS.values() if stream.live)
    return {
        "problems": problems[:100], "relayed": len(CONSOLE_RELAY_TARGETS),
        "live": len(CONSOLE_RELAY_ACTIVE), "websocket": websocket,
    }


@tasks.loop(seconds=30)
async def sync_agent_heartbeat():
    global AGENT_BINDINGS, AGENT_SERVER_UUIDS, AGENT_SERVER_NAMES, AGENT_GUILDS, HEARTBEAT_FAILURES
    if not bot.is_ready():
        return
    session = await get_http_session()
    guilds = [{
        "id": str(guild.id),
        "name": guild.name,
        "roles": [
            {"id": str(role.id), "name": role.name, "position": role.position, "managed": role.managed}
            for role in guild.roles
        ],
        "channels": [
            {
                "id": str(channel.id), "name": channel.name[:100], "position": channel.position,
                "can_send": channel.permissions_for(guild.me).send_messages and channel.permissions_for(guild.me).view_channel,
                "can_webhook": channel.permissions_for(guild.me).manage_webhooks,
            }
            for channel in guild.text_channels[:500]
        ],
    } for guild in bot.guilds]
    try:
        await AGENT_CLIENT.heartbeat(
            session, guilds, adapter_catalog(), SELF_SERVER_UUID or None,
            bot=bot_info(), diagnostics=collect_diagnostics(),
        )
        remote = await AGENT_CLIENT.config(session)
        guild_ids = {str(guild["id"]): str(guild["discord_id"]) for guild in remote.get("guilds", [])}
        AGENT_GUILDS = {str(guild["id"]): dict(guild) for guild in remote.get("guilds", [])}
        bindings: dict[str, list[dict]] = defaultdict(list)
        uuids: dict[str, str] = {}
        names: dict[str, str] = {}
        for binding in remote.get("servers", []):
            if SELF_SERVER_UUID and str(binding.get("uuid", "")).lower() == SELF_SERVER_UUID:
                continue
            identifier = normalize_server_identifier(str(binding["identifier"]))
            normalized = dict(binding)
            normalized["discord_guild_id"] = str(
                binding.get("guild_discord_id") or guild_ids.get(str(binding.get("guild_id")), "")
            )
            normalized["dialect"] = resolve_adapter(binding.get("game_adapter"), binding.get("adapter_config"))
            bindings[identifier].append(normalized)
            uuids[identifier] = str(binding["uuid"])
            names[identifier] = str(binding["name"])
        AGENT_BINDINGS = dict(bindings)
        AGENT_SERVER_UUIDS = uuids
        AGENT_SERVER_NAMES = names
        if HEARTBEAT_FAILURES:
            logger.info("PteroRelay agent heartbeat recovered after %d failed attempt(s)", HEARTBEAT_FAILURES)
        HEARTBEAT_FAILURES = 0
    except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
        # Network blips are retried every 30 s; only a persistent outage is an error.
        HEARTBEAT_FAILURES += 1
        log = logger.error if HEARTBEAT_FAILURES >= 3 else logger.warning
        log("PteroRelay agent heartbeat failed (%d in a row): %s: %s", HEARTBEAT_FAILURES, type(exc).__name__, exc)
    except Exception:
        HEARTBEAT_FAILURES += 1
        logger.exception("PteroRelay agent heartbeat failed")


for background_task in (
    poll_server_statuses, update_status_messages, update_bot_presence, update_health_marker,
    sync_agent_heartbeat, relay_console_events,
):
    @background_task.before_loop
    async def wait_for_ready():
        await bot.wait_until_ready()


# ─── On Ready ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    global _commands_synced
    logger.info("Bot logged in as %s (%s)", bot.user, bot.user.id)
    if not _commands_synced:
        synced = await bot.tree.sync()
        _commands_synced = True
        logger.info("Synced slash commands: %s", len(synced))
    if not poll_server_statuses.is_running():
        poll_server_statuses.start()
    if not update_status_messages.is_running():
        update_status_messages.start()
    if not update_bot_presence.is_running():
        update_bot_presence.start()
    if not update_health_marker.is_running():
        update_health_marker.start()
    if not sync_agent_heartbeat.is_running():
        sync_agent_heartbeat.start()
    if not relay_console_events.is_running():
        relay_console_events.start()
    logger.info("Heartbeat, console relay, live status, presence and healthcheck tasks started")
    # Pterodactyl's egg waits for this line to mark the agent server as running.
    logger.info("PteroRelay agent ready")


def validate_environment() -> str:
    """The agent needs its Discord token and its credentials from Admin → PteroRelay."""
    required = {
        "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN"), "PANEL_PUBLIC_URL": PANEL_PUBLIC_URL,
        "PTERORELAY_AGENT_ID": PTERORELAY_AGENT_ID, "PTERORELAY_AGENT_SECRET": PTERORELAY_AGENT_SECRET,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Deploy the agent from Admin → PteroRelay, which fills them in."
        )
    if not PANEL_PUBLIC_URL.startswith(("http://", "https://")):
        raise RuntimeError("PANEL_PUBLIC_URL must start with https://")
    return os.environ["DISCORD_TOKEN"]


if __name__ == "__main__":
    try:
        # logging.basicConfig above already handles discord.py's loggers; its own handler would duplicate every line.
        bot.run(validate_environment(), log_handler=None)
    except RuntimeError as exc:
        logger.critical("Startup failed: %s", exc)
        raise SystemExit(2) from exc
