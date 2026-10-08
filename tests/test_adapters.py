import json
import unittest

from adapters import formatters
from adapters.console import ConsoleTailer, PlayerTracker
from adapters.engine import ChatOut, GameEvent, dialect_for, parse_line, render_outgoing
from adapters.registry import catalog, get_preset, resolve, suggest


def preset(adapter_id):
    return resolve(adapter_id)


class RegistryTests(unittest.TestCase):
    def test_catalog_lists_presets_with_capabilities(self):
        items = {item["id"]: item for item in catalog()}
        self.assertIn("generic", items)
        self.assertIn("chat_in", items["minecraft-java"]["capabilities"])
        self.assertIn("colors_out", items["minecraft-java"]["capabilities"])
        self.assertNotIn("chat_in", items["minecraft-bedrock"]["capabilities"])
        self.assertNotIn("chat_out", items["valheim"]["capabilities"])

    def test_unknown_adapter_is_disabled(self):
        self.assertIsNone(resolve("unsupported-game"))
        self.assertIsNone(resolve(None))

    def test_overrides_merge_and_invalid_regex_is_skipped(self):
        dialect = resolve("generic", {"in": {"patterns": {"chat": "^(?P<player>\\w+): (?P<message>.+)$", "join": "re:("}}})
        self.assertEqual(parse_line(dialect, "Bob: hi"), GameEvent("chat", "Bob", "hi", None, "Bob: hi"))
        self.assertNotIn("join_leave", dialect.capabilities)

    def test_null_override_removes_key(self):
        dialect = resolve("minecraft-java", {"out": {"template": None}})
        self.assertNotIn("chat_out", dialect.capabilities)

    def test_catalog_reports_match_keywords(self):
        items = {item["id"]: item for item in catalog()}
        self.assertIn("paper", items["minecraft-java"]["match"]["eggs"])
        self.assertEqual(items["generic"]["match"], {"eggs": [], "images": []})


class SuggestTests(unittest.TestCase):
    def test_minecraft_java_eggs(self):
        for egg in ("Paper", "Purpur", "Forge Minecraft", "NeoForge", "Fabric", "Quilt", "Vanilla Minecraft",
                    "Sponge (SpongeVanilla)", "Mohist", "Bukkit", "Spigot", "PaperMC"):
            self.assertEqual(suggest(egg, "ghcr.io/pterodactyl/yolks:java_21"), "minecraft-java", egg)

    def test_proxies_and_other_eggs_are_not_suggested(self):
        for egg in ("BungeeCord", "Velocity", "Waterfall", "Trust Server", "Paperless", "Ark: Survival Evolved", None):
            self.assertIsNone(suggest(egg, "ghcr.io/pterodactyl/yolks:java_21"), egg)

    def test_other_games(self):
        cases = {
            "Vanilla Bedrock": "minecraft-bedrock",
            "Minecraft Bedrock Dedicated Server (BDS)": "minecraft-bedrock",
            "Terraria Vanilla": "terraria",
            "TShock": "terraria",
            "tModLoader": "terraria",
            "Rust": "rust",
            "Rust Autowipe": "rust",
            "Counter-Strike: Global Offensive": "source",
            "Counter Strike 2": "source",
            "Garrys Mod": "source",
            "Garry's Mod": "source",
            "Team Fortress 2": "source",
            "Left 4 Dead 2": "source",
            "Valheim Plus": "valheim",
        }
        for egg, expected in cases.items():
            self.assertEqual(suggest(egg, None), expected, egg)

    def test_image_is_used_when_the_egg_name_is_unknown(self):
        self.assertEqual(suggest("My custom egg", "ghcr.io/parkervcp/games:source"), "source")
        self.assertEqual(suggest("My custom egg", "ghcr.io/pterodactyl/games:rust"), "rust")
        self.assertIsNone(suggest("My custom egg", "ghcr.io/parkervcp/yolks:rust_latest"))

    def test_egg_wins_over_image(self):
        self.assertEqual(suggest("Paper", "ghcr.io/parkervcp/games:source"), "minecraft-java")


