import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from adapters.engine import GameEvent
from adapters.registry import resolve
from chat_relay import RoleInfo, can_chat, chat_identity, chat_role_ids, event_color, webhook_username

ADMIN = RoleInfo("10", "Admin", 10, 0xFF0000, True)
VIP = RoleInfo("20", "VIP", 5, 0x00FF00, False)
MEMBER = RoleInfo("30", "Member", 1, 0, True)


class IdentityTests(unittest.TestCase):
    def test_highest_hoisted_role_and_display_color_by_default(self):
        self.assertEqual(chat_identity([MEMBER, VIP, ADMIN], []), ("Admin", 0xFF0000))
        self.assertEqual(chat_identity([MEMBER, VIP], []), ("Member", 0x00FF00))
        self.assertEqual(chat_identity([], []), (None, None))

    def test_configured_label_and_color_win(self):
        mappings = [{"discord_role_id": "20", "chat_label": "Supporter", "chat_color": "#123456"}]
        self.assertEqual(chat_identity([MEMBER, VIP, ADMIN], mappings), ("Supporter", 0x123456))

    def test_configured_color_without_label_uses_role_name(self):
        mappings = [{"discord_role_id": "30", "chat_label": None, "chat_color": "#ABCDEF"}]
        self.assertEqual(chat_identity([MEMBER], mappings), ("Member", 0xABCDEF))

    def test_chat_permission(self):
        self.assertTrue(can_chat({"1"}, [{"discord_role_id": "2", "can_chat": False}]))
        self.assertFalse(can_chat({"1"}, [{"discord_role_id": "2", "can_chat": True}]))
        self.assertTrue(can_chat({"1", "2"}, [{"discord_role_id": "2", "can_chat": True}]))

    def test_chat_for_everyone_role(self):
        # Chat ticked for @everyone (role ID = guild ID) lets members without other roles chat.
        everyone = [{"discord_role_id": "900", "can_chat": True}]
        self.assertEqual(chat_role_ids([], 900), {"900"})
        self.assertTrue(can_chat(chat_role_ids([], 900), everyone))
        self.assertTrue(can_chat(chat_role_ids([MEMBER], "900"), everyone))
        self.assertFalse(can_chat(chat_role_ids([], 901), everyone))

    def test_webhook_username_and_event_colors(self):
        self.assertEqual(webhook_username("Discord_Fan", "VIP"), "[VIP] •_Fan")
        self.assertEqual(webhook_username("  "), "Player")
        self.assertEqual(event_color("join", None), 0x57F287)
        self.assertEqual(event_color("server_ready", {"server": "#000001"}), 1)


def fake_message(content="hello", channel_id=555, guild_id=777, roles=()):
    author = SimpleNamespace(id=42, bot=False, display_name="Ana", roles=list(roles))
    return SimpleNamespace(
        author=author, webhook_id=None, guild=SimpleNamespace(id=guild_id), channel=SimpleNamespace(id=channel_id),
        clean_content=content, attachments=[], stickers=[], add_reaction=AsyncMock(),
    )


def binding(**overrides):
    data = {
        "chat_enabled": True, "chat_channel_id": "555", "discord_guild_id": "777", "guild_id": 1,
        "roles": [], "dialect": resolve("minecraft-java"), "event_colors": None,
    }
    data.update(overrides)
    return data


class OnMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = SimpleNamespace(chat=AsyncMock(), logs=AsyncMock())
        self.patches = [
            patch.object(bot, "AGENT_CLIENT", self.client),
            patch.object(bot, "AGENT_SERVER_UUIDS", {"abcd1234": "abcd1234-0000-0000-0000-000000000000"}),
            patch.object(bot, "get_http_session", AsyncMock()),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def test_message_in_linked_channel_is_sent_as_tellraw(self):
        with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding()]}):
            await bot.on_message(fake_message("**hi** there"))
        self.client.chat.assert_awaited_once()
        _, uuid, commands, actor = self.client.chat.await_args.args
        self.assertEqual(uuid, "abcd1234-0000-0000-0000-000000000000")
        self.assertEqual(actor.guild_id, "777")
        components = json.loads(commands[0][len("tellraw @a "):])
        self.assertEqual(components[-1]["text"], ": hi there")

    async def test_other_channel_disabled_chat_and_missing_role_are_ignored(self):
        for bindings in (
            [binding(chat_channel_id="999")],
            [binding(chat_enabled=False)],
            [binding(roles=[{"discord_role_id": "1", "can_chat": True}])],
        ):
            with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": bindings}):
                await bot.on_message(fake_message())
        self.client.chat.assert_not_awaited()

    async def test_disabled_features_are_respected(self):
        with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding(disabled_features=["chat_out"])]}):
            await bot.on_message(fake_message())
        self.client.chat.assert_not_awaited()
        red = SimpleNamespace(id=10, name="Admin", position=5, color=SimpleNamespace(value=0xFF0000), hoist=True, is_default=lambda: False)
        with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding(disabled_features=["colors_out"])]}):
            await bot.on_message(fake_message(roles=[red]))
        components = json.loads(self.client.chat.await_args.args[2][0][len("tellraw @a "):])
        self.assertEqual(components[2], {"text": "[Admin] ", "color": "white"})

    async def test_bot_and_webhook_messages_are_ignored(self):
        message = fake_message()
        message.webhook_id = 1
        with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding()]}):
            await bot.on_message(message)
        self.client.chat.assert_not_awaited()

    async def test_failure_adds_warning_reaction(self):
        self.client.chat.side_effect = bot.aiohttp.ClientError("down")
        message = fake_message()
        with patch.object(bot, "AGENT_BINDINGS", {"abcd1234": [binding()]}):
            await bot.on_message(message)
        message.add_reaction.assert_awaited_once_with("⚠️")


class ConsoleRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_console_lines_are_parsed_and_delivered(self):
        client = SimpleNamespace(logs=AsyncMock(side_effect=[
            ["[12:00:00] [Server thread/INFO]: old line"],
            ["[12:00:00] [Server thread/INFO]: old line",
             "[12:00:01] [Server thread/INFO]: Steve joined the game",
             "[12:00:02] [Server thread/INFO]: <Steve> hi"],
        ]))
        deliver = AsyncMock()
        with (
            patch.object(bot, "AGENT_CLIENT", client),
            patch.object(bot, "AGENT_SERVER_UUIDS", {"abcd1234": "uuid"}),
            patch.object(bot, "get_http_session", AsyncMock()),
            patch.object(bot, "CONSOLE_TAILERS", {}),
            patch.object(bot, "PLAYER_TRACKERS", {}),
            patch.object(bot, "STATUS_CACHE", {}),
            patch.object(bot, "deliver_game_event", deliver),
        ):
            await bot.relay_server_console("abcd1234", [binding()])
            deliver.assert_not_awaited()
            await bot.relay_server_console("abcd1234", [binding()])
            kinds = [call.args[2].kind for call in deliver.await_args_list]
            self.assertEqual(kinds, ["join", "chat"])
            self.assertEqual(bot.PLAYER_TRACKERS["abcd1234"].names, ["Steve"])

    async def test_disabled_event_kinds_are_not_delivered_but_players_tracked(self):
        client = SimpleNamespace(logs=AsyncMock(side_effect=[
            [],
            ["[12:00:01] [Server thread/INFO]: Steve joined the game", "[12:00:02] [Server thread/INFO]: <Steve> hi"],
        ]))
        deliver = AsyncMock()
        with (
            patch.object(bot, "AGENT_CLIENT", client),
            patch.object(bot, "AGENT_SERVER_UUIDS", {"abcd1234": "uuid"}),
            patch.object(bot, "get_http_session", AsyncMock()),
            patch.object(bot, "CONSOLE_TAILERS", {}),
            patch.object(bot, "PLAYER_TRACKERS", {}),
            patch.object(bot, "STATUS_CACHE", {}),
            patch.object(bot, "deliver_game_event", deliver),
        ):
            disabled = binding(disabled_features=["join_leave"])
            await bot.relay_server_console("abcd1234", [disabled])
            await bot.relay_server_console("abcd1234", [disabled])
            self.assertEqual(bot.PLAYER_TRACKERS["abcd1234"].names, ["Steve"])
        self.assertEqual([call.args[2].kind for call in deliver.await_args_list], ["chat"])

    async def test_offline_server_is_not_polled(self):
        client = SimpleNamespace(logs=AsyncMock())
        with (
            patch.object(bot, "AGENT_CLIENT", client),
            patch.object(bot, "STATUS_CACHE", {"abcd1234": (0, {"current_state": "offline"})}),
            patch.object(bot, "CONSOLE_TAILERS", {}),
            patch.object(bot, "PLAYER_TRACKERS", {}),
        ):
            await bot.relay_server_console("abcd1234", [binding()])
        client.logs.assert_not_awaited()

    async def test_chat_event_uses_webhook_identity(self):
        channel = bot.discord.TextChannel.__new__(bot.discord.TextChannel)
        relay = SimpleNamespace(post_chat=AsyncMock(), post_event=AsyncMock())
        with (
            patch.object(bot.bot, "get_channel", lambda _id: channel),
            patch.object(bot, "CHAT_RELAY", relay),
        ):
            await bot.deliver_game_event("abcd1234", binding(), GameEvent("chat", "Steve", "hi"))
            await bot.deliver_game_event("abcd1234", binding(), GameEvent("join", "Steve"))
            await bot.deliver_game_event("abcd1234", binding(), GameEvent("broadcast", None, "Restart *soon*"))
            await bot.deliver_game_event("abcd1234", binding(), GameEvent("advancement", "Steve", "Stone Age"))
        broadcast, advancement = relay.post_event.await_args_list[-2:]
        self.assertEqual(broadcast.args[1:], (r"📢 Restart \*soon\*", 0xEB459E))
        self.assertIn("Stone Age", advancement.args[1])
        self.assertEqual(advancement.args[2], 0xFEE75C)
        relay.post_chat.assert_awaited_once_with(
            channel, "Steve", "hi", rank=None, avatar_url="https://mc-heads.net/avatar/Steve/64"
        )
        text, color = relay.post_event.await_args_list[0].args[1:]
        self.assertIn("Steve", text)
        self.assertEqual(color, 0x57F287)


