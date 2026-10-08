# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately
through GitHub: open the repository's **Security** tab → **Report a vulnerability**.

Include what you found, how to reproduce it, and the PteroSync, panel and Wings
versions. You will get a reply within 7 days. Fixes are released as soon as possible
and credited in the changelog unless you prefer otherwise.

## Supported versions

Only the latest release receives security fixes. The agent updates itself to the
extension's version when it restarts, so updating the extension updates both.

## What PteroSync protects

PteroSync is used by hosting companies whose customers each link their own Discord
server, so the main boundaries are between customers and between Discord and the panel:

- A customer can only see and configure their own servers, and only the Discord servers
  linked to them. Linking a Discord server needs a one-time code created in that
  Discord server by a member with *Manage Server*.
- Discord roles get **Console** or **Power** only from a panel user who has those
  permissions on the server, and never `@everyone`.
- Chat from Discord can only run the server's configured broadcast command; the panel
  checks every command and audits it.
- The agent signs every request (HMAC with a timestamp and a single-use nonce) and
  never controls the server it runs on.
- The agent installs and updates its code only over HTTPS from the panel.

Things that are **out of scope**: what a panel administrator can do (they control the
extension, the agent and its secrets), and what a Discord server's own administrators
do with the permissions they give their roles.
