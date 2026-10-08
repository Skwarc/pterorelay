import io
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import update_agent


def make_bundle(version: str, files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("VERSION", f"{version}\n")
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FakePanel(BaseHTTPRequestHandler):
    version = "2.0.0"
    bundle = b""
    downloads = 0

    def do_GET(self):
        if self.path == "/pterosync-agent/bundle/version":
            body = self.version.encode()
        elif self.path == "/pterosync-agent/bundle":
            type(self).downloads += 1
            body = self.bundle
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class UpdateAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakePanel)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        FakePanel.version, FakePanel.downloads = "2.0.0", 0
        FakePanel.bundle = make_bundle("2.0.0", {"bot.py": "print('new')", "adapters/presets/new.json": "{}"})
        url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.patches = [
            patch.object(update_agent, "HERE", self.home),
            patch.dict(os.environ, {"PANEL_PUBLIC_URL": url, "AUTO_UPDATE": "1", "ALLOW_INSECURE_PANEL": "1"}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def test_installs_and_replaces_adapters(self):
        (self.home / "adapters" / "presets").mkdir(parents=True)
        (self.home / "adapters" / "presets" / "removed.json").write_text("{}")
        (self.home / "VERSION").write_text("1.0.0\n")
        (self.home / "bot.py").write_text("print('old')")

        self.assertEqual(update_agent.main(), 0)
        self.assertEqual((self.home / "bot.py").read_text(), "print('new')")
        self.assertEqual(update_agent.local_version(), "2.0.0")
        self.assertFalse((self.home / "adapters" / "presets" / "removed.json").exists())
        self.assertTrue((self.home / "adapters" / "presets" / "new.json").exists())

    def test_same_version_does_not_download(self):
        (self.home / "VERSION").write_text("2.0.0\n")
        (self.home / "bot.py").write_text("print('current')")
        self.assertEqual(update_agent.main(), 0)
        self.assertEqual(FakePanel.downloads, 0)

    def test_auto_update_off_and_unreachable_panel_keep_current_code(self):
        (self.home / "bot.py").write_text("print('current')")
        with patch.dict(os.environ, {"AUTO_UPDATE": "0"}):
            self.assertEqual(update_agent.main(), 0)
        with patch.dict(os.environ, {"PANEL_PUBLIC_URL": "http://127.0.0.1:9/"}):
            self.assertEqual(update_agent.main(), 0)
        self.assertEqual((self.home / "bot.py").read_text(), "print('current')")
        self.assertEqual(FakePanel.downloads, 0)

    def test_first_install_failure_is_an_error(self):
        with patch.dict(os.environ, {"PANEL_PUBLIC_URL": "http://127.0.0.1:9/"}):
            self.assertEqual(update_agent.main(), 1)

    def test_plain_http_panel_is_refused_by_default(self):
        (self.home / "bot.py").write_text("print('current')")
        with patch.dict(os.environ, {"ALLOW_INSECURE_PANEL": "0"}):
            self.assertEqual(update_agent.main(), 0)
        self.assertEqual(FakePanel.downloads, 0)
        self.assertEqual((self.home / "bot.py").read_text(), "print('current')")

    def test_rejects_path_traversal(self):
        FakePanel.bundle = make_bundle("2.0.0", {"../evil.py": "x"})
        (self.home / "bot.py").write_text("print('current')")
        self.assertEqual(update_agent.main(), 0)
        self.assertFalse((self.home.parent / "evil.py").exists())
        self.assertEqual((self.home / "bot.py").read_text(), "print('current')")


if __name__ == "__main__":
    unittest.main()
