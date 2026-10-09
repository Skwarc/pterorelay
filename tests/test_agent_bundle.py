import json
import re
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.build_agent_bundle import ROOT, build


class AgentBundleTests(unittest.TestCase):
    def test_bundle_contains_runtime_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "agent.zip"
            version = build(output)
            with ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertEqual(archive.read("VERSION").decode().strip(), version)
        expected = json.loads((ROOT / "pterorelay-discord" / "extension.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(version, expected)
        for name in ("bot.py", "agent_client.py", "chat_relay.py", "i18n.py", "update_agent.py",
                     "requirements.txt", "adapters/engine.py", "adapters/presets/minecraft-java.json"):
            self.assertIn(name, names)
        self.assertFalse(any("__pycache__" in name or name.startswith("tests/") for name in names))

    def test_every_local_import_of_the_bot_is_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "agent.zip"
            build(output)
            names = set(ZipFile(output).namelist())
        local_modules = {Path(name).stem for name in names if name.endswith(".py") and "/" not in name}
        imports = re.findall(r"^(?:from|import) (\w+)", (ROOT / "bot.py").read_text(encoding="utf-8"), re.MULTILINE)
        for module in imports:
            if (ROOT / f"{module}.py").exists():
                self.assertIn(module, local_modules)


if __name__ == "__main__":
    unittest.main()
