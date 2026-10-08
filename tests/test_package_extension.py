import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.package_extension import package


class ExtensionPackagingTests(unittest.TestCase):
    def test_package_has_manifest_at_root_and_excludes_development_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            (root / "node_modules").mkdir()
            (root / "extension.json").write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
            (root / "dist" / "client.js").write_text("export {};", encoding="utf-8")
            (root / "dist" / "client.js.map").write_text("{}", encoding="utf-8")
            (root / "node_modules" / "secret.js").write_text("ignored", encoding="utf-8")
            output = root / "release.pteroext"

            package(root, output, "v1.2.3")

            with ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["dist/client.js", "extension.json"])

    def test_tag_must_match_manifest_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            (root / "dist" / "client.js").write_text("", encoding="utf-8")
            (root / "extension.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                package(root, root / "release.pteroext", "v2.0.0")
