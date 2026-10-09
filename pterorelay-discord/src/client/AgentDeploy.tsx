import { useEffect, useState, type ChangeEvent } from 'react';
import { Button, Input, http, httpErrorToHuman, toast, useCurrentUser } from '@pterodactyl/sdk';
import { AGENT_EGG_NAME, agentEgg, agentEggFile } from './agentEgg';
import { inviteUrl } from './SetupChecklist';

type Resource<T> = { object: string; attributes: T };
type Paginated<T> = { data: Resource<T>[] };
type Node = { id: number; name: string; fqdn: string; maintenance_mode: boolean };
type Egg = { id: number; name: string; startup: string; docker_image: string; docker_images: Record<string, string> };
type Allocation = { id: number; ip: string; alias: string | null; port: number };
type Server = { id: number; uuid: string; identifier: string; name: string };
type Step = { label: string; state: 'running' | 'done' | 'failed' | 'warning'; detail?: string };
type ExistingAgent = { public_id: string; name: string; version: string | null; last_seen_at: string | null };
type TokenCheck = { application_id: string; name: string; bot_name: string; message_content: boolean; invite_url: string };

const AGENT_IMAGE = 'ghcr.io/parkervcp/yolks:python_3.11';
const AGENTS_API = '/api/admin/extensions/pterorelay-discord/agents';

function eggUpload(panelUrl: string): FormData {
    const form = new FormData();
    form.append('import_file', new Blob([agentEggFile(panelUrl)], { type: 'application/json' }), 'egg-pterorelay-agent.json');
    return form;
}

