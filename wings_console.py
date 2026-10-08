"""Live console output from the Wings websocket, the same stream the panel's console page uses."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import aiohttp

logger = logging.getLogger("ptero-bot")

Credentials = Callable[[], Awaitable[dict]]
LinesCallback = Callable[[list[str]], Awaitable[None]]
LiveCallback = Callable[[bool, str | None], None]


class StreamError(Exception):
    """Wings rejected the socket or its token."""


class ConsoleStream:
    """One Wings console websocket that reconnects and renews its token on its own.

    Credentials are fetched for every connection: Wings rejects tokens issued before
    it last started, so a cached token would fail after a Wings restart.
    """

    def __init__(
        self, name: str, credentials: Credentials, on_lines: LinesCallback, *,
        session: Callable[[], Awaitable[aiohttp.ClientSession]], origin: str | None = None,
        on_live: LiveCallback | None = None, heartbeat: float = 30.0,
        min_backoff: float = 1.0, max_backoff: float = 60.0,
    ) -> None:
        self.name = name
        self._credentials = credentials
        self._on_lines = on_lines
        self._session = session
        self._origin = origin
        self._on_live = on_live
        self._heartbeat = heartbeat
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._task: asyncio.Task | None = None
        self._problem: str | None = None
        self.live = False
        self.authentications = 0
        self._authenticated = False
        self._auth_sent = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._run(), name=f"wings-console-{self.name}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._set_live(False, "stopped")

    def _set_live(self, live: bool, reason: str | None = None) -> None:
        if live == self.live:
            return
        self.live = live
        if self._on_live is not None:
            self._on_live(live, reason)

    async def _run(self) -> None:
        delay = self._min_backoff
        while True:
            connected_at = time.monotonic()
            try:
                await self._connect()
                reason = "the connection was closed"
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as exc:
                reason = f"Wings refused the connection with HTTP {exc.status}"
                if exc.status == 403:
                    reason += " (Wings only accepts the panel URL from its config.yml `remote` as Origin)"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            was_up = self._authenticated
            self._set_live(False, reason)
            if not was_up and reason != self._problem:
                logger.warning("Wings websocket for %s is unavailable: %s", self.name, reason)
            self._problem = None if was_up else reason
            # A connection that stayed up for a while starts the backoff over; one that keeps
            # dropping right after connecting backs off like a failed connection.
            if was_up and time.monotonic() - connected_at >= self._max_backoff:
                delay = self._min_backoff
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_backoff)

    async def _connect(self) -> None:
        self._authenticated = False
        credentials = await self._credentials()
        session = await self._session()
        origin = credentials.get("origin") or self._origin
        async with session.ws_connect(
            str(credentials["socket"]), origin=origin or None, heartbeat=self._heartbeat, max_msg_size=1 << 20,
        ) as ws:
            await self._authenticate(ws, credentials)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(ws, message.data)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    raise ws.exception() or StreamError("websocket error")
            if ws.close_code not in (None, 1000):
                raise StreamError(f"closed by Wings with code {ws.close_code}")

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse, credentials: dict) -> None:
        self._auth_sent = time.monotonic()
        await ws.send_json({"event": "auth", "args": [str(credentials["token"])]})

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        event = data.get("event")
        args = data.get("args") or []
        if event == "auth success":
            self.authentications += 1
            self._authenticated = True
            self._problem = None
            self._set_live(True)
        elif event == "console output":
            lines = [line.rstrip("\r") for arg in args for line in str(arg).split("\n")]
            if lines:
                try:
                    await self._on_lines(lines)
                except Exception:
                    # A relay bug must not turn into a reconnect loop.
                    logger.exception("Could not relay console output of %s", self.name)
        elif event == "token expiring":
            # Wings warns about a minute ahead; a failed renewal is retried on its next warning.
            try:
                await self._authenticate(ws, await self._credentials())
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as exc:
                logger.debug("Wings websocket token renewal for %s failed: %s", self.name, exc)
        elif event == "token expired":
            # Wings sends nothing more until a new token arrives; a failure here reconnects.
            await self._authenticate(ws, await self._credentials())
        elif event == "jwt error":
            reason = f"Wings rejected the token: {' '.join(map(str, args)) or 'unknown error'}"
            if not self._authenticated:
                raise StreamError(reason)
            # The token lapsed on an authenticated socket (Wings answers every event with this
            # error until a new token arrives): polling covers the gap while it is renewed.
            self._set_live(False, reason)
            if time.monotonic() - self._auth_sent >= 10:
                await self._authenticate(ws, await self._credentials())
