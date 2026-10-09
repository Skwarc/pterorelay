"""Checker bot for the PteroRelay soak test.

A second, separate Discord bot. It watches the chat channels of the fake game
servers (fake_game.py) and records every relayed message:

* game -> Discord: each "soak <tag> #<n> t=<time>" message, so gaps (lost),
  repeats (duplicated) and latency per server are measured;
* Discord -> game -> Discord: every PING_SECONDS it posts "soakin #<n> t=<time>";
  the fake servers echo it back as "soakack <tag> #<n>", giving the round trip;
* mention leaks: a relayed message that pinged anyone is a failure;
* the agent's memory, when PANEL_URL, PANEL_API_KEY and AGENT_SERVER are set.

It posts a report every REPORT_HOURS hours and answers /soak. Data is kept in
soak.db (SQLite), so the bot can restart without losing the history.

Environment: CHECKER_TOKEN, SOAK_CHANNEL_IDS (comma separated), REPORT_CHANNEL_ID
(default: the first soak channel), PING_SECONDS (300), REPORT_HOURS (24),
PANEL_URL, PANEL_API_KEY (a client API key), AGENT_SERVER (agent server identifier).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("soak-checker")

CHANNELS = {int(part) for part in re.findall(r"\d{5,20}", os.getenv("SOAK_CHANNEL_IDS", ""))}
REPORT_CHANNEL = int(os.getenv("REPORT_CHANNEL_ID", "0") or 0) or (min(CHANNELS) if CHANNELS else 0)
PING_SECONDS = max(30, int(os.getenv("PING_SECONDS", "300") or 300))
REPORT_HOURS = max(1, int(os.getenv("REPORT_HOURS", "24") or 24))
PANEL_URL = os.getenv("PANEL_URL", "").rstrip("/")
PANEL_API_KEY = os.getenv("PANEL_API_KEY", "")
AGENT_SERVER = os.getenv("AGENT_SERVER", "")
DATABASE = Path(os.getenv("SOAK_DB", "soak.db"))
# A ping without an echo after this long counts as lost.
ACK_TIMEOUT = 180

CHAT_RE = re.compile(r"soak (\w{1,8}) #(\d+) t=(\d+(?:\.\d+)?)")
ACK_RE = re.compile(r"soakack (\w{1,8}) #(\d+) t=(\d+(?:\.\d+)?)")
EVENT_RE = re.compile(r"\b([JD])([A-Za-z]{1,8}?)(\d+)\b")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat (tag TEXT, seq INTEGER, sent REAL, received REAL, leaked INTEGER);
CREATE TABLE IF NOT EXISTS events (tag TEXT, kind TEXT, seq INTEGER, received REAL);
CREATE TABLE IF NOT EXISTS pings (seq INTEGER PRIMARY KEY, sent REAL);
CREATE TABLE IF NOT EXISTS acks (tag TEXT, seq INTEGER, received REAL);
CREATE TABLE IF NOT EXISTS memory (at REAL, bytes INTEGER);
CREATE INDEX IF NOT EXISTS chat_tag ON chat (tag, seq);
-- Discord message IDs already counted, so history read after a restart is not counted twice.
CREATE TABLE IF NOT EXISTS seen (message_id INTEGER PRIMARY KEY, received REAL);
"""


def connect(path: Path = DATABASE) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    database.executescript(SCHEMA)
    return database


def record_message(database: sqlite3.Connection, text: str, received: float, pinged: bool,
                   message_id: int | None = None) -> bool:
    """Store whatever soak markers a relayed Discord message carries. False if already counted."""
    if message_id is not None:
        if database.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (message_id, received)).rowcount == 0:
            return False
    for tag, seq, sent in ACK_RE.findall(text):
        database.execute("INSERT INTO acks VALUES (?, ?, ?)", (tag, int(seq), received))
    without_acks = ACK_RE.sub("", text)
    for tag, seq, sent in CHAT_RE.findall(without_acks):
        database.execute("INSERT INTO chat VALUES (?, ?, ?, ?, ?)", (tag, int(seq), float(sent), received, int(pinged)))
    if not CHAT_RE.search(without_acks):
        for kind, tag, seq in EVENT_RE.findall(text):
            database.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (tag, "join/leave" if kind == "J" else "death", int(seq), received))
    database.commit()
    return True