class TemplatePatternTests(unittest.TestCase):
    def dialect(self, **patterns):
        return resolve("generic", {"in": {"line_prefix": "auto", "patterns": patterns}})

    def test_template_matches_like_the_console_line(self):
        dialect = self.dialect(chat="<{player}> {message} || [{rank}] {player}: {message}", join="{player} joined the game")
        self.assertEqual(parse_line(dialect, "<Bob> hi there").message, "hi there")
        ranked = parse_line(dialect, "[VIP] Ana: hello")
        self.assertEqual((ranked.player, ranked.rank, ranked.message), ("Ana", "VIP", "hello"))
        self.assertEqual(parse_line(dialect, "Bob  joined   the game").kind, "join")
        self.assertIsNone(parse_line(dialect, "Bob joined the game!"))

    def test_regex_characters_in_templates_are_literal(self):
        dialect = self.dialect(server_ready="Done ({*})! For help{*}")
        self.assertEqual(parse_line(dialect, 'Done (3.1s)! For help, type "help"').kind, "server_ready")
        self.assertIsNone(parse_line(dialect, "Done 3.1s! For help"))

    def test_regex_mode_still_works(self):
        dialect = self.dialect(chat=r"re:(?P<player>\w+) says (?P<message>.+)", join=r"^(?P<player>\w+) arrived$")
        self.assertEqual(parse_line(dialect, "Bob says hi").player, "Bob")
        self.assertEqual(parse_line(dialect, "Bob arrived").kind, "join")

    def test_auto_prefix_handles_common_timestamps(self):
        dialect = self.dialect(chat="<{player}> {message}")
        for line in (
            "[12:00:00] [Server thread/INFO]: <Bob> hi",
            "[12:00:00 INFO]: <Bob> hi",
            "[2026-10-06 12:00:00:000 INFO] <Bob> hi",
            "10/06/2026 12:00:00: <Bob> hi",
            "2026-10-06T12:00:00Z INFO <Bob> hi",
            "12:00:00 <Bob> hi",
            "<Bob> hi",
        ):
            self.assertEqual(parse_line(dialect, line).player, "Bob", line)


class MinecraftJavaParsingTests(unittest.TestCase):
    def setUp(self):
        self.dialect = preset("minecraft-java")

    def parse(self, line):
        return parse_line(self.dialect, line)

    def test_vanilla_chat(self):
        event = self.parse("[12:34:56] [Server thread/INFO]: <Steve> hello there")
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Steve", "hello there"))

    def test_paper_and_not_secure_chat_with_ansi(self):
        event = self.parse("\x1b[0;37m[12:34:56 INFO]: [Not Secure] <Alex> hi\x1b[m")
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Alex", "hi"))

    def test_console_prompt_redraw_before_line(self):
        line = "\x1b[m>....\r\x1b[K\x1b[32m[00:00:38] [Server thread/INFO] [minecraft/MinecraftServer]: <Skwarc> aaaaa"
        event = self.parse(line)
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Skwarc", "aaaaa"))
        self.assertEqual(self.parse("[12:00:00] [Server thread/INFO]: <Steve> hi\r\n").message, "hi")

    def test_neoforge_prefix(self):
        event = self.parse("[12:34:56] [Server thread/INFO] [minecraft/MinecraftServer]: Steve joined the game")
        self.assertEqual((event.kind, event.player), ("join", "Steve"))

    def test_leave_death_ready(self):
        self.assertEqual(self.parse("[12:00:00] [Server thread/INFO]: Steve left the game").kind, "leave")
        death = self.parse("[12:00:00] [Server thread/INFO]: Steve was slain by Zombie")
        self.assertEqual((death.kind, death.message), ("death", "was slain by Zombie"))
        self.assertEqual(self.parse('[12:00:00] [Server thread/INFO]: Done (3.2s)! For help, type "help"').kind, "server_ready")

    def test_irrelevant_and_echoed_lines_are_ignored(self):
        self.assertIsNone(self.parse("[12:00:00] [Server thread/INFO]: Preparing spawn area: 50%"))
        self.assertIsNone(self.parse("[12:00:00] [Server thread/INFO]: [Server] [Discord] Bob: hi"))
        self.assertIsNone(self.parse("[12:00:00] [Server thread/INFO]: <Steve> [Discord] spoof"))


