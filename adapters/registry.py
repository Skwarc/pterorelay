"""Built-in game presets and the catalogue reported to the panel."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from adapters.engine import Dialect, dialect_for

PRESET_DIR = Path(__file__).with_name("presets")
DEFAULT_PRESET = "generic"


@lru_cache(maxsize=1)
def presets() -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for path in sorted(PRESET_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        loaded[str(data["id"])] = data
    return loaded


def get_preset(adapter_id: str | None) -> dict | None:
    return presets().get(adapter_id or "")


def resolve(adapter_id: str | None, overrides: dict | None = None) -> Dialect | None:
    """Dialect for a binding, or ``None`` when no game integration is selected."""
    preset = get_preset(adapter_id)
    return dialect_for(preset, overrides) if preset is not None else None


def catalog() -> list[dict]:
    """Adapter descriptions the panel uses to render its integration settings."""
    items = []
    for preset in sorted(presets().values(), key=lambda item: (item.get("order", 500), item["id"])):
        dialect = dialect_for(preset)
        items.append({
            "id": preset["id"],
            "name": preset.get("name", preset["id"]),
            "notes": preset.get("notes", ""),
            # verified: checked against real console output; likely: matches documentation or
            # other parsers; unverified: best effort, check it with the panel's line tester.
            "status": preset.get("status", "unverified"),
            "capabilities": sorted(dialect.capabilities),
            "defaults": {key: preset[key] for key in ("out", "in", "avatar_url") if key in preset},
            "match": match_keywords(preset),
        })
    return items


def match_keywords(preset: dict) -> dict:
    match = preset.get("match") or {}
    return {key: [str(word).lower() for word in match.get(key) or []] for key in ("eggs", "images")}


def _words(text: str | None) -> str:
    # "Counter-Strike: GO" and "ghcr.io/parkervcp/games:rust" become " counter strike go " and
    # " ghcr io parkervcp games rust ", so keywords only match whole words ("rust", not "trust").
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def suggest(egg_name: str | None, image: str | None) -> str | None:
    """Preset that fits a server's egg or Docker image; the panel mirrors this in routes/server.php."""
    ordered = [item for item in catalog() if item["id"] != DEFAULT_PRESET]
    # Egg names are more telling than images (most Minecraft eggs share the generic Java image).
    for field, text in (("eggs", _words(egg_name)), ("images", _words(image))):
        for item in ordered:
            if any(_words(word) != "  " and _words(word) in text for word in item["match"][field]):
                return item["id"]
    return None
