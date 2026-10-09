# PteroRelay Discord extension

Discord integration for Pterodactyl Panel 2.0. You can control servers from
Discord, give Discord roles per-server permissions, and relay chat between Discord
and any game through the Wings console.

## Quick start

1. Install this `.pteroext` through **Admin → Extensions**.
2. Create a Discord bot with **Message Content Intent** enabled and invite it with
   the scopes `bot` and `applications.commands`.
3. Open **Admin → PteroRelay → Run the agent on this panel**, paste the bot token,
   choose a node and click **Deploy agent**.
4. Open a server's **Discord** tab, link your Discord server and set up roles and
   game chat.

To update, upload the new `.pteroext`, then restart the **PteroRelay Agent**
server, which updates itself to the same version.

The full documentation is in the project's main `README.md`.