class ServerMessageTests(unittest.TestCase):
    def test_minecraft_say_advancement_and_stop(self):
        dialect = preset("minecraft-java")
        say = parse_line(dialect, "[12:00:00] [Server thread/INFO]: [Server] Restart in 5 minutes")
        self.assertEqual((say.kind, say.player, say.message), ("broadcast", "Server", "Restart in 5 minutes"))
        player_say = parse_line(dialect, "[23:17:24] [Server thread/INFO] [minecraft/MinecraftServer]: [Saco] aaaa")
        self.assertEqual((player_say.kind, player_say.player, player_say.message), ("broadcast", "Saco", "aaaa"))
        self.assertEqual(parse_line(dialect, "[12:00:00] [Server thread/INFO]: [Not Secure] <Saco> hi").kind, "chat")
        advancement = parse_line(dialect, "[12:00:00] [Server thread/INFO]: Steve has made the advancement [Stone Age]")
        self.assertEqual((advancement.kind, advancement.player, advancement.message), ("advancement", "Steve", "Stone Age"))
        self.assertEqual(parse_line(dialect, "[12:00:00] [Server thread/INFO]: Stopping the server").kind, "server_stop")
        self.assertIn("server_messages", dialect.capabilities)
        self.assertIn("advancements", dialect.capabilities)

    def test_server_message_mentioning_discord_is_kept(self):
        event = parse_line(preset("minecraft-java"), "[12:00:00] [Server thread/INFO]: [Server] Join our Discord!")
        self.assertEqual(event.message, "Join our Discord!")

    def test_relayed_messages_echoed_by_say_are_dropped(self):
        self.assertIsNone(parse_line(preset("terraria"), "<Server> [c/5865F2:(Discord)] [c/FFFFFF:Ana]: hi"))
        self.assertIsNone(parse_line(preset("rust"), "[CHAT] SERVER : <color=#5865F2>[Discord]</color> Ana: hi"))
        self.assertIsNone(parse_line(preset("source"), 'L 01/02/2024 - 12:00:00: "Console<0><Console><Console>" say "[Discord] Ana: hi"'))
        self.assertEqual(parse_line(preset("terraria"), "<Server> Blood moon tonight").kind, "broadcast")

    def test_server_stop_resets_players(self):
        tracker = PlayerTracker()
        tracker.apply(GameEvent("server_ready"))
        tracker.apply(GameEvent("join", "A"))
        tracker.apply(GameEvent("server_stop"))
        self.assertEqual((tracker.names, tracker.known), ([], False))


