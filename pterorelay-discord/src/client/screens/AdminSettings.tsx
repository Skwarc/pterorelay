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
    const [name, setName] = useState('PteroRelay agent');
    const [credentials, setCredentials] = useState<{agent_id: string; secret: string} | null>(null);
    const [overview, setOverview] = useState<Overview | null>(null);
    const [busy, setBusy] = useState(false);
    const load = async () => {
        try {
            const { data } = await http.get<Overview>('/api/admin/extensions/pterorelay-discord/overview');
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
            const { data } = await http.post<{agent_id: string; secret: string}>('/api/admin/extensions/pterorelay-discord/agents', { name: name.trim() });
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
            await http.delete(`/api/admin/extensions/pterorelay-discord/agents/${agent.public_id}`);
            setCredentials((current) => current?.agent_id === agent.public_id ? null : current);
            await load();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setBusy(false);
        }
    };
    return <PageContentBlock title="PteroRelay Discord">
        <div className="prelay:grid prelay:gap-6">
            <UpdateBanner />
            {overview && <SetupChecklist overview={overview} />}
            {overview && overview.agents.length > 0 && <Diagnostics overview={overview} />}
            <AgentDeploy agents={overview?.agents ?? []} invitePermissions={overview?.invite_permissions ?? 0} onDeployed={load} />
            <section className="prelay:grid prelay:gap-4 prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground">
                <h2 className="prelay:text-lg prelay:font-semibold">{t('agent')}</h2>
                <p className="prelay:text-sm prelay:text-muted-foreground">{t('agent_help')}</p>
                <div className="prelay:flex prelay:flex-col prelay:gap-3 prelay:sm:flex-row">
                    <Input value={name} maxLength={80} onChange={(event: ChangeEvent<HTMLInputElement>) => setName(event.target.value)} />
                    <Button disabled={busy || !name.trim()} onClick={create}>{t('create_agent')}</Button>
                </div>
                {credentials && <div className="prelay:grid prelay:gap-2">
                    <p className="prelay:text-sm prelay:font-semibold">Copy these values now. The secret cannot be shown again.</p>
                    <pre className="prelay:overflow-auto prelay:rounded prelay:bg-muted prelay:p-4 prelay:text-sm">{`PTERORELAY_AGENT_ID=${credentials.agent_id}\nPTERORELAY_AGENT_SECRET=${credentials.secret}`}</pre>
                    <Button onClick={() => setCredentials(null)}>I saved the secret</Button>
                </div>}
            </section>
            <section className="prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground">
                <h2 className="prelay:mb-4 prelay:text-lg prelay:font-semibold">Agents</h2>
                {!overview ? <Spinner centered /> : overview.agents.length === 0 ? <p className="prelay:text-sm prelay:text-muted-foreground">No agents have been created.</p> :
                    <div className="prelay:grid prelay:gap-3">{overview.agents.map((agent) => <div key={agent.public_id} className="prelay:flex prelay:items-center prelay:justify-between prelay:gap-4 prelay:rounded prelay:border prelay:border-border prelay:p-3">
                        <div><div className="prelay:font-medium">{agent.name}</div><div className="prelay:text-xs prelay:text-muted-foreground">{agent.version ?? 'Not connected'} · {agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : 'Never seen'}{agent.bot?.name ? ` · ${agent.bot.name}` : ''}{agent.server_uuid ? ' · runs as a panel server' : ''}</div></div>
                        <Button disabled={busy} onClick={() => void remove(agent)}>Delete</Button>
                    </div>)}</div>}
            </section>
            <section className="prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground">
                <h2 className="prelay:mb-2 prelay:text-lg prelay:font-semibold">Discord servers</h2>
                <p className="prelay:mb-4 prelay:text-sm prelay:text-muted-foreground">The integration channel is where the bot relays game chat and events by default; each server can override it in its Discord tab. Notifications receive server state changes.</p>
                {!overview ? <Spinner centered /> : overview.guilds.length === 0 ? <p className="prelay:text-sm prelay:text-muted-foreground">No Discord servers have been reported by an agent yet.</p> :
                    <div className="prelay:grid prelay:gap-3">{overview.guilds.map((guild) => <GuildChannels key={guild.id} guild={guild} onSaved={load} />)}</div>}
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
    const selectClass = 'prelay:min-h-10 prelay:w-full prelay:rounded prelay:border prelay:border-border prelay:bg-background prelay:px-3 prelay:text-foreground';
    const save = async () => {
        if (saving) return;
        setSaving(true);
        try {
            await http.put(`/api/admin/extensions/pterorelay-discord/guilds/${guild.id}`, {
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
    return <div className="prelay:grid prelay:gap-3 prelay:rounded prelay:border prelay:border-border prelay:p-3 prelay:md:grid-cols-[1fr_1fr_1fr_auto] prelay:md:items-end">
        <div className="prelay:font-medium">{guild.name}</div>
        <label className="prelay:grid prelay:gap-1 prelay:text-sm">Integration channel{picker(integration, setIntegration)}</label>
        <label className="prelay:grid prelay:gap-1 prelay:text-sm">Notification channel{picker(notification, setNotification)}</label>
        <Button disabled={saving} onClick={() => void save()}>Save</Button>
    </div>;
}
