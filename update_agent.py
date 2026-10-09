"""Keep an agent installed from the panel in step with the panel's extension version.

Runs before the bot when the agent is hosted as a Pterodactyl server (PteroRelay
Agent egg). It downloads the agent bundle the PteroRelay extension serves at
``<PANEL_PUBLIC_URL>/pterorelay-agent/bundle`` whenever the panel has a different
version, so updating the extension also updates the agent on its next restart.
Standard library only: it must work before the requirements are installed.

    python update_agent.py            # update when the panel version differs (AUTO_UPDATE=0 skips)
    python update_agent.py --install  # always (re)install the bundle
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 60


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "PteroRelay agent updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def local_version() -> str:
    path = HERE / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def install(bundle: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        for name in archive.namelist():
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise ValueError(f"unsafe path in agent bundle: {name}")
        # Packages are replaced as a whole so removed modules and presets disappear.
        shutil.rmtree(HERE / "adapters", ignore_errors=True)
        archive.extractall(HERE)


def main() -> int:
    force = "--install" in sys.argv[1:]
    panel = os.getenv("PANEL_PUBLIC_URL", "").strip().rstrip("/")
    if not panel:
        print("PANEL_PUBLIC_URL is not set; cannot download the PteroRelay agent.")
        return 0 if (HERE / "bot.py").exists() else 1
    # Downloaded code is executed: never fetch it over plain HTTP unless explicitly allowed.
    if not panel.startswith("https://") and os.getenv("ALLOW_INSECURE_PANEL", "0") != "1":
        print("PANEL_PUBLIC_URL must use https:// to update the agent (set ALLOW_INSECURE_PANEL=1 to override).")
        return 0 if (HERE / "bot.py").exists() else 1
    if not force and os.getenv("AUTO_UPDATE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return 0

    current = local_version()
    try:
        remote = fetch(f"{panel}/pterorelay-agent/bundle/version").decode("utf-8").strip()
        if remote == current and not force and (HERE / "bot.py").exists():
            print(f"PteroRelay agent {current} is up to date.")
            return 0
        install(fetch(f"{panel}/pterorelay-agent/bundle"))
        print(f"PteroRelay agent {current or '(none)'} -> {local_version()}.")
    except Exception as exc:  # the agent keeps running its current version
        print(f"PteroRelay agent update skipped: {exc}")
        return 0 if (HERE / "bot.py").exists() else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