class OtherPresetParsingTests(unittest.TestCase):
    def test_terraria(self):
        dialect = preset("terraria")
        self.assertEqual(parse_line(dialect, "<Guide> hello").player, "Guide")
        self.assertEqual(parse_line(dialect, "Andrew has joined.").kind, "join")
        self.assertIsNone(parse_line(dialect, "<Server> [Discord] x"))

    def test_rust(self):
        dialect = preset("rust")
        event = parse_line(dialect, "[CHAT] Bob[76561198000000000] : gg")
        self.assertEqual((event.player, event.message), ("Bob", "gg"))
        join = parse_line(dialect, "1.2.3.4:5555/76561198000000000/Bob joined [windows/76561198000000000]")
        self.assertEqual((join.kind, join.player), ("join", "Bob"))

    def test_source(self):
        dialect = preset("source")
        line = 'L 01/02/2024 - 12:00:00: "Bob<2><STEAM_1:0:1><Red>" say "hello"'
        event = parse_line(dialect, line)
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Bob", "hello"))

    def test_valheim_death_and_spawn(self):
        dialect = preset("valheim")
        self.assertEqual(parse_line(dialect, "10/06/2026 12:00:00: Got character ZDOID from Ragnar : 0:0").kind, "death")
        # Respawns also log a non-zero ZDOID, so they are not reported as joins.
        self.assertIsNone(parse_line(dialect, "10/06/2026 12:00:00: Got character ZDOID from Ragnar : -123:4"))

    def test_bedrock(self):
        dialect = preset("minecraft-bedrock")
        event = parse_line(dialect, "[2026-10-06 12:00:00:000 INFO] Player connected: Steve, xuid: 123")
        self.assertEqual((event.kind, event.player), ("join", "Steve"))


class RenderTests(unittest.TestCase):
    def test_minecraft_tellraw_json_is_valid_and_colored(self):
        commands = render_outgoing(preset("minecraft-java"), ChatOut("Ana", 'hi "there" §4red', "Admin", 0xFF0000))
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].startswith("tellraw @a "))
        components = json.loads(commands[0][len("tellraw @a "):])
        self.assertEqual(components[2], {"text": "[Admin] ", "color": "#FF0000"})
        self.assertEqual(components[3], {"text": "Ana", "color": "#FF0000"})
        self.assertIn('hi "there"', components[4]["text"])

    def test_newlines_and_control_characters_are_removed(self):
        command = render_outgoing(preset("generic"), ChatOut("Ana", "line1\nop Bob\r\x00x"))[0]
        self.assertEqual(command, "say [Discord] Ana: line1 op Bob x")

    def test_invisible_and_bidi_characters_are_removed(self):
        command = render_outgoing(preset("generic"), ChatOut("A‮na", "h​i⁦x⁩ ﻿👨‍👩"))[0]
        self.assertEqual(command, "say [Discord] Ana: hix 👨‍👩")

    def test_bedrock_escapes_json_and_strips_section_signs(self):
        command = render_outgoing(preset("minecraft-bedrock"), ChatOut('A"na', 'x"}]} §kboom', "VIP", 0x55FF55))[0]
        payload = json.loads(command[len("tellraw @a "):])
        text = payload["rawtext"][0]["text"]
        self.assertIn("§a[VIP] §aA\"na§f: x\"}]} kboom", text)

    def test_terraria_and_unity_styles(self):
        terraria = render_outgoing(preset("terraria"), ChatOut("Ana", "hi", "Mod", 0x00FF00))[0]
        self.assertEqual(terraria, "say [c/5865F2:(Discord)] [c/00FF00:Mod] [c/00FF00:Ana]: hi")
        rust = render_outgoing(preset("rust"), ChatOut("A<b>", 'say "x"', None, None))[0]
        self.assertEqual(rust, "say <color=#5865F2>[Discord]</color> <color=#FFFFFF>A‹b›</color>: say 'x'")

    def test_long_messages_are_truncated_to_max_length(self):
        dialect = resolve("generic", {"out": {"max_length": 40}})
        command = render_outgoing(dialect, ChatOut("Ana", "x" * 500))[0]
        self.assertLessEqual(len(command), 40)
        self.assertTrue(command.endswith("…"))

    def test_no_output_without_chat_out(self):
        self.assertEqual(render_outgoing(preset("valheim"), ChatOut("Ana", "hi")), [])

    def test_nearest_legacy_color(self):
        self.assertEqual(formatters.nearest_legacy_code(0xFE0101), "4")
        self.assertEqual(formatters.nearest_legacy_code(0x5865F2), "9")


