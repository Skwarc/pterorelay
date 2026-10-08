"""Generic console dialect engine.

A game adapter is declarative data (a *dialect*): how to recognise chat, join,
leave and death lines in the server console, and which console command
broadcasts a message in-game. Only Wings console I/O is used, so games that
cannot be modded are supported as long as their console exposes the events.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import regex

from adapters import formatters

logger = logging.getLogger("ptero-bot.adapters")
# User patterns run with the `regex` module and a time limit, so a catastrophically
# backtracking pattern (ReDoS) only fails that pattern instead of freezing the agent.
PATTERN_TIMEOUT = 0.05

EVENT_KINDS = ("broadcast", "chat", "join", "leave", "advancement", "death", "server_ready", "server_stop")
# Markup our own formatters put before the label (Terraria tags, Unity rich text, § codes).
LEADING_MARKUP_RE = re.compile(r"^(?:\[c/[0-9A-Fa-f]{6}:|<color=[^>]*>|§.)+")
MAX_PATTERN_LENGTH = 500
MAX_LINE_LENGTH = 1000
# OSC sequences (e.g. "ESC ]0;window title BEL", set by Terraria/TShock) come first, then CSI and others.
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")

# Patterns are written like the console line they match: "<{player}> {message}".
# Alternatives are separated by "||"; a pattern starting with "^" or "re:" is a regex.
TEMPLATE_TOKEN_RE = re.compile(r"\{(player|message|rank|\*)\}")
TEMPLATE_TOKENS = {
    "player": r"(?P<player>.+?)",
    "message": r"(?P<message>.+)",
    "rank": r"(?P<rank>.+?)",
    "*": r".*?",
}
# line_prefix "auto": common timestamp / log-level prefixes, the first match is removed.
AUTO_PREFIXES = (
    r"^\[[^\]]*\d{1,2}:\d{2}[^\]]*\](?:\s*\[[^\]]*\])*\s*:?\s*",  # [12:00:00] [Server thread/INFO]:
    r"^L \d{2}/\d{2}/\d{4} - \d{2}:\d{2}:\d{2}:\s*",  # Source engine
    r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:[.,:]\d+)?(?:Z|[+-]\d{2}:?\d{2})?:?\s*"
    r"(?:\[?(?:INFO|WARN(?:ING)?|ERROR|DEBUG)\]?:?\s*)?",  # 10/06/2026 12:00:00: / ISO dates
    r"^\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?:?\s*",  # bare time
)


@dataclass(frozen=True)
class ChatOut:
    """A Discord message to broadcast in-game."""

    author: str
    message: str
    role: str | None = None
    role_color: int | None = None


@dataclass(frozen=True)
class GameEvent:
    kind: str
    player: str | None = None
    message: str | None = None
    rank: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class Dialect:
    id: str
    name: str
    out: dict
    inbound: dict
    avatar_url: str | None
    patterns: tuple[tuple[str, regex.Pattern], ...] = field(default=(), compare=False)
    ignore: tuple[regex.Pattern, ...] = field(default=(), compare=False)
    line_prefixes: tuple[regex.Pattern, ...] = field(default=(), compare=False)

    @property
    def capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        if self.out.get("template"):
            caps.add("chat_out")
            if self.out.get("style", "plain") in formatters.COLOR_STYLES:
                caps.add("colors_out")
        kinds = {kind for kind, _ in self.patterns}
        if "chat" in kinds:
            caps.add("chat_in")
        if {"join", "leave"} & kinds:
            caps.add("join_leave")
        if "death" in kinds:
            caps.add("death")
        if "broadcast" in kinds:
            caps.add("server_messages")
        if "advancement" in kinds:
            caps.add("advancements")
        if {"server_ready", "server_stop"} & kinds:
            caps.add("server_status")
        return frozenset(caps)

    @property
    def reads_console(self) -> bool:
        return bool(self.patterns)


def deep_merge(base: dict, overrides: dict | None) -> dict:
    """Return ``base`` with ``overrides`` applied; ``None`` values remove keys."""
    merged = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def template_to_regex(template: str) -> str:
    """``<{player}> {message}`` -> ``^<(?P<player>.+?)>\\s+(?P<message>.+)$``."""

    def literal(text: str) -> str:
        return r"\s+".join(re.escape(chunk) for chunk in re.split(r"\s+", text))

    parts, position, used = [], 0, set()
    for match in TEMPLATE_TOKEN_RE.finditer(template):
        parts.append(literal(template[position:match.start()]))
        name = match.group(1)
        parts.append(r".+?" if name in used else TEMPLATE_TOKENS[name])
        if name != "*":
            used.add(name)
        position = match.end()
    parts.append(literal(template[position:]))
    return "^" + "".join(parts) + "$"


def pattern_sources(pattern: str) -> list[str]:
    """Regex source(s) for a pattern written as a template or a regex."""
    if pattern.startswith("re:"):
        return [pattern[3:]]
    if pattern.startswith("^"):
        return [pattern]
    return [template_to_regex(item.strip()) for item in pattern.split("||") if item.strip()]


def _compile_all(pattern: object) -> list[regex.Pattern]:
    if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN_LENGTH:
        return []
    compiled = []
    for source in pattern_sources(pattern.strip()):
        try:
            compiled.append(regex.compile(source))
        except regex.error:
            continue
    return compiled


def _line_prefixes(value: object) -> tuple[regex.Pattern, ...]:
    if value == "auto":
        return tuple(regex.compile(source) for source in AUTO_PREFIXES)
    return tuple(_compile_all(value))


def _search(pattern: regex.Pattern, text: str):
    try:
        return pattern.search(text, timeout=PATTERN_TIMEOUT)
    except TimeoutError:
        logger.warning("Pattern %r took too long and was skipped; simplify it", pattern.pattern[:80])
        return None


def build_dialect(data: dict) -> Dialect:
    """Compile a merged preset/override document; invalid regexes are skipped."""
    inbound = data.get("in") or {}
    patterns = []
    raw_patterns = inbound.get("patterns") or {}
    for kind in EVENT_KINDS:
        patterns.extend((kind, compiled) for compiled in _compile_all(raw_patterns.get(kind)))
    ignore = tuple(compiled for item in inbound.get("ignore") or [] for compiled in _compile_all(item))
    return Dialect(
        id=str(data.get("id", "generic")),
        name=str(data.get("name", data.get("id", "Generic"))),
        out=dict(data.get("out") or {}),
        inbound=dict(inbound),
        avatar_url=data.get("avatar_url") or None,
        patterns=tuple(patterns),
        ignore=ignore,
        line_prefixes=_line_prefixes(inbound.get("line_prefix")),
    )


@lru_cache(maxsize=256)
def _cached_dialect(serialized: str) -> Dialect:
    return build_dialect(json.loads(serialized))


def dialect_for(preset: dict, overrides: dict | None = None) -> Dialect:
    merged = deep_merge(preset, overrides if isinstance(overrides, dict) else None)
    return _cached_dialect(json.dumps(merged, sort_keys=True))


def clean_line(dialect: Dialect, line: str) -> str:
    # A carriage return moves the cursor back like a terminal does: consoles redraw their
    # input prompt (e.g. "> ....\r") before each line, so only the text after the last one counts.
    line = line[:MAX_LINE_LENGTH].rstrip("\r\n").rsplit("\r", 1)[-1]
    if dialect.inbound.get("strip_ansi", True):
        line = ANSI_RE.sub("", line)
    for prefix in dialect.line_prefixes:
        match = _search(prefix, line)
        stripped = line[:match.start()] + line[match.end():] if match else line
        if stripped != line:
            line = stripped
            break
    return line.strip()


def is_relay_echo(dialect: Dialect, message: str) -> bool:
    """True for a Discord message we relayed ourselves, printed back by the console."""
    tag = formatters.label(dialect.out)
    text = LEADING_MARKUP_RE.sub("", message)
    variants = {tag, tag.replace("[", "(").replace("]", ")")}
    return any(variant and text.startswith(variant) for variant in variants)


def parse_line(dialect: Dialect, line: str) -> GameEvent | None:
    """Turn one console line into a game event, or ``None`` when irrelevant."""
    cleaned = clean_line(dialect, line)
    if not cleaned or any(_search(pattern, cleaned) for pattern in dialect.ignore):
        return None
    for kind, pattern in dialect.patterns:
        match = _search(pattern, cleaned)
        if match is None:
            continue
        groups = match.groupdict()
        player = (groups.get("player") or "").strip() or None
        message = (groups.get("message") or "").strip() or None
        if (kind == "chat" and (not player or not message)) or (kind == "broadcast" and not message):
            continue
        if kind == "advancement" and not player:
            continue
        if kind in ("chat", "broadcast") and is_relay_echo(dialect, message):
            return None
        return GameEvent(kind, player, message, (groups.get("rank") or "").strip() or None, cleaned)
    return None


def render_outgoing(dialect: Dialect, chat: ChatOut) -> list[str]:
    """Render console command(s) that broadcast ``chat`` in-game."""
    if "chat_out" not in dialect.capabilities:
        return []
    command = formatters.render(dialect.out, chat)
    return [command] if command else []
