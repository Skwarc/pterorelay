#!/bin/sh
# Docker alternative: update the PteroSync agent to the latest code and rebuild it.
# (The PteroSync Agent egg updates itself from the panel; this script is not needed there.)
set -eu
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build --remove-orphans
docker compose ps