class ConsoleTailerTests(unittest.TestCase):
    def test_first_poll_is_baseline_then_only_new_lines(self):
        tailer = ConsoleTailer()
        self.assertEqual(tailer.feed(["a", "b", "c"]), [])
        self.assertEqual(tailer.feed(["b", "c", "d", "e"]), ["d", "e"])
        self.assertEqual(tailer.feed(["b", "c", "d", "e"]), [])

    def test_repeated_identical_lines(self):
        tailer = ConsoleTailer()
        tailer.feed(["x", "x"])
        self.assertEqual(tailer.feed(["x", "x", "x"]), ["x"])

    def test_restart_delivers_whole_next_run(self):
        tailer = ConsoleTailer()
        tailer.feed(["old"])
        tailer.restart()
        self.assertEqual(tailer.feed(["Done", "join"]), ["Done", "join"])

    def test_no_overlap_returns_all(self):
        tailer = ConsoleTailer()
        tailer.feed(["a"])
        self.assertEqual(tailer.feed(["z"]), ["z"])


class PlayerTrackerTests(unittest.TestCase):
    def test_join_leave_and_ready_reset(self):
        tracker = PlayerTracker()
        tracker.apply(GameEvent("join", "A"))
        tracker.apply(GameEvent("join", "B"))
        tracker.apply(GameEvent("leave", "A"))
        self.assertEqual(tracker.names, ["B"])
        self.assertFalse(tracker.known)
        tracker.apply(GameEvent("server_ready"))
        self.assertEqual(tracker.names, [])
        self.assertTrue(tracker.known)


if __name__ == "__main__":
    unittest.main()


class SecurityTests(unittest.TestCase):
    def test_command_separators_cannot_add_console_commands(self):
        for preset_id in ("source", "rust", "generic"):
            commands = render_outgoing(
                preset(preset_id), ChatOut("Bo;b", 'hi; rcon_password pwned; quit "x"', "Ad;min")
            )
            self.assertEqual(len(commands), 1)
            self.assertNotIn(";", commands[0], preset_id)
        self.assertNotIn('"', render_outgoing(preset("source"), ChatOut("A", 'say "x"'))[0].split(" ", 1)[1])

    def test_minecraft_section_codes_are_removed_from_discord_input(self):
        command = render_outgoing(preset("minecraft-java"), ChatOut("§kBob", "§4red"))[0]
        self.assertNotIn("§", command)

    def test_catastrophic_pattern_times_out_instead_of_hanging(self):
        import time

        dialect = resolve("generic", {"in": {"patterns": {"chat": r"^(a+)+$"}}})
        started = time.monotonic()
        self.assertIsNone(parse_line(dialect, "a" * 40 + "!"))
        self.assertLess(time.monotonic() - started, 2)


