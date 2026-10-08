# TODO

Goal: release PteroSync 1.0 (out of beta) for free on GitHub.

## Done

- [x] **Run the agent as a Pterodactyl server** with an egg and a **Deploy agent**
  button (0.3.0). The agent never power-controls or sends commands to its own server.
- [x] **Setup checklist:** token check, invite link, live intent, guild and
  channel permission checks.
- [x] **Diagnostics in the panel** from the agent heartbeat.
- [x] **Version checks** and "Update available".
- [x] **Automatic game preset** from the egg or Docker image.
- [x] **Community preset library** (`presets/`, **Browse presets**). It needs the
  `repository` setting once the repository is public.
- [x] **Wings websocket** console, polling as the fallback.

## 1.0: must have

### Catch broken releases before users do
- [x] **PHP static analysis** (PHPStan) against the panel source, so a wrong
  constructor fails the build. 0.4.0-beta.1 shipped such a bug that `php -l`
  missed. Done: `scripts/panel_check.py`, also in `release.py` and GitHub CI.
- [x] **PHP feature tests** for the agent API (HMAC, nonce replay, own-server
  refusal, chat template check) and the server routes (permission checks,
  link codes, admin-only fields). Done: `pterosync-discord/tests`, 33 tests.
- [x] **Panel check after upload:** `scripts/verify_panel.py`, or
  `release.py --panel URL`, waits until the panel serves the new version.
- [ ] Extend the PHP tests to the admin routes, the heartbeat limits, `/logs` and
  `/websocket`.
- [ ] **Clean install and upgrade test:** a fresh panel, and an upgrade from
  0.2, 0.3 and 0.4. Done: every migration's `down()` runs in `panel_check.py`
  (up, reset, up again).

### Stability
- [ ] **Soak test for 1–2 weeks** with `soak/` (fake game servers and a checker
  bot, see `soak/README.md`; set up, not run yet) on the test panel with at least three games
  (Minecraft Java, one Source game, one non-moddable game such as Terraria or
  Valheim). Cover a Wings restart, a panel restart, websocket token expiry,
  the server being reinstalled or suspended, and the bot being kicked from a guild.
- [ ] **Heartbeat size with many Discord servers:** the heartbeat sends every guild with its
  roles and channels (limit 1000 guilds). Measure the payload with a few hundred guilds;
  send roles and channels only when they change if it gets large.
- [ ] **Load test** with about 50 servers with chat enabled: agent memory, panel
  request rate, Discord rate limits on webhooks.
- [x] **Show customers when the agent is offline** in the server's Discord tab,
  instead of chat silently stopping.
- [x] **Unlinking cleans up:** deleting a server, unlinking a guild or the bot
  leaving a guild removes bindings, role rows and channels.

### Hosting customers (basic, free)
- [x] Bot status shows totals only (no server names leak to other Discords);
  `/status` without a server lists the Discord's linked servers.
- [ ] **"Add the bot to your Discord"** button and short setup steps in the
  customer's Discord tab (link codes already work).
- [x] **Per-server notification channel** for start, stop and crash messages,
  settable by the server owner.

### Scope
- [x] **Remove the standalone (legacy) mode:** the Client API key, the `[ADMIN]`
  setup commands and `/listservers` are gone; the agent always runs with the
  extension. `config.json` keeps only uptime and live status embeds.
- [ ] **Presets:** each one shows its status (verified, likely, unverified) in the
  Game list. At least the top five games are verified on real servers.

### Publish
- [x] **Public GitHub repository** `Skwarc/pterosync` with a fresh history.
- [x] The `repository` setting defaults to `Skwarc/pterosync`, so update checks and
  the preset library work without configuration.
- [ ] Release assets on GitHub: the `.pteroext` and `pterosync-discord.zip` (built by
  CI from a `v*` tag), the agent bundle and the egg.
- [x] `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CLA.md`.
- [ ] CLA Assistant installed on the repository; private vulnerability reporting on;
  issue templates.
- [x] **Compatibility statement** and **privacy note** in the README.
- [x] Security re-review (2026-10-08). Fixed before publishing: changing the game
  needs Console, turning on the relay or changing its patterns needs console read.

### Security follow-ups (from the 2026-10-08 review, all low)
- [ ] Live status embeds (`/setstatusmsg`) are keyed by server only: key them by
  Discord server + server, so one Discord server cannot replace or delete another's.
- [ ] At `LOG_LEVEL=DEBUG` discord.py logs webhook URLs with tokens: keep the
  `discord.http`/`discord.webhook` loggers at INFO unless explicitly enabled, or redact.
- [ ] The agent accepts an `http://` panel URL outside the egg: require https unless
  `ALLOW_INSECURE_PANEL=1`, like the updater.
- [ ] Reject broadcast templates without fixed text before the placeholder (`{line}`)
  or without a placeholder; show what an imported setup changes in `out`.
- [ ] Link code redemption: proceed only if deleting the code removed one row (two
  parallel requests can both use a code); refuse codes for guilds the bot has left.
- [ ] Stop console access (`relaysChat`, `authorizeDiscord`) for guilds with `left_at`.
- [ ] Optionally a per-agent "shared" flag so private bots are not offered to every
  customer as invites.

## Later

- [ ] Prebuilt multi-arch agent image (amd64 and arm64) on GHCR for the Docker
  alternative. The egg already runs on both.
- [ ] **Valheim chat (BepInEx), parked 2026-10-08.** Vanilla Valheim and Valheim Chat
  Plus log no chat on the server. Tested on the test panel: BepInEx loads only with
  Doorstop 4 variables (`DOORSTOP_ENABLED`, `DOORSTOP_TARGET_ASSEMBLY`); the panel's
  "Valheim BepINex" egg still uses the v3 names. Joins show as the first
  `Got character ZDOID from <name> : <non-zero>`; leaves have no name. Options:
  - Discord → game: ConsoleStdinHeeler (`say @all <message>`, server-only); the egg
    must start Valheim in the foreground so it gets console input.
  - Game → Discord: no plugin prints chat to the console. Either read a chat log file
    (BetterServerConfig, ServerManager) through the panel, a generic "chat from a log
    file" feature, or a tiny plugin of our own.
