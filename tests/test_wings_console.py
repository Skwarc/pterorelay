import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

import bot
from adapters.registry import resolve
from agent_client import AgentClient
from test_agent_client import FakeSession
from wings_console import ConsoleStream

ORIGIN = "https://panel.example.com"


class FakeWings:
    """A Wings console websocket: requires the panel Origin and a valid token before it talks."""

    def __init__(self):
        self.tokens: list[str] = []
        self.valid = {"token-1", "token-2", "token-3", "token-4"}
        self.lines = ["[12:00:01] [Server thread/INFO]: <Steve> hi"]
        self.expire_after_auth = True
        self.refuse = False
        self.connections = 0
        self.sockets: list[web.WebSocketResponse] = []
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/servers/{uuid}/ws", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api/servers/uuid-1/ws"

    async def stop(self):
        for ws in self.sockets:
            await ws.close()
        await self.runner.cleanup()

    async def drop(self):
        """Close every socket and refuse new ones, as when Wings goes away."""
        self.refuse = True
        for ws in self.sockets:
            await ws.close()

    async def handle(self, request):
        if self.refuse:
            return web.Response(status=503)
        if request.headers.get("Origin") != ORIGIN:
            return web.Response(status=403)
        self.connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        async for message in ws:
            data = json.loads(message.data)
            if data["event"] != "auth":
                continue
            token = data["args"][0]
            self.tokens.append(token)
            if token not in self.valid:
                await ws.send_json({"event": "jwt error", "args": ["jwt: invalid token"]})
                continue
            await ws.send_json({"event": "auth success"})
            for line in self.lines:
                await ws.send_json({"event": "console output", "args": [line]})
            if self.expire_after_auth and len(self.tokens) == 1:
                await ws.send_json({"event": "token expiring"})
        return ws


