# Soak test

Runs PteroSync for days with fake game servers instead of real players, and measures
whether every message arrives once, how fast, and whether the agent's memory grows.

- **Fake game servers** (`fake_game.py`) run on Wings like any game. They write
  numbered chat, joins, leaves and deaths in the console format of Minecraft Java,
  Terraria or Valheim, with tricky messages mixed in (mentions, markdown, emoji,
  fake `<Server>` lines, long lines).
- **The checker** (`checker.py`) is a second Discord bot. It reads the chat channels
  and reports:
  - delivered, lost and duplicated messages;
  - latency;
  - Discord → game → Discord round trips;
  - relayed messages that pinged someone;
  - the agent's memory.

Both run from one egg, `egg-pterosync-soak.json`, built by
`python scripts/build_soak_egg.py` from the two scripts. Each fake server needs about
20 MB of RAM and the checker about 60 MB.

## Setup

1. **Checker bot.** In the [Discord Developer Portal](https://discord.com/developers/applications),
   create a second application, for example "PteroSync Soak". Under **Bot**:
   - turn on **Message Content Intent**;
   - copy the token;
   - copy the application ID (it is also the bot's user ID).

   Invite it to your test Discord server with View Channels, Send Messages and Read
   Message History:
   `https://discord.com/oauth2/authorize?client_id=<application id>&scope=bot%20applications.commands&permissions=68608`
2. **Channels.** One chat channel per fake server, for example `#soak-minecraft`,
   `#soak-terraria`, `#soak-valheim`, plus `#soak-report`.
3. **Egg.** Admin → Nests → Import Egg → `soak/egg-pterosync-soak.json`.
4. **Fake servers.** Create three servers with the egg, 64 MB RAM each:
   - Role `game`;
   - Game `minecraft-java`, `terraria` or `valheim`.

   Start them.
5. **Link them.** For each one, open the server's **Discord** tab:
   - link your test Discord;
   - pick the matching **Game** preset (Minecraft: Java Edition, Terraria, Valheim);
   - turn chat on;
   - choose its chat channel.
6. **Let the checker talk to the games.** The agent never relays bots, except IDs in
   `RELAY_BOT_IDS`. In the agent server's **Files**, create `.env` containing
   `RELAY_BOT_IDS=<checker application id>`. Then restart the agent.
7. **Checker server.** Create one more server with the egg, 128 MB RAM:
   - Role `checker`;
   - the checker token;
   - **Soak channels**: the three channel IDs, comma separated;
   - **Report channel**: the ID of `#soak-report`.

   Optional, for the agent's memory:
   - **Panel URL**;
   - **Panel API key**: a client key of an account that can see the agent server;
   - **Agent server**: its 8-character identifier.
8. After a few minutes, run `/soak` in Discord. It shows the results so far.

## Disruptions

The websocket token expires every 10 minutes on its own. On top of that:

- **Daily:** a Pterodactyl **Schedule** on each fake server restarts it (Schedules →
  Restart).
- **Weekly, by hand:**
  - restart the agent;
  - restart Wings;
  - restart the panel;
  - reinstall one fake server;
  - suspend and unsuspend one;
  - kick the bot from the Discord and invite it back.

Note the time of each disruption, so gaps in the report can be matched to it.

## Pass criteria for 1.0

Over 7 days:

- at least 99.9% of chat delivered, outside the minute after a disruption;
- 0 duplicates;
- 0 relayed messages that pinged anyone;
- at least 99% of round trips answered, median under 5 s;
- agent memory within 20 MB of where it started after the first day.