if __name__ == "__main__":
    unittest.main()


class HeartbeatLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_blips_warn_and_only_repeated_failures_are_errors(self):
        client = SimpleNamespace(heartbeat=AsyncMock(side_effect=bot.aiohttp.ClientOSError(104, "Connection reset by peer")))
        with (
            patch.object(bot, "AGENT_CLIENT", client),
            patch.object(bot, "HEARTBEAT_FAILURES", 0),
            patch.object(bot, "get_http_session", AsyncMock()),
            patch.object(bot.bot, "is_ready", lambda: True),
            patch.object(type(bot.bot), "guilds", new=[]),
            self.assertLogs("ptero-bot", level="WARNING") as logs,
        ):
            for _ in range(3):
                await bot.sync_agent_heartbeat.coro()
        levels = [record.levelname for record in logs.records]
        self.assertEqual(levels, ["WARNING", "WARNING", "ERROR"])
        self.assertTrue(all(record.exc_info is None for record in logs.records))


class RelayHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_chat_markdown_is_escaped(self):
        from chat_relay import ChatRelay

        relay = ChatRelay(SimpleNamespace(user=SimpleNamespace(id=1)))
        relay.webhook_for = AsyncMock(return_value=None)
        channel = SimpleNamespace(send=AsyncMock(), name="chat")
        await relay.post_chat(channel, "Steve", "[free nitro](https://evil.example) **bold**")
        content = channel.send.await_args.args[0]
        self.assertIn("\[free nitro](", content)  # an escaped "[" is no longer a masked link
        self.assertIn("\*\*bold\*\*", content)

    async def test_avatar_url_encodes_player_names(self):
        channel = bot.discord.TextChannel.__new__(bot.discord.TextChannel)
        relay = SimpleNamespace(post_chat=AsyncMock(), post_event=AsyncMock())
        with patch.object(bot.bot, "get_channel", lambda _id: channel), patch.object(bot, "CHAT_RELAY", relay):
            await bot.deliver_game_event("abcd1234", binding(), GameEvent("chat", "a/../b?x", "hi"))
        self.assertTrue(relay.post_chat.await_args.kwargs["avatar_url"].endswith("/avatar/a%2F..%2Fb%3Fx/64"))