def last_received(database: sqlite3.Connection) -> float | None:
    times = [database.execute(f"SELECT MAX(received) FROM {table}").fetchone()[0] for table in ("seen", "chat", "events", "acks")]
    return max((value for value in times if value is not None), default=None)


def missing_ranges(seen: list[int]) -> list[tuple[int, int]]:
    ranges = []
    for previous, current in zip(seen, seen[1:]):
        if current - previous > 1:
            ranges.append((previous + 1, current - 1))
    return ranges


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def report(database: sqlite3.Connection, since: float, now: float | None = None) -> str:
    """Plain-text summary of everything received after ``since``."""
    now = now or time.time()
    hours = (now - since) / 3600
    lines = [f"**PteroRelay soak report** (last {hours:.1f} h)"]
    tags = [row[0] for row in database.execute("SELECT DISTINCT tag FROM chat WHERE received >= ? ORDER BY tag", (since,))]
    for tag in tags:
        rows = database.execute("SELECT seq, sent, received, leaked FROM chat WHERE tag = ? AND received >= ?", (tag, since)).fetchall()
        seqs = [row[0] for row in rows]
        distinct = sorted(set(seqs))
        missing = missing_ranges(distinct)
        lost = sum(end - start + 1 for start, end in missing)
        expected = distinct[-1] - distinct[0] + 1
        latencies = [max(0.0, received - sent) for _, sent, received, _ in rows]
        leaks = sum(row[3] for row in rows)
        line = (f"`{tag}` chat: {len(distinct)}/{expected} delivered ({100 * len(distinct) / expected:.2f}%), "
                f"{lost} lost, {len(seqs) - len(distinct)} duplicated, "
                f"latency median {statistics.median(latencies):.1f}s / p95 {percentile(latencies, 0.95):.1f}s / max {max(latencies):.1f}s")
        if leaks:
            line += f", **{leaks} pinged someone**"
        if missing:
            shown = ", ".join(f"#{a}" if a == b else f"#{a}–#{b}" for a, b in missing[:5])
            line += f"\n  gaps: {shown}{' …' if len(missing) > 5 else ''}"
        lines.append(line)
    if not tags:
        lines.append("No relayed chat received.")

    for tag, kind, count in database.execute(
            "SELECT tag, kind, COUNT(DISTINCT seq) FROM events WHERE received >= ? GROUP BY tag, kind ORDER BY tag, kind", (since,)):
        lines.append(f"`{tag}` {kind} events: {count}")

    pings = [row[0] for row in database.execute("SELECT seq FROM pings WHERE sent >= ? AND sent <= ?", (since, now - ACK_TIMEOUT))]
    if pings:
        for tag, answered, round_trips in _round_trips(database, pings):
            text = f"`{tag}` Discord → game → Discord: {answered}/{len(pings)} answered"
            if round_trips:
                text += f", median {statistics.median(round_trips):.1f}s"
            lines.append(text)

    memory = database.execute("SELECT at, bytes FROM memory WHERE at >= ? ORDER BY at", (since,)).fetchall()
    if memory:
        values = [row[1] / 1048576 for row in memory]
        lines.append(f"Agent memory: now {values[-1]:.0f} MB, min {min(values):.0f}, max {max(values):.0f}, "
                     f"change {values[-1] - values[0]:+.0f} MB")
    return "\n".join(lines)


def _round_trips(database: sqlite3.Connection, pings: list[int]):
    marks = ",".join("?" * len(pings))
    sent = dict(database.execute(f"SELECT seq, sent FROM pings WHERE seq IN ({marks})", pings))
    tags = [row[0] for row in database.execute("SELECT DISTINCT tag FROM acks ORDER BY tag")]
    for tag in tags:
        acks = database.execute(f"SELECT seq, MIN(received) FROM acks WHERE tag = ? AND seq IN ({marks}) GROUP BY seq", [tag, *pings]).fetchall()
        yield tag, len(acks), [received - sent[seq] for seq, received in acks]


