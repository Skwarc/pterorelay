import { useEffect, useState } from 'react';
import { http } from '@pterodactyl/sdk';

export type Bot = {
    id: string | null; name: string | null; application_id: string | null;
    message_content: boolean | null; guild_count: number | null; latency_ms: number | null;
};
export type Problem = { kind: string; server: string | null; guild: string | null; message: string; since: string | null };
export type AgentInfo = {
    public_id: string; name: string; version: string | null; last_seen_at: string | null; server_uuid: string | null;
    bot: Bot | null; diagnostics: { problems?: Problem[]; relayed?: number; live?: number; websocket?: number } | null;
};
type GuildInfo = { id: number; name: string; integration_channel_id: string | null; notification_channel_id: string | null; channels: { discord_id: string; name: string; can_send: boolean; can_webhook: boolean }[] };
export type OverviewInfo = {
    agents: AgentInfo[]; guilds: GuildInfo[]; extension_version: string; invite_permissions: number;
    relayed_servers: number; linked_servers: number; servers?: Record<string, { name: string; identifier: string }>;
    php?: { version: string; vulnerable: boolean; fixed_in: string | null; upload_progress: boolean };
};
type Updates = { current: string; configured: boolean; latest?: { version: string; url: string } | null; update_available?: boolean };
type Check = { label: string; ok: boolean | null; detail: React.ReactNode };

/** An agent counts as connected when its last heartbeat (every 30 s) is under two minutes old. */
const isOnline = (agent: AgentInfo) => agent.last_seen_at !== null && Date.now() - new Date(agent.last_seen_at).getTime() < 120_000;

export function inviteUrl(applicationId: string, permissions: number): string {
    const query = new URLSearchParams({ client_id: applicationId, scope: 'bot applications.commands', permissions: String(permissions) });
    return `https://discord.com/oauth2/authorize?${query.toString()}`;
}

const card = 'prelay:grid prelay:gap-3 prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground';
const link = 'prelay:underline';

export function UpdateBanner() {
    const [updates, setUpdates] = useState<Updates | null>(null);
    useEffect(() => {
        http.get('/api/admin/extensions/pterorelay-discord/updates')
            .then((response: { data: unknown }) => setUpdates(response.data as Updates))
            .catch(() => setUpdates(null));
    }, []);
    if (!updates?.update_available || !updates.latest) return null;
    return <div className="prelay:rounded-lg prelay:border prelay:border-primary prelay:bg-primary/10 prelay:p-4 prelay:text-sm prelay:text-foreground">
        PteroRelay <b>{updates.latest.version}</b> is available (you have {updates.current}).{' '}
        <a className={link} href={updates.latest.url} target="_blank" rel="noreferrer">Release notes and download</a>.
        After installing it, restart the PteroRelay Agent server so the agent updates too.
    </div>;
}

/** Security warnings about the PHP that runs the panel (not about PteroRelay itself). */
export function PhpAdvisory({ php }: { php: OverviewInfo['php'] }) {
    if (!php || (!php.vulnerable && !php.upload_progress)) return null;
    return <div className="prelay:grid prelay:gap-2 prelay:rounded-lg prelay:border prelay:border-destructive prelay:bg-destructive/10 prelay:p-4 prelay:text-sm prelay:text-foreground" role="alert">
        {php.vulnerable && <p>
            <b>⚠ This panel runs PHP {php.version}, which has critical remote code execution flaws</b> (CVE-2026-109618 and
            CVE-2026-109619, &quot;PHP2Shell&quot;, exploited in the wild). Update PHP to {php.fixed_in ?? 'a supported, patched release (8.2.35, 8.3.36, 8.4.27 or 8.5.12)'} or
            newer and restart PHP-FPM. No configuration change protects against them.{' '}
            <a className={link} href="https://watchtowr.com/intelligence/php-cve-2026-109618-faq/" target="_blank" rel="noreferrer">Details</a>
        </p>}
        {php.upload_progress && <p>
            <b>Set <code>session.upload_progress.enabled = 0</code></b> in the panel&apos;s php.ini and restart PHP-FPM: PHP recommends it
            against CVE-2026-115560, which has no fix yet. The panel does not use upload progress.
        </p>}
    </div>;
}

