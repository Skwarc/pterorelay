"""Build a deterministic Pterodactyl .pteroext archive.

The archive is a ZIP whose root contains extension.json. Development dependencies,
tests, caches, and source maps are intentionally excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

INCLUDED_FILES = {"extension.json", "LICENSE", "README.md"}
# Same set as the panel's p:extension:pack (plus the autoloaded "src").
INCLUDED_DIRECTORIES = {"database", "dist", "resources", "routes", "src", "vendor"}
EXCLUDED_PARTS = {"node_modules", "tests", "coverage", "__pycache__"}


def extension_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if not path.is_file() or EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if relative.name.endswith((".map", ".pyc")):
            continue
        if relative.as_posix() in INCLUDED_FILES or relative.parts[0] in INCLUDED_DIRECTORIES:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source).as_posix())


def package(source: Path, output: Path, expected_version: str | None = None) -> str:
    manifest_path = source / "extension.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest["version"])
    if expected_version and version != expected_version.removeprefix("v"):
        raise ValueError(f"tag version {expected_version!r} does not match extension version {version!r}")
    if not (source / "dist" / "client.js").is_file():
        raise FileNotFoundError("dist/client.js is missing; build the extension frontend first")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in extension_files(source):
            relative = path.relative_to(source).as_posix()
            info = ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    if output.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("extension archive exceeds Pterodactyl's 50 MB upload limit")
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version")
    arguments = parser.parse_args()
    digest = package(arguments.source.resolve(), arguments.output.resolve(), arguments.version)
    checksum = arguments.output.with_suffix(arguments.output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {arguments.output.name}\n", encoding="ascii")
    print(f"Created {arguments.output} ({digest})")


if __name__ == "__main__":
    main()
