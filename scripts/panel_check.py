"""Check the extension against the real Pterodactyl panel source.

Runs in Docker, inside the full panel checkout in ``.panel``:

* PHPStan on the extension's PHP, with the panel's types, so a wrong constructor,
  method or argument fails here instead of on a live panel;
* the extension's Pest tests (``pterorelay-discord/tests``) with the panel's test
  harness and a MySQL container.

Usage: python scripts/panel_check.py [--phpstan-only | --tests-only] [--update-panel]
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / ".panel"
EXTENSION = ROOT / "pterorelay-discord"
CI = ROOT / "scripts" / "panel-ci"
IMAGE = "pterorelay-panel-ci"
NETWORK = "pterorelay-panel-ci"
MYSQL = "pterorelay-panel-ci-mysql"
PANEL_BRANCH = "2.0-develop"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(command), flush=True)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(command, check=True, env=env, **kwargs)


def docker_ok(*command: str) -> bool:
    return subprocess.run(["docker", *command], capture_output=True).returncode == 0


def ensure_panel(update: bool) -> None:
    """A full (not sparse) checkout of the panel, with its Composer dependencies."""
    if not (PANEL / ".git").is_dir():
        run(["git", "clone", "--filter=blob:none", "--branch", PANEL_BRANCH,
             "https://github.com/pterodactyl/panel", str(PANEL)])
    elif update:
        run(["git", "-C", str(PANEL), "fetch", "--depth", "1", "origin", PANEL_BRANCH])
        run(["git", "-C", str(PANEL), "checkout", "-q", "FETCH_HEAD"])
    if (PANEL / ".git" / "info" / "sparse-checkout").is_file() and \
            subprocess.run(["git", "-C", str(PANEL), "config", "core.sparseCheckout"],
                           capture_output=True, text=True).stdout.strip() == "true":
        run(["git", "-C", str(PANEL), "sparse-checkout", "disable"])
    # The panel reads .env directly in places; the tests take their settings from the environment.
    (PANEL / ".env").touch()
    run(["docker", "build", "-q", "-t", IMAGE, str(CI)], stdout=subprocess.DEVNULL)
    if update or not (PANEL / "vendor" / "autoload.php").is_file():
        docker_run(["composer", "install", "--no-interaction", "--no-progress", "--no-scripts"])


def mounts() -> list[str]:
    return ["-v", f"{PANEL}:/panel", "-v", f"{EXTENSION}:/ext:ro", "-v", f"{CI}:/panel-ci:ro",
            "-v", "pterorelay-composer-cache:/root/.composer/cache"]


def app_env(database: bool) -> list[str]:
    key = "base64:" + base64.b64encode(os.urandom(32)).decode()
    env = {"APP_KEY": key, "APP_ENV": "testing", "CACHE_STORE": "array", "SESSION_DRIVER": "array",
           "QUEUE_CONNECTION": "sync", "MAIL_MAILER": "array"}
    if database:
        env.update({"DB_CONNECTION": "mysql", "DB_HOST": MYSQL, "DB_PORT": "3306",
                    "DB_DATABASE": "panel_test", "DB_USERNAME": "root", "DB_PASSWORD": "ci"})
    return [part for name, value in env.items() for part in ("-e", f"{name}={value}")]


def docker_run(command: list[str], *, database: bool = False) -> None:
    network = ["--network", NETWORK] if database else []
    run(["docker", "run", "--rm", *network, *app_env(database), *mounts(), IMAGE, *command])


def phpstan() -> None:
    docker_run(["php", "vendor/bin/phpstan", "analyse", "-c", "/panel-ci/phpstan.neon",
                "--no-progress", "--memory-limit=-1", "--error-format=raw"])


def start_mysql() -> None:
    if not docker_ok("network", "inspect", NETWORK):
        run(["docker", "network", "create", NETWORK], stdout=subprocess.DEVNULL)
    if not docker_ok("container", "inspect", MYSQL):
        run(["docker", "run", "-d", "--rm", "--name", MYSQL, "--network", NETWORK,
             "-e", "MYSQL_ROOT_PASSWORD=ci", "-e", "MYSQL_DATABASE=panel_test",
             "--tmpfs", "/var/lib/mysql", "mysql:8.4"], stdout=subprocess.DEVNULL)
    for _ in range(60):
        if docker_ok("exec", MYSQL, "mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", "-pci", "--silent"):
            return
        time.sleep(2)
    raise SystemExit("MySQL did not start")


def tests() -> None:
    """Copy the extension's tests into the panel and run them with its harness."""
    target = PANEL / "tests" / "Integration" / "PteroRelay"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(EXTENSION / "tests", target)
    try:
        start_mysql()
        # The panel's migrations, then the extension's: up, every down() back to nothing, up again
        # (an uninstall followed by a reinstall), then the tests on that schema.
        ext = "--path=/ext/database/migrations --realpath --force"
        docker_run(["sh", "-c", "php artisan migrate:fresh --seed --force -q"
                    f" && php artisan migrate {ext} -q && php artisan migrate:reset {ext}"
                    f" && php artisan migrate {ext}"
                    " && SKIP_MIGRATIONS=1 php vendor/bin/pest --colors=never tests/Integration/PteroRelay"], database=True)
    finally:
        shutil.rmtree(target, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phpstan-only", action="store_true")
    parser.add_argument("--tests-only", action="store_true")
    parser.add_argument("--update-panel", action="store_true", help="fetch the latest panel first")
    args = parser.parse_args()
    if shutil.which("docker") is None or not docker_ok("info"):
        raise SystemExit("Docker is not running")
    try:
        ensure_panel(args.update_panel)
        if not args.tests_only:
            phpstan()
        if not args.phpstan_only:
            tests()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Panel check failed: {error.cmd[-1] if error.cmd else ''} exited with {error.returncode}")
    print("Panel check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
