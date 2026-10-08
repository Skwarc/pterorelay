"""Authenticated transport between the Discord agent and Panel extension."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

import aiohttp

# A pooled keep-alive connection can be closed by the panel or a proxy just as it is reused;
# such requests never reached the panel and are safe to send again on a fresh connection.
STALE_CONNECTION_ERRORS = (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)


@dataclass(frozen=True)
class DiscordActor:
    guild_id: str
    user_id: str
    role_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "guild_id": self.guild_id,
            "discord_user_id": self.user_id,
            "role_ids": list(self.role_ids),
        }


class ConsoleUnavailable(Exception):
    """The panel could not read a server's console from Wings."""


class AgentClient:
    def __init__(self, panel_url: str, agent_id: str, secret: str, version: str = "0.5.0-beta.4"):
        self.base_url = panel_url.rstrip("/") + "/pterosync-agent"
        self.agent_id = agent_id
        self.secret = secret.encode()
        self.version = version

    def signed_headers(self, method: str, path: str, body: bytes, *, timestamp: int | None = None, nonce: str | None = None) -> dict[str, str]:
        timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
        nonce_value = nonce or secrets.token_hex(16)
        digest = hashlib.sha256(body).hexdigest()
        message = "\n".join((method.upper(), path, timestamp_value, nonce_value, digest)).encode()
        signature = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        return {
            "X-PteroSync-Agent": self.agent_id,
            "X-PteroSync-Timestamp": timestamp_value,
            "X-PteroSync-Nonce": nonce_value,
            "X-PteroSync-Signature": signature,
            "X-PteroSync-Version": self.version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def request(
        self, session: aiohttp.ClientSession, method: str, endpoint: str, payload: dict[str, object] | None = None,
        *, retry: bool | None = None,
    ) -> dict:
        """Signed request; GETs (and ``retry=True`` calls) are retried once after a dropped connection.

        Actions that must not run twice (power, console commands, chat) are never retried.
        """
        path = f"/pterosync-agent/{endpoint.lstrip('/')}"
        body = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False).encode()
        attempts = 2 if (method.upper() == "GET" if retry is None else retry) else 1
        for attempt in range(attempts):
            # A fresh nonce per attempt: the panel rejects a reused one as a replay.
            headers = self.signed_headers(method, path, body)
            try:
                async with session.request(method, self.base_url + "/" + endpoint.lstrip("/"), data=body, headers=headers) as response:
                    response.raise_for_status()
                    if response.status == 204:
                        return {}
                    return await response.json()
            except STALE_CONNECTION_ERRORS:
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(0.5)
        raise AssertionError("unreachable")

    async def heartbeat(
        self, session: aiohttp.ClientSession, guilds: list[dict[str, object]], adapters: list[dict] | None = None,
        self_server_uuid: str | None = None, *, bot: dict | None = None, diagnostics: dict | None = None,
    ) -> dict:
        payload: dict[str, object] = {"guilds": guilds}
        if bot is not None:
            payload["bot"] = bot
        if diagnostics is not None:
            payload["diagnostics"] = diagnostics
        if adapters is not None:
            payload["adapters"] = adapters
        if self_server_uuid:
            payload["self_server_uuid"] = self_server_uuid
        # The heartbeat only upserts state, so repeating it is harmless.
        return await self.request(session, "POST", "heartbeat", payload, retry=True)

    async def config(self, session: aiohttp.ClientSession) -> dict:
        return await self.request(session, "GET", "config")

    async def service_status(self, session: aiohttp.ClientSession, server_uuid: str) -> dict:
        data = await self.request(session, "GET", f"servers/{server_uuid}/status")
        resources = data.get("resources", {})
        network = resources.pop("network", {}) if isinstance(resources, dict) else {}
        if isinstance(resources, dict):
            resources.setdefault("network_rx_bytes", network.get("rx_bytes", 0))
            resources.setdefault("network_tx_bytes", network.get("tx_bytes", 0))
        return data

    async def power(self, session: aiohttp.ClientSession, server_uuid: str, signal: str, actor: DiscordActor) -> None:
        await self.request(session, "POST", f"servers/{server_uuid}/power", {**actor.payload(), "signal": signal})

    async def command(self, session: aiohttp.ClientSession, server_uuid: str, command: str, actor: DiscordActor) -> None:
        await self.request(session, "POST", f"servers/{server_uuid}/commands", {**actor.payload(), "command": command})

    async def link_code(self, session: aiohttp.ClientSession, guild_id: str, user_id: str) -> dict:
        return await self.request(session, "POST", f"guilds/{guild_id}/link-codes", {"discord_user_id": user_id})

    async def chat(self, session: aiohttp.ClientSession, server_uuid: str, commands: list[str], actor: DiscordActor) -> None:
        await self.request(session, "POST", f"servers/{server_uuid}/chat", {**actor.payload(), "commands": commands})

    async def logs_batch(self, session: aiohttp.ClientSession, server_uuids: list[str]) -> dict[str, list[str] | ConsoleUnavailable]:
        """Console lines of several servers in one request (the panel rate-limits per client)."""
        data = await self.request(session, "POST", "logs", {"servers": server_uuids}, retry=True)
        results: dict[str, list[str] | ConsoleUnavailable] = {}
        for uuid in server_uuids:
            entry = (data.get("servers") or {}).get(uuid) or {}
            results[uuid] = (
                [str(line) for line in entry.get("lines", [])] if entry.get("available")
                else ConsoleUnavailable(str(entry.get("error") or "the panel could not read the console"))
            )
        return results

    async def websocket(self, session: aiohttp.ClientSession, server_uuid: str) -> dict:
        """Wings websocket address, a short-lived read-only token and the Origin Wings expects."""
        return await self.request(session, "GET", f"servers/{server_uuid}/websocket")

    async def logs(self, session: aiohttp.ClientSession, server_uuid: str) -> list[str]:
        """Recent console lines (oldest first); raises ConsoleUnavailable when Wings cannot be read."""
        data = await self.request(session, "GET", f"servers/{server_uuid}/logs")
        if not data.get("available"):
            raise ConsoleUnavailable(str(data.get("error") or "the panel could not read the console"))
        return [str(line) for line in data.get("lines", [])]