class RealConsoleLineTests(unittest.TestCase):
    """Lines quoted from real server logs (sources in the comments)."""

    def kinds(self, preset_id, lines):
        dialect = preset(preset_id)
        return [(event.kind, event.player, event.message) if (event := parse_line(dialect, line)) else None for line in lines]

    def test_minecraft_26_3_system_chat_labels(self):
        # MCDReforged#430, Ketbome/minepanel#289, and3rn3t/minecraft#79
        self.assertEqual(self.kinds("minecraft-java", [
            "[16:17:22] [Server thread/INFO]: System chat: Polaris_Light joined the game",
            "[16:17:30] [Server thread/INFO]: System chat: Polaris_Light left the game",
            "[16:18:00] [Server thread/INFO]: System chat: Mokkq fell from a high place",
            "[16:18:01] [Server thread/INFO]: <Mokkq> Hi Ketbome",
            "[16:18:02] [Server thread/INFO]: System chat: and3rn3t was slain by Zombie",
        ]), [
            ("join", "Polaris_Light", None), ("leave", "Polaris_Light", None),
            ("death", "Mokkq", "fell from a high place"), ("chat", "Mokkq", "Hi Ketbome"),
            ("death", "and3rn3t", "was slain by Zombie"),
        ])

    def test_minecraft_paper_async_chat_renamed_join_and_new_deaths(self):
        self.assertEqual(self.kinds("minecraft-java", [
            "[00:23:38] [Async Chat Thread - #0/INFO]: <Steve> hi",
            "[18:29:30] [Server thread/INFO] [minecraft/DedicatedServer]: <Steve> tests",
            "[12:00:00] [Server thread/INFO]: Alex (formerly known as Steve) joined the game",
            "[12:00:00] [Server thread/INFO]: Steve went off with a bang",
            "[12:00:00] [Server thread/INFO]: Steve was burned to a crisp while fighting Blaze",
            "[12:00:00] [Server thread/INFO]: Steve was speared by Pillager",
        ]), [
            ("chat", "Steve", "hi"), ("chat", "Steve", "tests"), ("join", "Alex", None),
            ("death", "Steve", "went off with a bang"), ("death", "Steve", "was burned to a crisp while fighting Blaze"),
            ("death", "Steve", "was speared by Pillager"),
        ])

    def test_bedrock_players_without_xbox_sign_in(self):
        # itzg/docker-minecraft-bedrock-server#378 and #587
        self.assertEqual(self.kinds("minecraft-bedrock", [
            "[2023-12-05 19:07:50:757 INFO] Player connected: Zonnig, xuid: ",
            "[2023-12-05 19:08:56:676 INFO] Player disconnected: Zonnig, xuid: , pfid: 99d7a8123147739a",
            "[2026-01-06 03:22:28:402 INFO] Player connected: Steve, xuid: 2535411111111111",
            "[2026-01-06 03:16:45:235 INFO] Server started.",
        ]), [("join", "Zonnig", None), ("leave", "Zonnig", None), ("join", "Steve", None), ("server_ready", None, None)])

    def test_terraria_tshock_title_sequences_geoip_and_broadcast(self):
        # Pryaxis/TShock#2093 (console title escape codes) and TShock source formats
        self.assertEqual(self.kinds("terraria", [
            "\x1b]0;Terraria Server: world\x07\x1b]0;0/22 on world @ 0.0.0.0:7777 (TShock for Terraria v4.4.0.0)\x07: Server started",
            "\x1b]0;1/22 on world\x07User has joined.",
            "Bob (Germany) has joined.",
            "(Server Broadcast) Restart in 5 minutes",
            "<Bob> hello",
        ]), [
            ("server_ready", None, None), ("join", "User", None), ("join", "Bob", None),
            ("broadcast", None, "Restart in 5 minutes"), ("chat", "Bob", "hello"),
        ])

    def test_source_cs2_lines_ignore_bots(self):
        # shobhit-pathak/MatchZy#263 and #175, yungwood/cs2log
        self.assertEqual(self.kinds("source", [
            'L 01/07/2025 - 21:27:26: "USER<0><[U:1:11111111]><>" entered the game',
            'L 01/07/2025 - 21:27:46: "USER<0><[U:1:11111111]><CT>" say ".ready"',
            'L 06/26/2024 - 16:12:01: "K<0><BOT><>" entered the game',
            'L 12/06/2023 - 00:33:52.509 - "Ann<9><[U:1:123456789]><Spectator>" disconnected (reason "NETWORK_DISCONNECT_DISCONNECT_BY_USER")',
        ]), [("join", "USER", None), ("chat", "USER", ".ready"), None, ("leave", "Ann", None)])

    def test_valheim_bepinex_prefix(self):
        # valheimPlus/ValheimPlus#767
        event = parse_line(preset("valheim"), "[Info   : Unity Log] 01/19/2023 21:16:28: Got character ZDOID from Boots : 0:0")
        self.assertEqual((event.kind, event.player), ("death", "Boots"))

    def test_presets_report_their_verification_status(self):
        statuses = {item["id"]: item["status"] for item in catalog()}
        self.assertEqual(statuses["minecraft-java"], "verified")
        self.assertTrue(all(status in {"verified", "likely", "unverified"} for status in statuses.values()))