export function SetupChecklist({ overview }: { overview: OverviewInfo }) {
    const agent = overview.agents.find(isOnline) ?? overview.agents[0] ?? null;
    const bot = agent?.bot ?? null;
    const online = agent !== null && isOnline(agent);
    const invite = bot?.application_id ? inviteUrl(bot.application_id, overview.invite_permissions) : null;
    const channelProblems = overview.guilds.flatMap((guild) => {
        const channel = guild.channels.find((item) => item.discord_id === guild.integration_channel_id);
        if (!guild.integration_channel_id) return [`${guild.name}: no integration channel`];
        if (!channel?.can_send) return [`${guild.name}: the bot cannot send in the integration channel`];
        if (!channel.can_webhook) return [`${guild.name}: Manage Webhooks missing in #${channel.name}`];
        return [];
    });
    const checks: Check[] = [
        {
            label: 'Agent connected',
            ok: online,
            detail: online ? `${agent?.name}, last heartbeat ${new Date(agent!.last_seen_at!).toLocaleTimeString()}`
                : agent ? 'The agent has not reported in the last two minutes. Check the PteroRelay Agent server console.'
                : 'Deploy the agent below.',
        },
        {
            label: 'Agent up to date',
            ok: agent?.version ? agent.version === overview.extension_version : null,
            detail: !agent?.version ? 'Unknown until the agent connects.'
                : agent.version === overview.extension_version ? `Version ${agent.version}.`
                : <>The agent runs {agent.version}, the extension is {overview.extension_version}. {agent.server_uuid
                    ? 'Restart the PteroRelay Agent server: it updates itself on start.'
                    : 'Update the agent (Docker: ./scripts/update-agent.sh).'}</>,
        },
        {
            label: 'Discord bot',
            ok: bot ? Boolean(bot.message_content) : null,
            detail: bot ? <>{bot.name} with Message Content intent{bot.latency_ms !== null ? `, ${bot.latency_ms} ms latency` : ''}.</>
                : 'Reported once the agent is connected.',
        },
        {
            label: 'Bot is in a Discord server',
            ok: bot ? (bot.guild_count ?? overview.guilds.length) > 0 : null,
            detail: <>{overview.guilds.length > 0 ? overview.guilds.map((guild) => guild.name).join(', ') : 'Not in any Discord server yet.'}
                {invite && <> <a className={link} href={invite} target="_blank" rel="noreferrer">Invite link</a> (with the permissions PteroRelay needs).</>}</>,
        },
        {
            label: 'Integration channels',
            ok: overview.guilds.length === 0 ? null : channelProblems.length === 0,
            detail: overview.guilds.length === 0 ? 'Set once the bot is in a Discord server.'
                : channelProblems.length === 0 ? 'Every Discord server has a usable integration channel.' : channelProblems.join('; '),
        },
        {
            label: 'Game chat on a server',
            ok: overview.relayed_servers > 0 ? true : overview.linked_servers > 0 ? false : null,
            detail: overview.relayed_servers > 0 ? `${overview.relayed_servers} server(s) relay chat.`
                : 'Open a server → Discord tab, link a Discord server, choose the game and enable chat.',
        },
    ];
    const icon = (ok: boolean | null) => ok === null ? '○' : ok ? '✓' : '✗';
    const tone = (ok: boolean | null) => ok === null ? 'prelay:text-muted-foreground' : ok ? 'prelay:text-success' : 'prelay:text-destructive';
    return <section className={card}>
        <h2 className="prelay:text-lg prelay:font-semibold">Setup checklist</h2>
        <ul className="prelay:grid prelay:gap-2 prelay:text-sm">{checks.map((check) => <li key={check.label} className="prelay:flex prelay:gap-3">
            <span className={`prelay:w-4 prelay:font-mono ${tone(check.ok)}`}>{icon(check.ok)}</span>
            <span><b>{check.label}.</b> <span className="prelay:text-muted-foreground">{check.detail}</span></span>
        </li>)}</ul>
    </section>;
}

export function Diagnostics({ overview }: { overview: OverviewInfo }) {
    const problems = overview.agents.flatMap((agent) => (agent.diagnostics?.problems ?? []).map((problem) => ({ agent, problem })));
    const stats = overview.agents.map((agent) => agent.diagnostics).filter(Boolean);
    const server = (uuid: string | null) => uuid && overview.servers?.[uuid] ? overview.servers[uuid] : null;
    return <section className={card}>
        <h2 className="prelay:text-lg prelay:font-semibold">Diagnostics</h2>
        {stats.length > 0 && <p className="prelay:text-sm prelay:text-muted-foreground">
            Reading {stats.reduce((sum, item) => sum + (item?.relayed ?? 0), 0)} server console(s), {stats.reduce((sum, item) => sum + (item?.live ?? 0), 0)} working: {stats.reduce((sum, item) => sum + (item?.websocket ?? 0), 0)} via the Wings websocket, the rest by polling.
        </p>}
        {problems.length === 0 ? <p className="prelay:text-sm">✓ No problems reported by the agent.</p>
            : <ul className="prelay:grid prelay:gap-2 prelay:text-sm">{problems.map(({ agent, problem }, index) => {
                const target = server(problem.server);
                return <li key={`${agent.public_id}-${index}`} className="prelay:rounded prelay:border prelay:border-border prelay:p-3">
                    <div className="prelay:font-medium">{target ? <a className={link} href={`/server/${target.identifier}/discord`}>{target.name}</a> : problem.server ?? agent.name}</div>
                    <div>{problem.message}</div>
                    {problem.since && <div className="prelay:text-xs prelay:text-muted-foreground">Since {new Date(problem.since).toLocaleString()}</div>}
                </li>;
            })}</ul>}
    </section>;
}
