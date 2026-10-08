"""Render Discord chat into a game console command.

Styles decide how role colours are expressed in-game; escape modes make the
inserted values safe for the command syntax of the template.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.engine import ChatOut

STYLES = ("plain", "minecraft_json", "minecraft_legacy", "terraria", "unity_rich")
COLOR_STYLES = frozenset(STYLES) - {"plain"}
ESCAPES = ("none", "json_string", "double_quotes")
DEFAULT_LABEL = "[Discord]"
DEFAULT_LABEL_COLOR = 0x5865F2
DEFAULT_MAX_LENGTH = 256
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
# Invisible characters that hide text or reverse its direction (zero-width space, bidi
# overrides and isolates, word joiners, BOM). Zero-width (non-)joiners stay: emoji and
# some scripts need them.
INVISIBLE_RE = re.compile(r"[​‎‏‪-‮⁠-⁤⁦-⁩﻿؜]")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

# Minecraft legacy formatting codes and their RGB values.
LEGACY_COLORS = {
    "0": 0x000000, "1": 0x0000AA, "2": 0x00AA00, "3": 0x00AAAA,
    "4": 0xAA0000, "5": 0xAA00AA, "6": 0xFFAA00, "7": 0xAAAAAA,
    "8": 0x555555, "9": 0x5555FF, "a": 0x55FF55, "b": 0x55FFFF,
    "c": 0xFF5555, "d": 0xFF55FF, "e": 0xFFFF55, "f": 0xFFFFFF,
}


def sanitize(value: object, limit: int = 500) -> str:
    """Single line, no control or invisible characters, collapsed whitespace."""
    text = CONTROL_RE.sub(" ", INVISIBLE_RE.sub("", str(value)))
    return " ".join(text.split())[:limit]


def label(out: dict) -> str:
    return sanitize(out.get("label", DEFAULT_LABEL), 32)


def hex_color(color: int | None) -> str | None:
    return f"#{color:06X}" if color else None


def nearest_legacy_code(color: int) -> str:
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF

    def distance(item: tuple[str, int]) -> int:
        value = item[1]
        return (r - (value >> 16 & 0xFF)) ** 2 + (g - (value >> 8 & 0xFF)) ** 2 + (b - (value & 0xFF)) ** 2

    return min(LEGACY_COLORS.items(), key=distance)[0]


def _escape(value: str, mode: str) -> str:
    if mode == "json_string":
        return json.dumps(value, ensure_ascii=False)[1:-1]
    if mode == "double_quotes":
        return value.replace("\\", "").replace('"', "'")
    return value


def _styled(style: str, out: dict, author: str, message: str, role: str | None, color: int | None) -> tuple[str, str]:
    """Return ``(line, json)`` for the given style."""
    tag = label(out)
    label_color = int(out.get("label_color", DEFAULT_LABEL_COLOR))
    if style == "minecraft_legacy":
        author, message = author.replace("§", ""), message.replace("§", "")
        role = role.replace("§", "") if role else role
        code = f"§{nearest_legacy_code(color)}" if color else "§f"
        role_part = f"{code}[{role}] " if role else ""
        return f"§{nearest_legacy_code(label_color)}{tag} {role_part}{code}{author}§f: {message}", ""
    if style == "terraria":
        def tag_text(text: str) -> str:
            return text.replace("[", "(").replace("]", ")")

        name_hex = f"{color:06X}" if color else "FFFFFF"
        role_part = f"[c/{name_hex}:{tag_text(role)}] " if role else ""
        return f"[c/{label_color:06X}:{tag_text(tag)}] {role_part}[c/{name_hex}:{tag_text(author)}]: {message}", ""
    if style == "unity_rich":
        def plain(text: str) -> str:
            return text.replace("<", "‹").replace(">", "›")

        name_hex = hex_color(color) or "#FFFFFF"
        role_part = f"<color={name_hex}>[{plain(role)}]</color> " if role else ""
        return (
            f"<color={hex_color(label_color)}>{plain(tag)}</color> {role_part}"
            f"<color={name_hex}>{plain(author)}</color>: {plain(message)}"
        ), ""
    role_part = f"[{role}] " if role else ""
    line = f"{tag} {role_part}{author}: {message}"
    if style == "minecraft_json":
        name_color = hex_color(color) or "white"
        components: list[object] = ["", {"text": f"{tag} ", "color": hex_color(label_color)}]
        if role:
            components.append({"text": f"[{role}] ", "color": name_color})
        components.append({"text": author, "color": name_color})
        components.append({"text": f": {message}", "color": "white"})
        return line, json.dumps(components, ensure_ascii=False, separators=(",", ":"))
    return line, ""


def render(out: dict, chat: "ChatOut") -> str:
    template = str(out.get("template") or "")
    style = out.get("style", "plain") if out.get("style") in STYLES else "plain"
    escape = out.get("escape", "none") if out.get("escape") in ESCAPES else "none"
    max_length = max(16, min(int(out.get("max_length", DEFAULT_MAX_LENGTH)), 4000))
    # Characters a console treats as syntax (e.g. ";" separates commands in srcds) are removed
    # from everything a Discord user controls; Minecraft styles also drop § formatting codes.
    strip = str(out.get("strip") or "") + ("§" if style.startswith("minecraft") else "")
    table = {ord(char): None for char in strip}

    def clean(value: object, limit: int) -> str:
        return sanitize(str(value).translate(table), limit)

    author = clean(chat.author, 64) or "?"
    role = clean(chat.role, 32) if chat.role else None
    message = clean(chat.message, max_length)
    if not template or not message:
        return ""

    def build(text: str) -> str:
        line, payload = _styled(style, out, author, text, role, chat.role_color)
        values = {
            "author": author, "message": text, "role": role or "",
            "prefix": f"[{role}] " if role else "", "label": label(out), "line": line,
        }

        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            if key == "json":
                return payload
            if key in values:
                return _escape(values[key], escape)
            return match.group(0)

        return PLACEHOLDER_RE.sub(substitute, template)

    command = build(message)
    while len(command) > max_length:
        overflow = len(command) - max_length
        shorter = message[: len(message) - overflow - 1].rstrip()
        if not shorter:
            return ""
        message = shorter + "…"
        command = build(message)
    return command if len(command) <= max_length else ""
