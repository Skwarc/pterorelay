# Changelog

PteroRelay follows [semantic versioning](https://semver.org/) from 1.0: within 1.x
there are no breaking changes to presets, the agent API or the extension settings.
Every release ships the extension (`.pteroext`) and the agent bundle together; the
agent updates itself to the extension's version.

## 0.6.0 (beta)

**Renamed from PteroSync to PteroRelay** (another Pterodactyl project already uses
the old name). Everything was renamed: the extension is now `pterorelay-discord`, its
tables `ext_pterorelay_*`, the agent's settings `PTERORELAY_AGENT_ID` and
`PTERORELAY_AGENT_SECRET`, and the agent route `/pterorelay-agent`. There is no
automatic upgrade from PteroSync: uninstall the old extension and the old agent
server, install PteroRelay, deploy the agent again and link your servers again.

## 0.5.0 (beta)

**Breaking:** the standalone mode is removed. The agent always runs with the
extension; `PTERODACTYL_URL`, `PTERODACTYL_API_KEY` and the `[ADMIN]` setup commands
(`/addserver`, `/addrole`, `/setchannel`, …) and `/listservers` are gone. Link servers
and give roles access in the panel instead. The Docker files are now
`docker-compose.yml` and `.env.example`.

- Customers get an **Add the bot to your Discord** button and a three-step guide in
  the server's Discord tab, and can **unlink** a Discord server.
- Per-server **notification channel** for start, stop and crash messages.
- The Discord tab warns when the bot is offline or no longer in the Discord server.
- Discord servers the bot has left are kept for 30 days, then deleted with their links.
- `/setstatusmsg` and `/removestatusmsg` need *Manage Server* and access to the server
  in that Discord server.
- Fixed: **Chat** ticked for @everyone blocked every member instead of allowing them.
- The heartbeat accepts up to 1000 Discord servers per agent (was 100).

## 0.4.0 (beta)

- **Setup checklist** in Admin → PteroRelay: token check, invite link, intent, guild and
  channel permission checks.
- **Diagnostics:** the agent reports relay problems and missing permissions to the panel.
- **Version checks** and "Update available".
- **Game preset suggested** from the server's egg or Docker image.
- **Community preset library** (`presets/`) with **Browse presets**.
- **Wings websocket** console: game chat arrives within about a second; polling stays
  as the fallback (`CONSOLE_MODE`).
- The bot's status shows totals only, so no server names leak to other Discord servers;
  `/status` without a server lists the Discord server's linked servers.
- Invisible and text-direction characters are stripped from relayed chat.
- PHPStan and integration tests against the real panel source on every release.

## 0.3.0 (beta)

- **Run the agent as a Pterodactyl server:** the PteroRelay Agent egg and a **Deploy
  agent** button; the agent installs and updates itself from the panel and never
  controls its own server.
- Link codes (`/link`) so non-admin server owners can link their own Discord server.
- Role permission rules: Console and Power can only be given by panel users who have
  them, never to @everyone.
- Feature toggles, easier chat patterns, a pattern tester, import/export of game setups.
- Memory use of the agent reduced to about 50 MB.

## 0.2.0 (beta)

- **Two-way game chat without mods,** through the Wings console only: presets for
  Minecraft Java and Bedrock, Terraria, Valheim, Rust, Source games and a generic one.
- Roles and colours from Discord shown in-game; player names and avatars in Discord
  through webhooks; join, leave, death and server events as embeds.
- Integration channel per Discord server and chat channel per server.

## 0.1.0 (beta)

- First extension release: agent API with HMAC-signed requests, Discord roles mapped to
  View, Power, Console and Configure per server, power and console commands from
  Discord, live status embeds.
