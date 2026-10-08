# Contributing

Thanks for helping. The easiest and most useful contribution is a **game preset**.

## Game presets

A preset tells PteroSync how a game writes chat, joins and leaves to its console and
which command broadcasts a message. Presets are JSON, so no programming is needed.

1. In the panel, open a server → **Discord** → **Game integration**, set up the patterns
   with the line tester until the game's console lines are recognised, and use
   **Export setup**.
2. Add the file as `presets/<game-id>.json` (see the existing files for the format and
   `README.md` → *Writing patterns*), then run `python scripts/build_preset_index.py`.
3. Open a pull request with a few **real console lines** from the game (chat, join,
   leave), the game version and whether the server was modded.

Presets are data: they don't need the contributor agreement below.

## Code

Bug fixes and features are welcome. For anything larger than a small fix, open an
issue first so we can agree on the approach.

- Development setup, tests and the release script: `README.md` → *Development*.
- Run `python -m unittest discover -s tests` and `python scripts/panel_check.py`
  (PHPStan and the PHP tests against the panel, needs Docker) before opening a pull
  request.
- Keep the agent free of game mods: PteroSync only uses the Wings console.

### Contributor License Agreement

Code pull requests need a one-time agreement, [CLA.md](CLA.md). The CLA Assistant bot
asks you to accept it on your first pull request; it takes one click. It keeps the
project able to offer its code under other licenses in the future (for example a
commercial edition), while the code you contribute stays available here under the
AGPL.

## Reporting bugs

Open an issue with the PteroSync, panel and Wings versions, what you expected and
what happened. The **Diagnostics** box in Admin → PteroSync and the agent server's
console usually show the cause. Remove tokens, secrets and webhook URLs before
pasting logs.

Security problems: see [SECURITY.md](SECURITY.md), not public issues.
