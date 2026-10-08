"""Build the agent bundle the extension serves to agents hosted as Pterodactyl servers.

    python scripts/build_agent_bundle.py pterosync-discord/resources/agent/pterosync-agent.zip

The zip holds the agent runtime and a VERSION file with the extension version, and
is deterministic so identical sources give an identical archive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = ("bot.py", "agent_client.py", "chat_relay.py", "wings_console.py", "i18n.py", "update_agent.py", "requirements.txt")


def bundle_files() -> list[Path]:
    files = [ROOT / name for name in RUNTIME_FILES]
    files += sorted(
        path for path in (ROOT / "adapters").rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts
    )
    return files


def build(output: Path) -> str:
    version = json.loads((ROOT / "pterosync-discord" / "extension.json").read_text(encoding="utf-8"))["version"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        entries = [(path.relative_to(ROOT).as_posix(), path.read_bytes()) for path in bundle_files()]
        entries.append(("VERSION", f"{version}\n".encode()))
        for name, data in sorted(entries):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    return version


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    target = Path(sys.argv[1])
    print(f"Built agent bundle {build(target)} -> {target}")
