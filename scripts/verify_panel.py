"""Check that a panel runs this extension version after an upload.

Polls the public ``/pterorelay-agent/bundle/version`` route. The route only answers
when the panel booted the extension, so a matching version means the extension is
enabled and serving the new build; a 404 or 5xx means it is disabled or failed.

Usage: python scripts/verify_panel.py https://panel.example.com [--version X] [--wait 900]
       (the URL may also come from PTERORELAY_PANEL_URL)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def manifest_version() -> str:
    return json.loads((ROOT / "pterorelay-discord" / "extension.json").read_text(encoding="utf-8"))["version"]


def served_version(panel: str) -> tuple[int, str]:
    request = urllib.request.Request(panel.rstrip("/") + "/pterorelay-agent/bundle/version",
                                     headers={"User-Agent": "PteroRelay release check", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(100).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, str(getattr(error, "reason", error))


def verify(panel: str, version: str, wait: float, interval: float = 10) -> bool:
    deadline = time.monotonic() + wait
    last = None
    while True:
        status, body = served_version(panel)
        if status == 200 and body == version:
            print(f"OK: {panel} serves PteroRelay {version}.")
            return True
        state = (status, body)
        if state != last:
            if status == 200:
                print(f"Waiting: the panel still serves {body or 'an unknown version'}; upload {version} in Admin -> Extensions.")
            elif status == 404:
                print("The extension route is missing: the extension is disabled, not installed, or failed to boot.")
            elif status >= 500:
                print(f"The panel returned {status}: the extension is probably in an error state. Check Admin -> Extensions.")
            else:
                print(f"The panel could not be reached ({status or body}).")
            last = state
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("panel", nargs="?", default=os.environ.get("PTERORELAY_PANEL_URL"))
    parser.add_argument("--version", default=None, help="expected version (default: extension.json)")
    parser.add_argument("--wait", type=float, default=0, help="seconds to keep polling for the upload")
    args = parser.parse_args()
    if not args.panel:
        parser.error("give the panel URL or set PTERORELAY_PANEL_URL")
    return 0 if verify(args.panel, args.version or manifest_version(), args.wait) else 1


if __name__ == "__main__":
    sys.exit(main())
