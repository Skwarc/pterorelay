"""A fake game server for the PteroSync soak test.

Runs on Wings like any game (the "PteroSync Soak" egg) and writes console lines the
way Minecraft Java, Terraria or Valheim do: numbered player chat, joins, leaves and
deaths, with tricky messages mixed in. Every chat line carries a sequence number and
the time it was written, so the checker bot can find lost, duplicated and slow
messages in Discord.

Console input is answered too: when the agent relays a checker message
("soakin #N t=..."), a player echoes it back ("soakack #N t=..."), which measures
Discord -> game -> Discord.

Settings (environment): SOAK_DIALECT minecraft-java | terraria | valheim,
SOAK_TAG short name in messages, SOAK_INTERVAL average seconds between lines.
Standard library only.
"""

from __future__ import annotations

import json
import os
import random
import re
import select
import sys
import time
from datetime import datetime
from pathlib import Path

DIALECT = os.getenv("SOAK_DIALECT", "minecraft-java").strip().lower()
TAG = re.sub(r"[^A-Za-z0-9]", "", os.getenv("SOAK_TAG", "") or DIALECT.split("-")[0])[:8] or "soak"
INTERVAL = max(2.0, float(os.getenv("SOAK_INTERVAL", "20") or 20))
STATE = Path(os.getenv("SOAK_STATE", "soak-state.json"))
PLAYERS = ["Steve", "Alex", "Ana_B", "Zoe99"]
ECHO_PLAYER = "Echo"

# Messages that once broke (or could break) a relay. {n} is the sequence marker.
TRICKY = [
    "{n} @everyone @here <@123456789012345678>",
    "{n} **bold** ||spoiler|| `code` # heading",
    "{n} <Server> fake broadcast",
    "{n} x has joined. Steve left the game",
    "{n} čšž 😀 中文 ✓",
    "{n} [c/FF0000:red] [i:29] §cred",
    "{n} https://discord.gg/example",
    "{n} " + "long " * 50,
    "{n} trailing spaces   ",
    "{n} \"quotes\" and 'apostrophes' {{braces}} \\backslash",
]


def now_stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def emit(text: str) -> None:
    """One console line in the dialect's format."""
    if DIALECT == "minecraft-java":
        line = f"[{now_stamp()}] [Server thread/INFO]: {text}"
    elif DIALECT == "valheim":
        line = f"{datetime.now():%m/%d/%Y %H:%M:%S}: {text}"
    else:
        line = text
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def chat(player: str, message: str) -> None:
    if DIALECT == "valheim":  # vanilla Valheim logs no chat
        return
    emit(f"<{player}> {message}")


def join(player: str) -> None:
    if DIALECT == "minecraft-java":
        emit(f"{player} joined the game")
    elif DIALECT == "terraria":
        emit(f"{player} has joined.")
    else:
        emit(f"Got character ZDOID from {player} : 5:1")


def leave(player: str) -> None:
    if DIALECT == "minecraft-java":
        emit(f"{player} left the game")
    elif DIALECT == "terraria":
        emit(f"{player} has left.")


def death(player: str) -> None:
    if DIALECT == "minecraft-java":
        emit(f"{player} fell from a high place")
    elif DIALECT == "valheim":
        emit(f"Got character ZDOID from {player} : 0:0")


def ready() -> None:
    if DIALECT == "minecraft-java":
        emit('Done (1.234s)! For help, type "help"')
    elif DIALECT == "terraria":
        emit("Server started")
    else:
        emit("Game server connected")


def load_counters() -> dict[str, int]:
    """Counters survive restarts, so a gap in Discord is a lost message, not a restart."""
    try:
        data = json.loads(STATE.read_text())
        return {key: int(data.get(key, 0)) for key in ("chat", "join", "death")}
    except (OSError, ValueError, AttributeError):
        return {"chat": 0, "join": 0, "death": 0}


def marker(sequence: int) -> str:
    return f"soak {TAG} #{sequence} t={time.time():.3f}"


def handle_command(command: str) -> bool:
    """React to console input. Returns False on stop."""
    command = command.strip()
    if command in ("stop", "exit", "quit"):
        emit("Stopping the server" if DIALECT == "minecraft-java" else "Server stopping")
        return False
    found = re.search(r"soakin #(\d+) t=(\d+(?:\.\d+)?)", command)
    if found:
        # Received from Discord: echo it back as player chat for the round trip.
        chat(ECHO_PLAYER, f"soakack {TAG} #{found.group(1)} t={found.group(2)}")
    elif command.startswith("say "):
        if DIALECT == "minecraft-java":
            emit(f"[Server] {command[4:]}")
        elif DIALECT == "terraria":
            emit(f"<Server> {command[4:]}")
    return True


def main() -> int:
    random.seed()
    counters = load_counters()
    emit(f"PteroSync soak server ({DIALECT}, tag {TAG}) starting at chat #{counters['chat'] + 1}")
    ready()
    online: list[str] = []
    next_line = time.monotonic() + random.uniform(1, INTERVAL)
    while True:
        timeout = max(0.0, next_line - time.monotonic())
        if select.select([sys.stdin], [], [], timeout)[0]:
            command = sys.stdin.readline()
            if command == "":
                time.sleep(timeout)  # stdin closed: keep producing lines
            elif not handle_command(command):
                return 0
            continue
        roll = random.random()
        if DIALECT == "valheim":  # no chat: deaths are what Discord gets
            roll = 0.11 if roll < 0.5 else 0.0
        if roll < 0.06 and len(online) < 20:
            counters["join"] += 1
            online.append(f"J{TAG}{counters['join']}")
            join(online[-1])
        elif roll < 0.10 and online:
            leave(online.pop(0))
        elif roll < 0.13 and DIALECT != "terraria":  # the Terraria preset has no death pattern
            counters["death"] += 1
            death(f"D{TAG}{counters['death']}")
        else:
            counters["chat"] += 1
            text = marker(counters["chat"])
            if roll < 0.18:
                text = random.choice(TRICKY).format(n=text)
            chat(random.choice(PLAYERS), text)
        STATE.write_text(json.dumps(counters))
        next_line = time.monotonic() + random.expovariate(1 / INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