/** Runs the Discord agent as a server on one of the panel's own nodes. */
export default function AgentDeploy({ agents, invitePermissions, onDeployed }: { agents: ExistingAgent[]; invitePermissions: number; onDeployed: () => Promise<void> }) {
    const user = useCurrentUser();
    const panelUrl = window.location.origin;
    const [token, setToken] = useState('');
    const [nodes, setNodes] = useState<Node[]>([]);
    const [nodeId, setNodeId] = useState('');
    // '' creates new credentials; an agent id moves that agent (its guilds, links and settings) to the server.
    const [replaceId, setReplaceId] = useState('');
    const [steps, setSteps] = useState<Step[]>([]);
    const [busy, setBusy] = useState(false);
    const [server, setServer] = useState<Server | null>(null);
    const [tokenCheck, setTokenCheck] = useState<TokenCheck | null>(null);
    const [checking, setChecking] = useState(false);
    const checkToken = async () => {
        if (!token.trim() || checking) return;
        setChecking(true);
        setTokenCheck(null);
        try {
            setTokenCheck((await http.post('/api/admin/extensions/pterorelay-discord/discord/validate-token', { token: token.trim() })).data as TokenCheck);
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setChecking(false);
        }
    };

    useEffect(() => {
        http.get('/api/admin/nodes', { params: { per_page: 100 } })
            .then((response: { data: unknown }) => {
                const list = (response.data as Paginated<Node>).data.map((item) => item.attributes).filter((node) => !node.maintenance_mode);
                setNodes(list);
                if (list.length === 1) setNodeId(String(list[0].id));
            })
            .catch((error: unknown) => toast.error(httpErrorToHuman(error)));
    }, []);

    const downloadEgg = () => {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([agentEggFile(panelUrl)], { type: 'application/json' }));
        link.download = 'egg-pterorelay-agent.json';
        link.click();
        URL.revokeObjectURL(link.href);
    };

    const deploy = async () => {
        if (busy || !token.trim() || !nodeId) return;
        if (!panelUrl.startsWith('https://')) {
            // The agent downloads and runs its code from this URL, so it must be HTTPS.
            toast.error('Open the panel over https:// to deploy the agent: it installs and updates itself from this address.');
            return;
        }
        setBusy(true);
        setServer(null);
        const log: Step[] = [];
        const begin = (label: string) => { log.push({ label, state: 'running' }); setSteps([...log]); };
        const finish = (state: Step['state'], detail?: string) => { log[log.length - 1] = { ...log[log.length - 1], state, detail }; setSteps([...log]); };
        let agentId: string | null = null;
        const replacing = agents.find((agent) => agent.public_id === replaceId) ?? null;
        try {
            begin('Import the PteroRelay Agent egg');
            const eggs = (await http.get('/api/admin/eggs', { params: { 'filter[name]': AGENT_EGG_NAME, per_page: 100 } })).data as Paginated<Egg>;
            let egg = eggs.data.map((item) => item.attributes).find((item) => item.name === AGENT_EGG_NAME) ?? null;
            if (egg) {
                try {
                    egg = ((await http.post(`/api/admin/eggs/${egg.id}/import`, eggUpload(panelUrl))).data as Resource<Egg>).attributes;
                    finish('done', 'Updated the existing egg.');
                } catch (error) {
                    finish('warning', `Kept the existing egg (update failed: ${httpErrorToHuman(error)}).`);
                }
            } else {
                egg = ((await http.post('/api/admin/eggs/import', eggUpload(panelUrl))).data as Resource<Egg>).attributes;
                finish('done');
            }

            begin('Find a free allocation on the node');
            const allocations = (await http.get(`/api/admin/nodes/${nodeId}/allocations/available`, { params: { per_page: 1 } })).data as Paginated<Allocation>;
            const allocation = allocations.data[0]?.attributes;
            if (!allocation) throw new Error('The node has no free allocation. Add one under the node\'s allocations and try again.');
            finish('done', `${allocation.alias ?? allocation.ip}:${allocation.port} (the agent does not listen on it)`);

            let credentials: { agent_id: string; secret: string };
            if (replacing) {
                begin(`Issue a new secret for “${replacing.name}”`);
                credentials = (await http.post(`${AGENTS_API}/${replacing.public_id}/rotate-secret`)).data as { agent_id: string; secret: string };
                finish('done', 'Its Discord servers, links and settings stay. Wherever it ran before can no longer reach the panel — stop that copy.');
            } else {
                begin('Create agent credentials');
                credentials = (await http.post(AGENTS_API, { name: 'PteroRelay agent (server)' })).data as { agent_id: string; secret: string };
                agentId = credentials.agent_id;
                finish('done');
            }

            begin('Create the agent server');
            const ownerId = user.id ?? ((await http.get('/api/client/account')).data as Resource<{ id: number }>).attributes.id;
            const created = (await http.post('/api/admin/servers', {
                name: 'PteroRelay Agent',
                description: 'Discord agent for the PteroRelay extension. It never controls its own server.',
                owner_id: ownerId,
                egg_id: egg.id,
                docker_image: Object.values(egg.docker_images ?? {})[0] ?? egg.docker_image ?? AGENT_IMAGE,
                startup: egg.startup || agentEgg(panelUrl).startup,
                environment: {
                    DISCORD_TOKEN: token.trim(),
                    PANEL_PUBLIC_URL: panelUrl,
                    PTERORELAY_AGENT_ID: credentials.agent_id,
                    PTERORELAY_AGENT_SECRET: credentials.secret,
                    DEFAULT_LOCALE: 'en',
                    LOG_LEVEL: 'INFO',
                    AUTO_UPDATE: '1',
                    CONSOLE_POLL_SECONDS: '3',
                },
                // The agent idles at ~45 MB; the headroom covers pip on first start and large guilds.
                memory: 128, swap: 0, disk: 1024, io: 500, cpu: 50,
                database_limit: 0, allocation_limit: 0, backup_limit: 0,
                primary_allocation_id: allocation.id,
                skip_scripts: false,
                start_on_completion: true,
            })).data as Resource<Server>;
            agentId = null;
            setServer(created.attributes);
            finish('done', 'The server installs the agent from this panel and starts it.');
            setToken('');
            toast.success('PteroRelay agent deployed.');
            await onDeployed();
        } catch (error) {
            finish('failed', error instanceof Error && !('isAxiosError' in error) ? error.message : httpErrorToHuman(error));
            if (replacing && log.some((step) => step.label.startsWith('Issue a new secret') && step.state === 'done')) {
                toast.error(`“${replacing.name}” now has a new secret that only exists in this failed attempt. Run Deploy again to finish moving it.`);
            }
            if (agentId) {
                // Do not leave unused credentials behind when the server could not be created.
                await http.delete(`${AGENTS_API}/${agentId}`).catch(() => undefined);
            }
        } finally {
            setBusy(false);
        }
    };

    const selectClass = 'prelay:min-h-10 prelay:w-full prelay:rounded prelay:border prelay:border-border prelay:bg-background prelay:px-3 prelay:text-foreground';
    const icon: Record<Step['state'], string> = { running: '…', done: '✓', failed: '✗', warning: '!' };
    return <section className="prelay:grid prelay:gap-4 prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground">
        <h2 className="prelay:text-lg prelay:font-semibold">Run the agent on this panel</h2>
        <p className="prelay:text-sm prelay:text-muted-foreground">
            Deploys the Discord agent as a server on one of your nodes: the PteroRelay Agent egg is imported, credentials are
            created and the server installs and updates the agent from this panel. You only need your Discord bot token
            (Discord Developer Portal → your application → Bot, with the Message Content intent enabled).
        </p>
        <div className="prelay:grid prelay:gap-3 prelay:md:grid-cols-2">
            <label className="prelay:grid prelay:gap-1 prelay:text-sm">Discord bot token
                <Input type="password" autoComplete="off" value={token} onChange={(event: ChangeEvent<HTMLInputElement>) => setToken(event.target.value)} />
            </label>
            <label className="prelay:grid prelay:gap-1 prelay:text-sm prelay:md:col-span-2">Agent
                <select className={selectClass} value={replaceId} onChange={(event) => setReplaceId(event.target.value)}>
                    <option value="">Create a new agent</option>
                    {agents.map((agent) => <option key={agent.public_id} value={agent.public_id}>Move “{agent.name}” here (keeps its Discord servers and settings)</option>)}
                </select>
            </label>
            <label className="prelay:grid prelay:gap-1 prelay:text-sm">Node
                <select className={selectClass} value={nodeId} onChange={(event) => setNodeId(event.target.value)}>
                    <option value="">Select a node</option>
                    {nodes.map((node) => <option key={node.id} value={node.id}>{node.name} ({node.fqdn})</option>)}
                </select>
            </label>
        </div>
        <div className="prelay:flex prelay:flex-wrap prelay:items-center prelay:gap-3">
            <Button disabled={checking || !token.trim()} onClick={() => void checkToken()}>Check token</Button>
            <Button disabled={busy || !token.trim() || !nodeId} onClick={() => void deploy()}>Deploy agent</Button>
            <button type="button" className="prelay:text-sm prelay:underline" onClick={downloadEgg}>Download the egg to import it yourself</button>
        </div>
        {tokenCheck && <div className="prelay:grid prelay:gap-1 prelay:text-sm">
            <span className="prelay:text-green-500">✓ Token belongs to {tokenCheck.bot_name || tokenCheck.name}.</span>
            {tokenCheck.message_content
                ? <span className="prelay:text-green-500">✓ Message Content intent is enabled.</span>
                : <span className="prelay:text-red-500">✗ Message Content intent is off: enable it in the Developer Portal (Bot → Privileged Gateway Intents), or the bot will not start.</span>}
            <span>Add the bot to your Discord server: <a className="prelay:underline" href={tokenCheck.invite_url || inviteUrl(tokenCheck.application_id, invitePermissions)} target="_blank" rel="noreferrer">invite link</a>.</span>
        </div>}
        {replaceId && <p className="prelay:text-sm prelay:text-muted-foreground">
            Moving issues the agent a new secret, so a copy running elsewhere (for example Docker) stops reaching the panel.
            Stop that copy afterwards — it is still connected to Discord with the same bot token.
        </p>}
        {steps.length > 0 && <ol className="prelay:grid prelay:gap-1 prelay:text-sm">{steps.map((step) =>
            <li key={step.label} className={step.state === 'failed' ? 'prelay:text-red-500' : ''}>
                <span className="prelay:inline-block prelay:w-5 prelay:font-mono">{icon[step.state]}</span>{step.label}
                {step.detail && <span className="prelay:text-muted-foreground"> — {step.detail}</span>}
            </li>)}
        </ol>}
        {server && <p className="prelay:text-sm">
            Open <a className="prelay:underline" href={`/server/${server.identifier}`}>{server.name}</a> to follow the installation.
            When its console shows “PteroRelay agent ready”, the agent appears as connected below.
        </p>}
    </section>;
}
