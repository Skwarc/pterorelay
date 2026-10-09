import json
import tempfile
import unittest
from pathlib import Path

from adapters.engine import parse_line
from adapters.registry import resolve
from scripts.build_preset_index import INDEX_NAME, PRESET_DIR, build_index, render


def setup(**changes):
    data = {
        "format": "pterorelay-game", "version": 1, "name": "Example", "description": "An example setup.",
        "author": "someone", "status": "unverified", "game": "generic",
        "overrides": {"in": {"line_prefix": "auto", "patterns": {"chat": "{player}: {message}"}}},
        "event_colors": None, "disabled_features": [],
    }
    data.update(changes)
    return data


class PresetIndexTests(unittest.TestCase):
    def build(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in files.items():
                (root / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
            return build_index(root)

    def test_repository_presets_are_valid_and_the_index_is_current(self):
        index = build_index()
        self.assertGreaterEqual(len(index["presets"]), 2)
        self.assertEqual((PRESET_DIR / INDEX_NAME).read_text(encoding="utf-8").replace("\r\n", "\n"), render(index))

    def test_index_lists_metadata_and_skips_itself(self):
        index = self.build({"my-game.json": setup(), INDEX_NAME: {"version": 1, "presets": []}})
        self.assertEqual(index, {"version": 1, "presets": [{
            "id": "my-game", "file": "my-game.json", "name": "Example", "game": "generic",
            "description": "An example setup.", "author": "someone", "status": "unverified",
        }]})

    def test_invalid_setups_are_rejected(self):
        cases = {
            "unknown game": setup(game="no-such-game"),
            "wrong format": setup(format="something-else"),
            "missing author": setup(author=""),
            "bad status": setup(status="great"),
            "bad pattern": setup(overrides={"in": {"patterns": {"chat": "re:(unclosed"}}}),
            "unknown pattern key": setup(overrides={"in": {"patterns": {"whisper": "{player}"}}}),
            "bad colour": setup(event_colors={"join": "green"}),
            "bad feature": setup(disabled_features=["everything"]),
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.build({"broken.json": data})
        with self.assertRaisesRegex(ValueError, "file name"):
            self.build({"Bad Name.json": setup()})
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            self.build({"broken.json": "{"})


class SeedPresetTests(unittest.TestCase):
    def dialect(self, preset_id):
        data = json.loads((PRESET_DIR / f"{preset_id}.json").read_text(encoding="utf-8"))
        return resolve(data["game"], data["overrides"])

    def test_tshock_5_chat(self):
        event = parse_line(self.dialect("terraria-tshock-5"), "Ana: hello there")
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Ana", "hello there"))

    def test_minecraft_me_emote(self):
        dialect = self.dialect("minecraft-java-me-emotes")
        event = parse_line(dialect, "[12:00:00] [Server thread/INFO]: * Steve waves at everyone")
        self.assertEqual((event.kind, event.player, event.message), ("chat", "Steve", "waves at everyone"))
        self.assertEqual(parse_line(dialect, "[12:00:00] [Server thread/INFO]: <Steve> hi").message, "hi")


if __name__ == "__main__":
    unittest.main()