class ConsoleBatchTests(unittest.IsolatedAsyncioTestCase):
    def patches(self, client):
        servers = {"aaaa0001": "uuid-1", "aaaa0002": "uuid-2", "aaaa0003": "uuid-3"}
        return [
            patch.object(bot, "AGENT_CLIENT", client),
            patch.object(bot, "AGENT_SERVER_UUIDS", servers),
            patch.object(bot, "AGENT_BINDINGS", {sid: [binding()] for sid in servers}),
            patch.object(bot, "get_http_session", AsyncMock()),
            patch.object(bot, "CONSOLE_TAILERS", {}),
            patch.object(bot, "PLAYER_TRACKERS", {}),
            patch.object(bot, "STATUS_CACHE", {}),
            patch.object(bot, "CONSOLE_RELAY_TARGETS", set()),
            patch.object(bot, "CONSOLE_BATCH_SUPPORTED", True),
            patch.object(bot, "CONSOLE_MODE", "poll"),
        ]

    async def test_all_relayed_consoles_are_read_in_one_request(self):
        client = SimpleNamespace(
            logs_batch=AsyncMock(return_value={"uuid-1": ["a"], "uuid-2": ["b"], "uuid-3": ["c"]}),
            logs=AsyncMock(),
        )
        active = self.patches(client)
        for item in active:
            item.start()
        try:
            await bot.relay_console_events.coro()
        finally:
            for item in reversed(active):
                item.stop()
        client.logs_batch.assert_awaited_once()
        self.assertEqual(sorted(client.logs_batch.await_args.args[1]), ["uuid-1", "uuid-2", "uuid-3"])
        client.logs.assert_not_awaited()

    async def test_older_panel_falls_back_to_one_request_per_server(self):
        not_found = bot.aiohttp.ClientResponseError(SimpleNamespace(real_url="x"), (), status=404)
        client = SimpleNamespace(logs_batch=AsyncMock(side_effect=not_found), logs=AsyncMock(return_value=[]))
        active = self.patches(client)
        for item in active:
            item.start()
        try:
            await bot.relay_console_events.coro()
            self.assertFalse(bot.CONSOLE_BATCH_SUPPORTED)
        finally:
            for item in reversed(active):
                item.stop()
        self.assertEqual(client.logs.await_count, 3)


class DiagnosticsTests(unittest.TestCase):
    def test_problems_are_reported_with_server_uuid_and_kept_since(self):
        bindings = {
            "aaaa0001": [binding(chat_channel_id=None)],
            "aaaa0002": [binding(chat_channel_id="555")],
        }
        with (
            patch.object(bot, "AGENT_BINDINGS", bindings),
            patch.object(bot, "AGENT_SERVER_UUIDS", {"aaaa0001": "uuid-1", "aaaa0002": "uuid-2"}),
            patch.object(bot, "CONSOLE_RELAY_PROBLEMS", {"aaaa0002": "ConsoleUnavailable: offline"}),
            patch.object(bot, "CHAT_OUT_PROBLEMS", {}),
            patch.object(bot, "PROBLEM_SINCE", {}),
            patch.object(bot.bot, "get_channel", lambda _id: None),
        ):
            first = bot.collect_diagnostics()
            second = bot.collect_diagnostics()
        kinds = {(p["kind"], p["server"]) for p in first["problems"]}
        self.assertEqual(kinds, {("setup", "uuid-1"), ("console", "uuid-2"), ("channel", "uuid-2")})
        self.assertEqual([p["since"] for p in first["problems"]], [p["since"] for p in second["problems"]])

    def test_bot_info_reports_identity_and_intent(self):
        info = bot.bot_info()
        self.assertIn("message_content", info)
        self.assertTrue(info["message_content"])
