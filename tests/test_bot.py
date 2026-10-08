import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot

ACTOR = bot.DiscordActor("10", "20", ("30",))


class ConfigTests(unittest.TestCase):
    def test_missing_config_is_created_with_complete_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            old_path = bot.CONFIG_FILE
            bot.CONFIG_FILE = str(Path(directory) / "config.json")
            try:
                config = bot.load_config()
            finally:
                bot.CONFIG_FILE = old_path

        self.assertEqual(set(config), set(bot.DEFAULT_CONFIG))

    def test_settings_of_older_agents_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"servers": {"4cb49f72": "Old"}, "uptime_data": {"4cb49f72": {"total_seconds": 5}}}', encoding="utf-8")
            with patch.object(bot, "CONFIG_FILE", str(path)):
                config = bot.load_config()

        self.assertEqual(config, {"status_messages": {}, "uptime_data": {"4cb49f72": {"total_seconds": 5}}})

    def test_invalid_json_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            old_path = bot.CONFIG_FILE
            bot.CONFIG_FILE = str(path)
            try:
                with self.assertRaisesRegex(RuntimeError, "Could not read the configuration"):
                    bot.load_config()
            finally:
                bot.CONFIG_FILE = old_path


class HelperTests(unittest.TestCase):
    def test_manage_view_offers_power_refresh_and_settings(self):
        view = bot.ServerManageView(1, [("4cb49f72", "Test")])
        labels = {child.label for child in view.children if isinstance(child, bot.discord.ui.Button)}
        self.assertEqual(labels, {"Start", "Stop", "Restart", "Refresh", "Settings"})

    def test_normalize_server_identifier(self):
        self.assertEqual(bot.normalize_server_identifier("4CB49F72"), "4cb49f72")
        self.assertEqual(
            bot.normalize_server_identifier("4cb49f72-0d53-4cae-90f8-ec7f4f0135ce"),
            "4cb49f72",
        )
        self.assertEqual(
            bot.normalize_server_identifier("https://panel.example.com/server/4CB49F72/files"),
            "4cb49f72",
        )
        with self.assertRaises(ValueError):
            bot.normalize_server_identifier("not-an-id")

    def test_format_uptime(self):
        self.assertEqual(bot.format_uptime(90_061), "1d 1h 1m")

    def test_environment_validation_lists_missing_values(self):
        with (
            patch.object(bot, "PANEL_PUBLIC_URL", ""), patch.object(bot, "PTEROSYNC_AGENT_ID", ""),
            patch.object(bot, "PTEROSYNC_AGENT_SECRET", ""), patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "DISCORD_TOKEN.*PANEL_PUBLIC_URL.*PTEROSYNC_AGENT_ID.*PTEROSYNC_AGENT_SECRET"):
                bot.validate_environment()

    def test_unlinked_servers_are_refused_before_any_request(self):
        with patch.object(bot, "AGENT_SERVER_UUIDS", {"4cb49f72": "4cb49f72-0d53-4cae-90f8-ec7f4f0135ce"}):
            self.assertEqual(bot.server_uuid("4CB49F72"), "4cb49f72-0d53-4cae-90f8-ec7f4f0135ce")
            with self.assertRaises(PermissionError):
                bot.server_uuid("deadbeef")

    def test_servers_are_only_visible_in_their_discord_to_view_roles(self):
        bindings = {"4cb49f72": [{"discord_guild_id": "10", "roles": [
            {"discord_role_id": "30", "can_view": True}, {"discord_role_id": "31", "can_view": False},
        ]}]}

        def member(guild_id, *role_ids):
            return SimpleNamespace(guild_id=guild_id, user=SimpleNamespace(roles=[SimpleNamespace(id=int(r)) for r in role_ids]))

        with patch.object(bot, "AGENT_BINDINGS", bindings):
            self.assertTrue(bot.has_server_access(member(10, "30"), "4cb49f72"))
            self.assertFalse(bot.has_server_access(member(10, "31"), "4cb49f72"))
            # Another customer's Discord server with the same role ID sees nothing.
            self.assertFalse(bot.has_server_access(member(11, "30"), "4cb49f72"))
            self.assertFalse(bot.has_server_access(member(10, "30"), "deadbeef"))


class AsyncHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_does_not_send_duplicate_signal_when_already_running(self):
        with (
            patch.object(bot, "get_server_status", AsyncMock(return_value={"current_state": "running"})),
            patch.object(bot, "send_power_action", AsyncMock()) as send_power,
        ):
            success, state = await bot.execute_power_and_wait("4cb49f72", "start", ACTOR)

        self.assertTrue(success)
        self.assertEqual(state, "running")
        send_power.assert_not_awaited()

    async def test_power_action_waits_for_target_state(self):
        statuses = [
            {"current_state": "offline"},
            {"current_state": "starting"},
            {"current_state": "running"},
        ]
        with (
            patch.object(bot, "get_server_status", AsyncMock(side_effect=statuses)),
            patch.object(bot, "send_power_action", AsyncMock()) as send_power,
            patch.object(bot.asyncio, "sleep", AsyncMock()),
        ):
            success, state = await bot.execute_power_and_wait("4cb49f72", "start", ACTOR)

        self.assertTrue(success)
        self.assertEqual(state, "running")
        send_power.assert_awaited_once_with("4cb49f72", "start", ACTOR)


class StateNotificationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def channel():
        channel = MagicMock(spec=bot.discord.TextChannel)
        channel.send = AsyncMock()
        return channel

    async def notify(self, bindings, channels, guilds=None):
        guilds = guilds if guilds is not None else {"1": {"id": 1, "locale": "en", "notification_channel_id": "900"}}
        with (
            patch.object(bot, "AGENT_BINDINGS", {"4cb49f72": bindings}),
            patch.object(bot, "AGENT_GUILDS", guilds),
            patch.object(bot, "AGENT_SERVER_NAMES", {"4cb49f72": "Survival"}),
            patch.object(bot.bot, "get_channel", MagicMock(side_effect=lambda cid: channels.get(cid))) as get_channel,
        ):
            await bot.notify_state_transition("4cb49f72", "offline", "running")
        return get_channel

    async def test_posts_to_the_channel_resolved_for_the_binding(self):
        own, guild_default = self.channel(), self.channel()
        await self.notify([{"guild_id": 1, "notification_channel_id": "555"}], {555: own, 900: guild_default})

        own.send.assert_awaited_once()
        self.assertIn("Survival", own.send.await_args.args[0])
        self.assertIn("RUNNING", own.send.await_args.args[0])
        guild_default.send.assert_not_awaited()

    async def test_binding_without_a_channel_posts_nothing(self):
        guild_default = self.channel()
        get_channel = await self.notify([{"guild_id": 1, "notification_channel_id": None}], {900: guild_default})

        get_channel.assert_not_called()
        guild_default.send.assert_not_awaited()

    async def test_older_panels_fall_back_to_the_guild_channel(self):
        guild_default = self.channel()
        await self.notify([{"guild_id": 1}], {900: guild_default})

        guild_default.send.assert_awaited_once()

    async def test_console_server_status_toggle_does_not_hide_notifications(self):
        # "Server start / stop" switches the console lines in the chat channel, not these.
        own = self.channel()
        await self.notify(
            [{"guild_id": 1, "notification_channel_id": "555", "disabled_features": ["server_status"]}], {555: own},
        )

        own.send.assert_awaited_once()

    async def test_each_binding_posts_once_in_its_guild_locale(self):
        first, second = self.channel(), self.channel()
        guilds = {"1": {"id": 1, "locale": "en"}, "2": {"id": 2, "locale": "de"}}
        with patch.object(bot, "translate", MagicMock(return_value="text")) as translate:
            await self.notify(
                [{"guild_id": 1, "notification_channel_id": "555"}, {"guild_id": 2, "notification_channel_id": "556"}],
                {555: first, 556: second}, guilds,
            )

        first.send.assert_awaited_once()
        second.send.assert_awaited_once()
        self.assertEqual([call.args[1] for call in translate.call_args_list], ["en", "de"])


class StatusOverviewTests(unittest.TestCase):
    def test_presence_names_no_servers(self):
        text = bot.presence_text(["a", "b", "c"], {"a": "running", "b": "running", "c": "offline"}, {"a": 3, "b": None, "c": 9})
        self.assertEqual(text, "3 servers · 2 online · 3 players")

    def test_presence_without_player_counts_or_servers(self):
        self.assertEqual(bot.presence_text(["a"], {"a": None}, {"a": None}), "1 server · 0 online")
        self.assertEqual(bot.presence_text([], {}, {}), "🔧 no servers")

    def test_overview_lines(self):
        lines = bot.status_overview_lines([("Survival*", "running", 2, True), ("Creative", None, None, False)], "en")
        self.assertEqual(lines, [r"🟢 **Survival\*** · Running · 👥 2 · 💬 Chat relay", "⚪ **Creative** · Unknown"])


if __name__ == "__main__":
    unittest.main()