class Checker(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.database = connect()
        self.started = time.time()

    async def setup_hook(self) -> None:
        @self.tree.command(name="soak", description="Soak test results so far")
        @app_commands.describe(hours="Hours to cover (default: since the checker started)")
        async def soak(interaction: discord.Interaction, hours: float | None = None) -> None:
            since = time.time() - hours * 3600 if hours else self.started
            await interaction.response.send_message(report(self.database, since)[:1990], ephemeral=True)

        await self.tree.sync()
        self.ping.start()
        self.daily.start()
        if PANEL_URL and PANEL_API_KEY and AGENT_SERVER:
            self.memory.start()

    async def on_ready(self) -> None:
        print(f"Checker ready as {self.user}, watching {sorted(CHANNELS)}", flush=True)
        for channel_id in sorted(CHANNELS | {REPORT_CHANNEL}):
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                print(f"PROBLEM: channel {channel_id} not found: invite the checker to that Discord server.", flush=True)
                continue
            permissions = channel.permissions_for(channel.guild.me)
            missing = [name for name, allowed in (("View Channel", permissions.view_channel),
                                                  ("Send Messages", permissions.send_messages),
                                                  ("Read Message History", permissions.read_message_history)) if not allowed]
            if missing:
                print(f"PROBLEM: #{channel.name} ({channel_id}) is missing {', '.join(missing)} for the checker. "
                      "Add it in the channel's Permissions, then restart the checker.", flush=True)
            else:
                print(f"Watching #{channel.name} ({channel_id})", flush=True)
        await self.catch_up()

    def record(self, message: discord.Message) -> bool:
        if message.channel.id not in CHANNELS or message.author.id == self.user.id:
            return False
        text = " ".join([message.content, *(f"{e.title or ''} {e.description or ''} {e.author.name or ''}" for e in message.embeds)])
        pinged = bool(message.mention_everyone or message.mentions or message.role_mentions)
        return record_message(self.database, text, message.created_at.timestamp(), pinged, message.id)

    async def on_message(self, message: discord.Message) -> None:
        self.record(message)

    async def catch_up(self) -> None:
        """Count what was posted while the checker was offline or disconnected, from channel history."""
        last = last_received(self.database)
        if last is None:
            return
        after = discord.Object(id=discord.utils.time_snowflake(datetime.fromtimestamp(last - 60, timezone.utc)))
        for channel_id in sorted(CHANNELS):
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            counted = 0
            try:
                async for message in channel.history(limit=None, after=after, oldest_first=True):
                    counted += self.record(message)
            except discord.HTTPException as error:
                log.warning("Could not read the history of %s: %s", channel_id, error)
            if counted:
                print(f"Caught up {counted} messages in #{channel.name} posted while the checker was away", flush=True)

    @tasks.loop(seconds=PING_SECONDS)
    async def ping(self) -> None:
        await self.wait_until_ready()
        seq = (self.database.execute("SELECT MAX(seq) FROM pings").fetchone()[0] or 0) + 1
        now = time.time()
        sent = 0
        for channel_id in CHANNELS:
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue  # reported in on_ready
            try:
                await channel.send(f"soakin #{seq} t={now:.3f}", allowed_mentions=discord.AllowedMentions.none())
                sent += 1
            except discord.HTTPException as error:
                log.warning("Ping to %s failed: %s", channel_id, error)
        # Only pings that went out count, so a missing permission is not reported as lost echoes.
        if sent:
            self.database.execute("INSERT INTO pings VALUES (?, ?)", (seq, now))
            self.database.commit()

    @tasks.loop(hours=REPORT_HOURS)
    async def daily(self) -> None:
        await self.wait_until_ready()
        if time.time() - self.started < 600:
            return  # the first run fires at start
        channel = self.get_channel(REPORT_CHANNEL)
        if isinstance(channel, discord.TextChannel):
            await channel.send(report(self.database, time.time() - REPORT_HOURS * 3600)[:1990],
                               allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=10)
    async def memory(self) -> None:
        url = f"{PANEL_URL}/api/client/servers/{AGENT_SERVER}/resources"
        headers = {"Authorization": f"Bearer {PANEL_API_KEY}", "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession() as session, session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as response:
                data = await response.json()
            used = int(data["attributes"]["resources"]["memory_bytes"])
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as error:
            log.warning("Agent memory unavailable: %s", error)
            return
        self.database.execute("INSERT INTO memory VALUES (?, ?)", (time.time(), used))
        self.database.commit()


def main() -> None:
    token = os.getenv("CHECKER_TOKEN", "")
    if not token or not CHANNELS:
        raise SystemExit("Set CHECKER_TOKEN and SOAK_CHANNEL_IDS.")
    Checker().run(token, log_handler=None)


if __name__ == "__main__":
    main()
