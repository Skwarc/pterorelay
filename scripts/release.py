"""One-command PteroSync release.

    python scripts/release.py 0.2.0-beta.2      # explicit version
    python scripts/release.py --bump            # 0.2.0-beta.1 -> 0.2.0-beta.2, 0.2.0 -> 0.2.1

Steps: set the version everywhere, run the Python tests, lint the PHP, type-check
and build the extension frontend, package the .pteroext into release/, build the
agent Docker image, then commit, tag and push (Gitea CI publishes the release
from the tag). Use --no-push to stop before touching git, and --skip-docker when
Docker is not running. PHPStan and the PHP tests run against the panel in Docker
(scripts/panel_check.py). With --panel URL the script waits after pushing until
that panel serves the new version (scripts/verify_panel.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "pterosync-discord"
RELEASE_DIR = ROOT / "release"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$")

sys.path.insert(0, str(ROOT / "scripts"))
from package_extension import package  # noqa: E402
from verify_panel import verify as verify_panel  # noqa: E402


def step(title: str) -> None:
    print(f"\n==> {title}", flush=True)


def run(*command: str, cwd: Path = ROOT, retries: int = 0) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise SystemExit(f"{command[0]} is not installed or not on PATH")
    for attempt in range(retries + 1):
        if subprocess.run([executable, *command[1:]], cwd=cwd).returncode == 0:
            return
        if attempt < retries:
            print(f"   retrying in 3 s ({attempt + 1}/{retries})")
            time.sleep(3)
    raise SystemExit(f"Command failed: {' '.join(command)}")


def output(*command: str) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def current_version() -> str:
    return json.loads((EXTENSION / "extension.json").read_text(encoding="utf-8"))["version"]


def bumped(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?", version)
    if match is None:
        raise SystemExit(f"Cannot bump version {version!r}")
    major, minor, patch, channel, number = match.groups()
    if channel:
        return f"{major}.{minor}.{patch}-{channel}.{int(number) + 1}"
    return f"{major}.{minor}.{int(patch) + 1}"


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Version marker not found in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8", newline="\n")


def set_version(version: str) -> None:
    replace_once(EXTENSION / "extension.json", r'^(  "version": )"[^"]+"', rf'\1"{version}"')
    replace_once(EXTENSION / "package.json", r'^(  "version": )"[^"]+"', rf'\1"{version}"')
    replace_once(ROOT / "agent_client.py", r'(version: str = )"[^"]+"', rf'\1"{version}"')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="new version, e.g. 0.2.0-beta.2")
    parser.add_argument("--bump", action="store_true", help="increment the current version")
    parser.add_argument("--no-push", action="store_true", help="build only; do not commit, tag or push")
    parser.add_argument("--skip-docker", action="store_true", help="do not build the agent Docker image")
    parser.add_argument("--skip-panel-check", action="store_true",
                        help="skip PHPStan and the PHP tests against the panel (needs Docker)")
    parser.add_argument("--panel", default=os.environ.get("PTEROSYNC_PANEL_URL"),
                        help="after pushing, wait until this panel serves the new version (default: PTEROSYNC_PANEL_URL)")
    args = parser.parse_args()

    previous = current_version()
    version = bumped(previous) if args.bump else (args.version or previous).removeprefix("v")
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(f"Invalid version {version!r}")
    tag = f"v{version}"
    if not args.no_push and output("git", "tag", "--list", tag):
        raise SystemExit(f"Tag {tag} already exists; choose a new version or use --bump")

    step(f"Version {previous} -> {version}")
    set_version(version)

    step("Python tests")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q")
    run(sys.executable, "-m", "py_compile", "bot.py", "agent_client.py", "chat_relay.py", "wings_console.py", "i18n.py", "update_agent.py",
        *(str(path.relative_to(ROOT)) for path in (ROOT / "adapters").glob("*.py")))

    step("PHP syntax")
    if shutil.which("php"):
        for path in sorted([*EXTENSION.glob("src/**/*.php"), *EXTENSION.glob("routes/*.php"), *EXTENSION.glob("database/**/*.php"), *EXTENSION.glob("resources/**/*.php")]):
            run("php", "-l", str(path))
    else:
        print("   php not found; skipped")

    if args.skip_panel_check:
        print("\n==> Panel check skipped", flush=True)
    else:
        step("PHPStan and PHP tests against the panel")
        run(sys.executable, "scripts/panel_check.py")

    step("Extension frontend")
    if not (ROOT / ".panel" / "packages" / "sdk").is_dir():
        raise SystemExit("The panel SDK is missing: git clone --depth 1 --filter=blob:none --sparse --branch "
                         "2.0-develop https://github.com/pterodactyl/panel .panel && git -C .panel sparse-checkout set packages/sdk")
    if not (EXTENSION / "node_modules").is_dir():
        run("npm", "ci", "--no-audit", "--no-fund", cwd=EXTENSION)
    run("npm", "run", "typecheck", cwd=EXTENSION)
    run("npm", "run", "build", cwd=EXTENSION)

    step("Package")
    run(sys.executable, "scripts/build_agent_bundle.py", "pterosync-discord/resources/agent/pterosync-agent.zip")
    RELEASE_DIR.mkdir(exist_ok=True)
    archive = RELEASE_DIR / f"pterosync-discord-{tag}.pteroext"
    digest = package(EXTENSION, archive, tag)
    archive.with_suffix(".pteroext.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    # Stable name for links to releases/latest/download/pterosync-discord.zip.
    shutil.copyfile(archive, RELEASE_DIR / "pterosync-discord.zip")
    print(f"   {archive.relative_to(ROOT)}  sha256 {digest}")

    if not args.skip_docker:
        step("Agent Docker image")
        run("docker", "build", "-t", f"pterosync-agent:{version}", "-t", "pterosync-agent:latest", ".")

    if args.no_push:
        print(f"\nBuilt {tag} locally; nothing was committed or pushed.")
        return

    step("Commit, tag and push")
    run("git", "add", "-A")  # release/ is gitignored
    if output("git", "diff", "--cached", "--name-only"):
        run("git", "commit", "-m", f"release: {version}")
    run("git", "tag", "-a", tag, "-m", f"PteroSync {version}")
    run("git", "push", "origin", "HEAD", retries=2)
    run("git", "push", "origin", tag, retries=2)
    print(f"\nReleased {tag}. Upload {archive.relative_to(ROOT)} or the Gitea release asset in Admin -> Extensions.")
    if args.panel:
        step(f"Waiting for {args.panel} to serve {version}")
        if not verify_panel(args.panel, version, wait=1800):
            raise SystemExit(f"{args.panel} does not serve {version}; check Admin -> Extensions.")


if __name__ == "__main__":
    main()
