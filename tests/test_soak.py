import importlib
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from adapters.engine import parse_line
from adapters.registry import resolve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "soak"))


def fake_game(dialect: str):
    with patch.dict(os.environ, {"SOAK_DIALECT": dialect, "SOAK_TAG": "t"}):
        import fake_game
        return importlib.reload(fake_game)


def events(dialect: str, action) -> list:
    game = fake_game(dialect)
    output = io.StringIO()
    with redirect_stdout(output):
        action(game)
    preset = resolve(dialect)
    return [parse_line(preset, line) for line in output.getvalue().splitlines()]


class FakeGameTests(unittest.TestCase):
    """The fake server must speak exactly what the real presets parse."""

    def test_minecraft_lines_parse(self):
        def action(game):
            game.ready(); game.chat("Steve", "soak t #1 t=1.0"); game.join("Jt1"); game.leave("Jt1"); game.death("Dt1")
        kinds = [(event.kind, event.player) for event in events("minecraft-java", action)]
        self.assertEqual(kinds, [("server_ready", None), ("chat", "Steve"), ("join", "Jt1"), ("leave", "Jt1"), ("death", "Dt1")])

    def test_terraria_lines_parse(self):
        def action(game):
            game.ready(); game.chat("Steve", "soak t #1 t=1.0"); game.join("Jt1"); game.leave("Jt1")
        kinds = [(event.kind, event.player) for event in events("terraria", action)]
        self.assertEqual(kinds, [("server_ready", None), ("chat", "Steve"), ("join", "Jt1"), ("leave", "Jt1")])

    def test_valheim_lines_parse(self):
        kinds = [(e.kind, e.player) if e else None for e in events("valheim", lambda game: (game.ready(), game.death("Dt1")))]
        self.assertEqual(kinds, [("server_ready", None), ("death", "Dt1")])

    def test_tricky_messages_stay_chat(self):
        def action(game):
            for template in game.TRICKY:
                game.chat("Steve", template.format(n="soak t #1 t=1.0"))
        parsed = events("minecraft-java", action)
        self.assertTrue(all(event and event.kind == "chat" and event.message.startswith("soak t #1") for event in parsed))

    def test_discord_message_is_echoed_back(self):
        game = fake_game("terraria")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertTrue(game.handle_command("say [c/5865F2:(Discord)] [c/FFFFFF:Checker]: soakin #7 t=1700000000.5\n"))
            self.assertFalse(game.handle_command("stop\n"))
        self.assertIn("<Echo> soakack t #7 t=1700000000.5", output.getvalue())


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "soak"))
import checker  # noqa: E402


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.db = checker.connect(":memory:")

    def test_gaps_duplicates_and_latency(self):
        for seq in (1, 2, 2, 5, 6):
            checker.record_message(self.db, f"soak mc #{seq} t=100.0", 101.5, False)
        text = checker.report(self.db, since=0, now=200)
        self.assertIn("`mc` chat: 4/6 delivered (66.67%), 2 lost, 1 duplicated", text)
        self.assertIn("latency median 1.5s", text)
        self.assertIn("gaps: #3–#4", text)

    def test_mentions_round_trips_and_events(self):
        checker.record_message(self.db, "soak tr #1 t=10 @everyone", 11, True)
        checker.record_message(self.db, "Jtr4 joined", 12, False)
        self.db.execute("INSERT INTO pings VALUES (1, 20)")
        checker.record_message(self.db, "soakack tr #1 t=20", 23, False)
        text = checker.report(self.db, since=0, now=1000)
        self.assertIn("**1 pinged someone**", text)
        self.assertIn("`tr` join/leave events: 1", text)
        self.assertIn("`tr` Discord → game → Discord: 1/1 answered, median 3.0s", text)
        self.assertNotIn("`tr` chat: 2", text)  # the ack is not counted as chat


class SoakEggTests(unittest.TestCase):
    def test_egg_is_up_to_date(self):
        import json
        from scripts.build_soak_egg import EGG, egg
        self.assertEqual(json.loads(EGG.read_text(encoding="utf-8")), egg(),
                         "soak/egg-pterosync-soak.json is stale: run python scripts/build_soak_egg.py")


class CheckerCatchUpTests(unittest.TestCase):
    def test_messages_are_counted_once(self):
        db = checker.connect(":memory:")
        self.assertTrue(checker.record_message(db, "soak mc #1 t=1", 2, False, message_id=10))
        self.assertFalse(checker.record_message(db, "soak mc #1 t=1", 2, False, message_id=10))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM chat").fetchone()[0], 1)
        self.assertEqual(checker.last_received(db), 2)
