"""Discord side of the game chat relay: identities, permissions and webhooks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import discord

from adapters.formatters import sanitize

logger = logging.getLogger("ptero-bot.chat")
WEBHOOK_NAME = "PteroSync"
CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w{1,32}):\d{1,20}>")
FORBIDDEN_WEBHOOK_NAMES = re.compile(r"discord|clyde|everyone|here", re.IGNORECASE)
DEFAULT_EVENT_COLORS = {
    "join": 0x57F287, "leave": 0xED4245, "death": 0x4F545C,
    "advancement": 0xFEE75C, "broadcast": 0xEB459E, "server": 0x5865F2,
}


@dataclass(frozen=True)
class RoleInfo:
    id: str
    name: str
    position: int
    color: int
    hoist: bool


def parse_hex(value: object) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return int(value[1:], 16)
    return None


def can_chat(role_ids: set[str], mappings: list[dict]) -> bool:
    """Everyone may chat unless at least one role is explicitly marked for chat."""
    chat_roles = {str(item.get("discord_role_id")) for item in mappings if item.get("can_chat")}
    return not chat_roles or bool(chat_roles & role_ids)


def chat_identity(roles: list[RoleInfo], mappings: list[dict]) -> tuple[str | None, int | None]:
    """Role label and colour shown in-game for a Discord member.

    A role with a configured in-game label or colour wins (highest first);
    otherwise the member's highest hoisted role and Discord display colour are used.
    """
    by_id = {str(item.get("discord_role_id")): item for item in mappings}
    ordered = sorted(roles, key=lambda role: role.position, reverse=True)
    display_color = next((role.color for role in ordered if role.color), None)
    for role in ordered:
        mapping = by_id.get(role.id)
        if mapping and (mapping.get("chat_label") or mapping.get("chat_color")):
            label = mapping.get("chat_label") or role.name
            return label, parse_hex(mapping.get("chat_color")) or role.color or display_color
    hoisted = next((role for role in ordered if role.hoist), None)
    return (hoisted.name if hoisted else None), display_color


def member_roles(member: discord.abc.User) -> list[RoleInfo]:
    return [
        RoleInfo(str(role.id), role.name, role.position, role.color.value, role.hoist)
        for role in getattr(member, "roles", [])
        if not role.is_default()
    ]


def chat_role_ids(roles: list[RoleInfo], guild_id: int | str) -> set[str]:
    """Role IDs that decide chat permission: the member's roles plus @everyone (its ID is the
    guild ID), so Chat ticked for @everyone lets every member chat."""
    return {role.id for role in roles} | {str(guild_id)}


def discord_message_text(message: discord.Message) -> str:
    """Plain single-line text of a Discord message for the game console."""
    text = CUSTOM_EMOJI_RE.sub(r":\1:", message.clean_content)
    text = discord.utils.remove_markdown(text)
    extras = ["[attachment]"] * len(message.attachments) + ["[sticker]"] * len(message.stickers)
    return sanitize(" ".join(part for part in [text, *extras] if part), 1000)


def webhook_username(player: str, rank: str | None = None) -> str:
    name = sanitize(f"[{rank}] {player}" if rank else player, 80)
    name = FORBIDDEN_WEBHOOK_NAMES.sub("•", name).strip()
    return name or "Player"


def event_color(kind: str, colors: dict | None) -> int:
    key = "server" if kind in ("server_ready", "server_stop") else kind
    return parse_hex((colors or {}).get(key)) or DEFAULT_EVENT_COLORS.get(key, DEFAULT_EVENT_COLORS["server"])


class ChatRelay:
    """Posts game chat through a channel webhook, falling back to bot messages."""

    def __init__(self, client: discord.Client) -> None:
        self.client = client
        self.webhooks: dict[int, discord.Webhook | None] = {}

    async def webhook_for(self, channel: discord.TextChannel) -> discord.Webhook | None:
        if channel.id in self.webhooks:
            return self.webhooks[channel.id]
        webhook = None
        if channel.permissions_for(channel.guild.me).manage_webhooks:
            try:
                existing = await channel.webhooks()
                webhook = next(
                    (item for item in existing if item.name == WEBHOOK_NAME and item.user and item.user.id == self.client.user.id),
                    None,
                ) or await channel.create_webhook(name=WEBHOOK_NAME, reason="PteroSync game chat relay")
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("Could not prepare a chat webhook in #%s", channel.name)
        self.webhooks[channel.id] = webhook
        return webhook

    async def post_chat(
        self, channel: discord.TextChannel, player: str, message: str, *, rank: str | None = None, avatar_url: str | None = None
    ) -> None:
        # Game chat is plain text: escape markdown so players cannot post masked links or headings.
        content = discord.utils.escape_markdown(sanitize(message, 1800))
        if not content:
            return
        webhook = await self.webhook_for(channel)
        if webhook is not None:
            try:
                await webhook.send(
                    content, username=webhook_username(player, rank), avatar_url=avatar_url,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.NotFound:
                self.webhooks.pop(channel.id, None)
            except discord.HTTPException:
                logger.warning("Webhook delivery failed in #%s; falling back to a bot message", channel.name)
        await channel.send(
            f"**{discord.utils.escape_markdown(webhook_username(player, rank))}**: {content}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def post_event(self, channel: discord.TextChannel, text: str, color: int) -> None:
        await channel.send(
            embed=discord.Embed(description=text, color=color),
            allowed_mentions=discord.AllowedMentions.none(),
        )
