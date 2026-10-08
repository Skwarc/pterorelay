/**
 * PteroSync Agent egg: runs the Discord agent as an ordinary Pterodactyl server.
 *
 * The install script and every start fetch the agent from this panel
 * (`/pterosync-agent/bundle`), so the agent always matches the extension
 * version and no git or Docker registry access is needed.
 */

export const AGENT_EGG_NAME = 'PteroSync Agent';
export const AGENT_DONE_LINE = 'PteroSync agent ready';

const INSTALL_SCRIPT = `#!/bin/bash
# PteroSync Agent installer: downloads the agent bundle from the panel.
set -euo pipefail
mkdir -p /mnt/server
cd /mnt/server
python3 - <<'PY'
import io, os, urllib.request, zipfile
url = os.environ["PANEL_PUBLIC_URL"].rstrip("/") + "/pterosync-agent/bundle"
request = urllib.request.Request(url, headers={"User-Agent": "PteroSync installer"})
with urllib.request.urlopen(request, timeout=120) as response:
    archive = zipfile.ZipFile(io.BytesIO(response.read()))
for name in archive.namelist():
    if name.startswith("/") or ".." in name.split("/"):
        raise SystemExit("unsafe path in agent bundle: " + name)
archive.extractall(".")
print("Installed PteroSync agent", open("VERSION").read().strip())
PY
`;

// update_agent.py keeps the agent on the panel's version; exec hands ^C straight to Python.
const STARTUP = 'python update_agent.py && python -m pip install --user --quiet --disable-pip-version-check '
    + '--no-warn-script-location -r requirements.txt && exec python bot.py';

type EggVariable = {
    name: string; description: string; env_variable: string; default_value: string;
    user_viewable: boolean; user_editable: boolean; rules: string; field_type: 'text';
};

function variable(name: string, env: string, description: string, defaultValue: string, rules: string, visible: boolean): EggVariable {
    return {
        name, description, env_variable: env, default_value: defaultValue,
        user_viewable: visible, user_editable: visible, rules, field_type: 'text',
    };
}

export function agentEgg(panelUrl: string) {
    return {
        _comment: 'PteroSync Agent: Discord agent for the PteroSync Pterodactyl extension.',
        meta: { version: 'PTDL_v2', update_url: null },
        exported_at: '2026-10-07T00:00:00+00:00',
        name: AGENT_EGG_NAME,
        author: 'noreply@pterosync.invalid',
        description: 'Discord agent for the PteroSync extension. Installs and updates itself from this panel.',
        features: null,
        docker_images: { 'Python 3.11': 'ghcr.io/parkervcp/yolks:python_3.11' },
        file_denylist: [],
        startup: STARTUP,
        config: {
            files: '{}',
            startup: JSON.stringify({ done: AGENT_DONE_LINE }),
            logs: '{}',
            stop: '^C',
        },
        scripts: { installation: { script: INSTALL_SCRIPT, container: 'python:3.11-slim', entrypoint: 'bash' } },
        variables: [
            variable('Discord bot token', 'DISCORD_TOKEN', 'Token of your Discord bot (Discord Developer Portal → Bot).', '', 'required|string|max:200', false),
            variable('Panel URL', 'PANEL_PUBLIC_URL', 'Public URL of this Pterodactyl panel.', panelUrl, 'required|url|max:255|regex:/^https:\\/\\//', false),
            variable('Agent ID', 'PTEROSYNC_AGENT_ID', 'Agent ID from Admin → PteroSync.', '', 'required|uuid', false),
            variable('Agent secret', 'PTEROSYNC_AGENT_SECRET', 'Agent secret from Admin → PteroSync.', '', 'required|string|max:128', false),
            variable('Language', 'DEFAULT_LOCALE', 'Default language of bot replies (en or sl).', 'en', 'required|in:en,sl', true),
            variable('Log level', 'LOG_LEVEL', 'DEBUG, INFO, WARNING or ERROR.', 'INFO', 'required|in:DEBUG,INFO,WARNING,ERROR', true),
            variable('Auto update', 'AUTO_UPDATE', 'Update the agent to the panel extension version on every start (1 = yes).', '1', 'required|in:0,1', true),
            variable('Console poll seconds', 'CONSOLE_POLL_SECONDS', 'How often game consoles are read for chat relay.', '3', 'required|integer|between:2,60', true),
        ],
    };
}

export function agentEggFile(panelUrl: string): string {
    return JSON.stringify(agentEgg(panelUrl), null, 4);
}
