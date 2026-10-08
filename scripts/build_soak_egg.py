"""Write soak/egg-pterosync-soak.json from soak/fake_game.py and soak/checker.py.

The egg's install script carries both files, so the soak servers need no network
access besides Discord (and pip, for the checker).

Usage: python scripts/build_soak_egg.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOAK = ROOT / "soak"
EGG = SOAK / "egg-pterosync-soak.json"
FILES = ("fake_game.py", "checker.py")

STARTUP = (
    'if [ "${SOAK_ROLE}" = "checker" ]; then '
    "python -m pip install --user --quiet --disable-pip-version-check --no-warn-script-location "
    "discord.py==2.7.1 aiohttp==3.14.3 && exec python checker.py; "
    "else exec python fake_game.py; fi"
)


def variable(name: str, env: str, description: str, default: str, rules: str) -> dict:
    return {"name": name, "description": description, "env_variable": env, "default_value": default,
            "user_viewable": True, "user_editable": True, "rules": rules, "field_type": "text"}


def install_script() -> str:
    lines = ["#!/bin/bash", "# PteroSync Soak installer: writes the soak scripts.", "set -euo pipefail",
             "mkdir -p /mnt/server", "cd /mnt/server"]
    for name in FILES:
        # Unix line endings whatever the checkout uses (Git for Windows writes CRLF), so the
        # egg is the same on every machine and the scripts run on Linux.
        encoded = base64.b64encode((SOAK / name).read_bytes().replace(b"\r\n", b"\n")).decode()
        lines.append(f"echo '{encoded}' | base64 -d > {name}")
    lines.append('echo "Installed the PteroSync soak scripts"')
    return "\n".join(lines) + "\n"


def egg() -> dict:
    return {
        "_comment": "PteroSync Soak: fake game servers and a checker bot for testing PteroSync. Not a game.",
        "meta": {"version": "PTDL_v2", "update_url": None},
        "exported_at": "2026-10-08T00:00:00+00:00",
        "name": "PteroSync Soak",
        "author": "noreply@pterosync.invalid",
        "description": "Soak test for PteroSync: a fake game server that writes numbered chat, or the checker bot "
                       "that measures delivery in Discord. See soak/README.md.",
        "features": None,
        "docker_images": {"Python 3.11": "ghcr.io/parkervcp/yolks:python_3.11"},
        "file_denylist": [],
        "startup": STARTUP,
        "config": {
            "files": "{}",
            "startup": json.dumps({"done": ["Done (", "Server started", "Game server connected", "Checker ready"]}),
            "logs": "{}",
            "stop": "^C",
        },
        "scripts": {"installation": {"script": install_script(), "container": "python:3.11-slim", "entrypoint": "bash"}},
        "variables": [
            variable("Role", "SOAK_ROLE", "game: a fake game server. checker: the checker bot.", "game", "required|in:game,checker"),
            variable("Game", "SOAK_DIALECT", "Console format of the fake game.", "minecraft-java",
                     "required|in:minecraft-java,terraria,valheim"),
            variable("Tag", "SOAK_TAG", "Short name in messages (letters and digits). Default: from the game.", "",
                     "nullable|alpha_num|max:8"),
            variable("Interval", "SOAK_INTERVAL", "Average seconds between console lines.", "20", "required|numeric|between:2,3600"),
            variable("Checker bot token", "CHECKER_TOKEN", "Checker only: token of a second Discord bot.", "", "nullable|string|max:200"),
            variable("Soak channels", "SOAK_CHANNEL_IDS", "Checker only: chat channel IDs of the fake servers, comma separated.",
                     "", "nullable|string|max:500"),
            variable("Report channel", "REPORT_CHANNEL_ID", "Checker only: channel for reports (default: first soak channel).",
                     "", "nullable|numeric"),
            variable("Ping seconds", "PING_SECONDS", "Checker only: seconds between Discord -> game round-trip checks.", "300",
                     "required|integer|between:30,86400"),
            variable("Report hours", "REPORT_HOURS", "Checker only: hours between reports.", "24", "required|integer|between:1,168"),
            variable("Panel URL", "PANEL_URL", "Checker only, optional: panel URL for the agent's memory.", "", "nullable|url"),
            variable("Panel API key", "PANEL_API_KEY", "Checker only, optional: a client API key that can see the agent server.",
                     "", "nullable|string|max:200"),
            variable("Agent server", "AGENT_SERVER", "Checker only, optional: the agent server's identifier (8 characters).", "",
                     "nullable|alpha_num|max:36"),
        ],
    }


def main() -> None:
    EGG.write_text(json.dumps(egg(), indent=4) + "\n", encoding="utf-8")
    print(f"Wrote {EGG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
