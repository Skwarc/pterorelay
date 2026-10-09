"""Validate the community game setups in presets/ and write presets/index.json.

    python scripts/build_preset_index.py           # validate and write the index
    python scripts/build_preset_index.py --check   # validate and fail if the index is out of date

Each presets/<id>.json is a setup in the panel's Export setup format plus a name,
description, author and status. The panel lists index.json in Browse presets and
fetches the files from jsDelivr.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import regex

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.engine import pattern_sources  # noqa: E402
from adapters.registry import presets as builtin_presets  # noqa: E402

PRESET_DIR = ROOT / "presets"
INDEX_NAME = "index.json"
ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
STATUSES = ("verified", "likely", "unverified")
EVENT_KEYS = {"join", "leave", "death", "advancement", "broadcast", "server"}
FEATURES = {"chat_out", "colors_out", "chat_in", "join_leave", "death", "advancements", "server_messages", "server_status"}
PATTERN_KEYS = {"chat", "broadcast", "join", "leave", "advancement", "death", "server_ready", "server_stop"}
# Same limits as the panel's integration form, so an imported setup can be saved.
LIMITS = {"name": 100, "description": 1000, "author": 100}
MAX_PATTERN_LENGTH = 500


def _check_pattern(value: object, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str) or len(value) > MAX_PATTERN_LENGTH:
        return [f"{where} must be a string of at most {MAX_PATTERN_LENGTH} characters"]
    if value.strip() in ("", "auto"):
        return []
    errors = []
    for source in pattern_sources(value.strip()):
        try:
            regex.compile(source)
        except regex.error as error:
            errors.append(f"{where} does not compile: {error}")
    return errors


def _check_overrides(overrides: object) -> list[str]:
    if overrides is None:
        return []
    if not isinstance(overrides, dict) or not set(overrides) <= {"out", "in", "avatar_url"}:
        return ["overrides may only contain out, in and avatar_url"]
    errors = []
    inbound = overrides.get("in") or {}
    if not isinstance(inbound, dict):
        return ["overrides.in must be an object"]
    errors += _check_pattern(inbound.get("line_prefix"), "line_prefix")
    patterns = inbound.get("patterns") or {}
    if not isinstance(patterns, dict) or not set(patterns) <= PATTERN_KEYS:
        errors.append(f"overrides.in.patterns may only contain {', '.join(sorted(PATTERN_KEYS))}")
    else:
        for key, value in patterns.items():
            errors += _check_pattern(value, f"{key} pattern")
    ignore = inbound.get("ignore") or []
    if not isinstance(ignore, list) or len(ignore) > 20:
        errors.append("overrides.in.ignore must be a list of at most 20 patterns")
    else:
        for item in ignore:
            errors += _check_pattern(item, "ignore pattern")
    if "out" in overrides and not isinstance(overrides["out"], dict):
        errors.append("overrides.out must be an object")
    avatar = overrides.get("avatar_url")
    if avatar is not None and (not isinstance(avatar, str) or not avatar.startswith("https://")):
        errors.append("avatar_url must start with https://")
    return errors


def validate(path: Path, games: set[str]) -> list[str]:
    """Problems with one setup file; an empty list means it is valid."""
    if not ID_RE.match(path.stem):
        return ["the file name must be lowercase letters, digits and dashes (at most 64) plus .json"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"invalid JSON: {error}"]
    if not isinstance(data, dict):
        return ["the file must contain a JSON object"]
    errors = []
    if data.get("format") != "pterorelay-game" or data.get("version") != 1:
        errors.append('format must be "pterorelay-game" and version 1 (use Export setup in the panel)')
    if data.get("game") not in games:
        errors.append(f"game must be one of the built-in presets: {', '.join(sorted(games))}")
    for key, limit in LIMITS.items():
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            errors.append(f"{key} is required (at most {limit} characters)")
    if data.get("status") not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}")
    colors = data.get("event_colors")
    if colors is not None and (not isinstance(colors, dict) or not set(colors) <= EVENT_KEYS
                               or not all(isinstance(value, str) and COLOR_RE.match(value) for value in colors.values())):
        errors.append("event_colors must map event names to #RRGGBB colours")
    disabled = data.get("disabled_features", [])
    if not isinstance(disabled, list) or not all(isinstance(item, str) and item in FEATURES for item in disabled):
        errors.append(f"disabled_features may only contain {', '.join(sorted(FEATURES))}")
    errors += _check_overrides(data.get("overrides"))
    return errors


def build_index(directory: Path = PRESET_DIR) -> dict:
    """Validate every setup and return the index; raises ValueError listing all problems."""
    # "generic" (Custom in the panel) is allowed: it is how setups for games without a preset are shared.
    games = set(builtin_presets())
    problems, entries = [], []
    for path in sorted(directory.glob("*.json")):
        if path.name == INDEX_NAME:
            continue
        errors = validate(path, games)
        if errors:
            problems += [f"{path.name}: {error}" for error in errors]
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        entries.append({
            "id": path.stem, "file": path.name, "name": data["name"].strip(), "game": data["game"],
            "description": data["description"].strip(), "author": data["author"].strip(), "status": data["status"],
        })
    if problems:
        raise ValueError("\n".join(problems))
    return {"version": 1, "presets": entries}


def render(index: dict) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    try:
        index = build_index()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    target = PRESET_DIR / INDEX_NAME
    text = render(index)
    if "--check" in argv:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{target.relative_to(ROOT)} is out of date; run python scripts/build_preset_index.py", file=sys.stderr)
            return 1
        print(f"{len(index['presets'])} presets valid, index up to date")
        return 0
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"Wrote {target.relative_to(ROOT)} with {len(index['presets'])} presets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