async def wait_for(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class ConsoleStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.wings = FakeWings()
        await self.wings.start()
        self.session = aiohttp.ClientSession()
        self.issued = 0
        self.lines: list[str] = []
        self.changes: list[bool] = []

    async def asyncTearDown(self):
        await self.wings.stop()
        await self.session.close()

    async def credentials(self):
        self.issued += 1
        return {"socket": self.wings.url, "token": f"token-{self.issued}", "origin": ORIGIN}

    def stream(self, credentials=None):
        async def session():
            return self.session

        async def on_lines(lines):
            self.lines.extend(lines)

        return ConsoleStream(
            "abcd1234", credentials or self.credentials, on_lines, session=session,
            on_live=lambda live, reason: self.changes.append(live), min_backoff=0.01, max_backoff=0.05,
        )

    async def test_console_lines_are_delivered_and_token_is_renewed(self):
        stream = self.stream()
        stream.start()
        try:
            await wait_for(lambda: stream.authentications == 2)
            self.assertTrue(stream.live)
            self.assertEqual(self.wings.tokens, ["token-1", "token-2"])
            self.assertEqual(self.wings.connections, 1)  # renewed on the open socket
            await wait_for(lambda: len(self.lines) == 2)
            self.assertEqual(self.lines, ["[12:00:01] [Server thread/INFO]: <Steve> hi"] * 2)
        finally:
            await stream.stop()
        self.assertFalse(stream.live)
        self.assertEqual(self.changes, [True, False])

    async def test_reconnects_with_fresh_credentials_after_a_drop(self):
        self.wings.expire_after_auth = False
        stream = self.stream()
        stream.start()
        try:
            await wait_for(lambda: stream.live)
            for ws in list(self.wings.sockets):
                await ws.close()
            await wait_for(lambda: self.wings.connections == 2 and stream.live)
            self.assertEqual(self.wings.tokens, ["token-1", "token-2"])
            self.assertEqual(self.changes, [True, False, True])
        finally:
            await stream.stop()

    async def test_rejected_token_is_not_live_and_retried(self):
        self.wings.valid = set()
        stream = self.stream()
        stream.start()
        try:
            await wait_for(lambda: self.wings.connections >= 2)
            self.assertFalse(stream.live)
            self.assertEqual(self.lines, [])
        finally:
            await stream.stop()

    async def test_wrong_origin_is_refused(self):
        async def credentials():
            return {"socket": self.wings.url, "token": "token-1", "origin": "https://elsewhere.example"}

        stream = self.stream(credentials)
        stream.start()
        try:
            await asyncio.sleep(0.1)
            self.assertFalse(stream.live)
            self.assertEqual(self.wings.connections, 0)
        finally:
            await stream.stop()


class AgentClientWebsocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_are_a_retried_signed_get(self):
        session = FakeSession(failures=1)
        client = AgentClient(ORIGIN, "agent-id", "secret")
        self.assertEqual(await client.websocket(session, "uuid-1"), {"ok": True})
        self.assertEqual(len(session.nonces), 2)


def binding():
    return {
        "chat_enabled": True, "chat_channel_id": "555", "discord_guild_id": "777", "guild_id": 1,
        "roles": [], "dialect": resolve("minecraft-java"), "event_colors": None,
    }


class BotStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.wings = FakeWings()
        self.wings.expire_after_auth = False
        await self.wings.start()
        self.issued = 0

        async def websocket(_session, uuid):
            self.issued += 1
            return {"socket": self.wings.url, "token": f"token-{self.issued}", "origin": ORIGIN}

        self.client = SimpleNamespace(
            websocket=AsyncMock(side_effect=websocket), logs_batch=AsyncMock(return_value={"uuid-1": []}), logs=AsyncMock(),
        )
        self.deliver = AsyncMock()
        self.patches = [
            patch.object(bot, "AGENT_CLIENT", self.client),
            patch.object(bot, "AGENT_SERVER_UUIDS", {"abcd1234": "uuid-1"}),
            patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding()]}),
            patch.object(bot, "get_http_session", AsyncMock()),
            patch.object(bot, "CONSOLE_TAILERS", {}),
            patch.object(bot, "PLAYER_TRACKERS", {}),
            patch.object(bot, "STATUS_CACHE", {}),
            patch.object(bot, "CONSOLE_RELAY_TARGETS", {"abcd1234"}),
            patch.object(bot, "CONSOLE_RELAY_ACTIVE", set()),
            patch.object(bot, "CONSOLE_RELAY_PROBLEMS", {}),
            patch.object(bot, "CONSOLE_BATCH_SUPPORTED", True),
            patch.object(bot, "CONSOLE_STREAMS", {}),
            patch.object(bot, "CONSOLE_STREAM_BUDGETS", {}),
            patch.object(bot, "WS_SESSION", None),
            patch.object(bot, "deliver_game_event", self.deliver),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await bot.stop_console_streams()
        for item in reversed(self.patches):
            item.stop()
        await self.wings.stop()

    async def test_polling_skips_a_live_stream_and_resumes_when_it_drops(self):
        with patch.object(bot, "CONSOLE_MODE", "auto"):
            await bot.relay_console_events.coro()  # the stream is still connecting: polled
            self.assertEqual(self.client.logs_batch.await_count, 1)
            stream = bot.CONSOLE_STREAMS["abcd1234"]
            stream._min_backoff = stream._max_backoff = 0.05
            await wait_for(lambda: stream.live and self.deliver.await_count == 1)
            self.assertEqual(self.deliver.await_args.args[2].kind, "chat")
            self.assertIn("abcd1234", bot.CONSOLE_RELAY_ACTIVE)

            await bot.relay_console_events.coro()
            self.assertEqual(self.client.logs_batch.await_count, 1)  # the websocket covers it

            await self.wings.drop()
            await wait_for(lambda: not stream.live)
            with self.assertLogs("ptero-bot", level="INFO"):
                await bot.relay_console_events.coro()
            self.assertEqual(self.client.logs_batch.await_count, 2)
            self.assertEqual(self.deliver.await_count, 1)  # the new polling baseline re-posts nothing

    async def test_poll_mode_never_opens_a_websocket(self):
        with patch.object(bot, "CONSOLE_MODE", "poll"):
            await bot.relay_console_events.coro()
            await bot.relay_console_events.coro()
        self.assertEqual(bot.CONSOLE_STREAMS, {})
        self.client.websocket.assert_not_awaited()
        self.assertEqual(self.client.logs_batch.await_count, 2)

    async def test_streams_stop_for_servers_no_longer_relayed(self):
        with patch.object(bot, "CONSOLE_MODE", "auto"):
            await bot.relay_console_events.coro()
            stream = bot.CONSOLE_STREAMS["abcd1234"]
            await wait_for(lambda: stream.live)
            with patch.object(bot, "AGENT_BINDINGS", {}):
                await bot.relay_console_events.coro()
        self.assertEqual(bot.CONSOLE_STREAMS, {})
        self.assertFalse(stream.running)

    async def test_websocket_mode_never_polls(self):
        with patch.object(bot, "CONSOLE_MODE", "websocket"):
            await bot.relay_console_events.coro()
        self.client.logs_batch.assert_not_awaited()
        self.client.logs.assert_not_awaited()
        self.assertIn("abcd1234", bot.CONSOLE_STREAMS)


if __name__ == "__main__":
    unittest.main()
