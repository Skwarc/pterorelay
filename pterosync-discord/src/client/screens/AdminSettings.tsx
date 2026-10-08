import { useEffect, useState, type ChangeEvent } from 'react';
import { Button, Input, PageContentBlock, Spinner, http, httpErrorToHuman, toast, useExtensionTranslation } from '@pterodactyl/sdk';
import AgentDeploy from '../AgentDeploy';
import { Diagnostics, SetupChecklist, UpdateBanner, type AgentInfo, type OverviewInfo } from '../SetupChecklist';

type Agent = AgentInfo;
type Channel = { discord_id: string; name: string; can_send: boolean; can_webhook: boolean };
type Guild = { id: number; name: string; integration_channel_id: string | null; notification_channel_id: string | null; channels: Channel[] };
type Overview = OverviewInfo & { agents: Agent[]; guilds: Guild[] };

export default function AdminSettings() {
    const { t } = useExtensionTranslation('messages');
    const [name, setName] = useState('PteroSync agent');
    const [credentials, setCredentials] = useState<{agent_id: string; secret: string} | null>(null);
    const [overview, setOverview] = useState<Overview | null>(null);
    const [busy, setBusy] = useState(false);
    const load = async () => {
        try {
            const { data } = await http.get<Overview>('/api/admin/extensions/pterosync-discord/overview');
            setOverview(data);
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        }
    };
    useEffect(() => { void load(); }, []);
    const create = async () => {
        if (!name.trim() || busy) return;
        setBusy(true);
        try {
            const { data } = await http.post<{agent_id: string; secret: string}>('/api/admin/extensions/pterosync-discord/agents', { name: name.trim() });
            setCredentials(data);
            toast.success('Agent credentials created. Save the secret now.');
            await load();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setBusy(false);
        }
    };
    const remove = async (agent: Agent) => {
        if (!window.confirm(`Delete agent “${agent.name}”?`) || busy) return;
        setBusy(true);
        try {
            await http.delete(`/api/admin/extensions/pterosync-discord/agents/${agent.public_id}`);
            setCredentials((current) => current?.agent_id === agent.public_id ? null : current);
            await load();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setBusy(false);
        }
    };
    return <PageContentBlock title="PteroSync Discord">
        <div className="psync:grid psync:gap-6">
            <UpdateBanner />
            {overview && <SetupChecklist overview={overview} />}
            {overview && overview.agents.length > 0 && <Diagnostics overview={overview} />}
            <AgentDeploy agents={overview?.agents ?? []} invitePermissions={overview?.invite_permissions ?? 0} onDeployed={load} />
            <section className="psync:grid psync:gap-4 psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
                <h2 className="psync:text-lg psync:font-semibold">{t('agent')}</h2>
                <p className="psync:text-sm psync:text-muted-foreground">{t('agent_help')}</p>
                <div className="psync:flex psync:flex-col psync:gap-3 psync:sm:flex-row">
                    <Input value={name} maxLength={80} onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.target.value)} />
                    <Button disabled={busy || !name.trim()} onClick={create}>{t('create_agent')}</Button>
                </div>
                {credentials && <div className="psync:grid psync:gap-2">
                    <p className="psync:text-sm psync:font-semibold">Copy these values now. The secret cannot be shown again.</p>
                    <pre className="psync:overflow-auto psync:rounded psync:bg-muted psync:p-4 psync:text-sm">{`PTEROSYNC_AGENT_ID=${credentials.agent_id}\nPTEROSYNC_AGENT_SECRET=${credentials.secret}`}</pre>
                    <Button onClick={() => setCredentials(null)}>I saved the secret</Button>
                </div>}
            </section>
            <section className="psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
                <h2 className="psync:mb-4 psync:text-lg psync:font-semibold">Agents</h2>
                {!overview ? <Spinner centered /> : overview.agents.length === 0 ? <p className="psync:text-sm psync:text-muted-foreground">No agents have been created.</p> :
                    <div className="psync:grid psync:gap-3">{overview.agents.map((agent) => <div key={agent.public_id} className="psync:flex psync:items-center psync:justify-between psync:gap-4 psync:rounded psync:border psync:border-border psync:p-3">
                        <div><div className="psync:font-medium">{agent.name}</div><div className="psync:text-xs psync:text-muted-foreground">{agent.version ?? 'Not connected'} · {agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : 'Never seen'}{agent.bot?.name ? ` · ${agent.bot.name}` : ''}{agent.server_uuid ? ' · runs as a panel server' : ''}</div></div>
                        <Button disabled={busy} onClick={() => void remove(agent)}>Delete</Button>
                    </div>)}</div>}
            </section>
            <section className="psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
                <h2 className="psync:mb-2 psync:text-lg psync:font-semibold">Discord servers</h2>
                <p className="psync:mb-4 psync:text-sm psync:text-muted-foreground">The integration channel is where the bot relays game chat and events by default; each server can override it in its Discord tab. Notifications receive server state changes.</p>
                {!overview ? <Spinner centered /> : overview.guilds.length === 0 ? <p className="psync:text-sm psync:text-muted-foreground">No Discord servers have been reported by an agent yet.</p> :
                    <div className="psync:grid psync:gap-3">{overview.guilds.map((guild) => <GuildChannels key={guild.id} guild={guild} onSaved={load} />)}</div>}
            </section>
        </div>
    </PageContentBlock>;
}

function GuildChannels({ guild, onSaved }: { guild: Guild; onSaved: () => Promise<void> }) {
    const [integration, setIntegration] = useState(guild.integration_channel_id ?? '');
    const [notification, setNotification] = useState(guild.notification_channel_id ?? '');
    const [saving, setSaving] = useState(false);
    const stored = [guild.integration_channel_id, guild.notification_channel_id];
    const channels = (guild.channels ?? []).filter((channel) => channel.can_send || stored.includes(channel.discord_id));
    const selectClass = 'psync:min-h-10 psync:w-full psync:rounded psync:border psync:border-border psync:bg-background psync:px-3 psync:text-foreground';
    const save = async () => {
        if (saving) return;
        setSaving(true);
        try {
            await http.put(`/api/admin/extensions/pterosync-discord/guilds/${guild.id}`, {
                integration_channel_id: integration || null,
                notification_channel_id: notification || null,
            });
            toast.success(`Channels for ${guild.name} saved.`);
            await onSaved();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setSaving(false);
        }
    };
    const picker = (value: string, setValue: (value: string) => void) => <select className={selectClass} value={value} onChange={(event) => setValue(event.target.value)}>
        <option value="">Not set</option>
        {channels.map((channel) => <option key={channel.discord_id} value={channel.discord_id}>#{channel.name}{channel.can_webhook ? '' : ' (no webhook permission)'}</option>)}
    </select>;
    return <div className="psync:grid psync:gap-3 psync:rounded psync:border psync:border-border psync:p-3 psync:md:grid-cols-[1fr_1fr_1fr_auto] psync:md:items-end">
        <div className="psync:font-medium">{guild.name}</div>
        <label className="psync:grid psync:gap-1 psync:text-sm">Integration channel{picker(integration, setIntegration)}</label>
        <label className="psync:grid psync:gap-1 psync:text-sm">Notification channel{picker(notification, setNotification)}</label>
        <Button disabled={saving} onClick={() => void save()}>Save</Button>
    </div>;
}
