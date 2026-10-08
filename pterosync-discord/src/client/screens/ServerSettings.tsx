import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Button, ServerContentBlock, Spinner, http, httpErrorToHuman, toast, useCurrentServerRequired, useExtensionTranslation } from '@pterodactyl/sdk';

type Permission = {
    discord_role_id: string; can_view: boolean; can_power: boolean; can_console: boolean; can_configure: boolean;
    can_chat?: boolean; chat_label?: string | null; chat_color?: string | null;
};
type Role = { discord_id: string; name: string; managed: boolean };
type Channel = { discord_id: string; name: string; can_send: boolean; can_webhook: boolean };
type PatternKey = 'chat' | 'broadcast' | 'join' | 'leave' | 'advancement' | 'death' | 'server_ready' | 'server_stop';
type OutConfig = { template?: string | null; style?: string; escape?: string; max_length?: number; label?: string; strip?: string | null };
type InConfig = { strip_ansi?: boolean; line_prefix?: string | null; patterns?: Partial<Record<PatternKey, string | null>>; ignore?: string[] };
type AdapterConfig = { out?: OutConfig; in?: InConfig; avatar_url?: string | null };
type PresetStatus = 'verified' | 'likely' | 'unverified';
type Adapter = { id: string; name: string; notes?: string; status?: PresetStatus | null; capabilities: string[]; defaults: AdapterConfig };
const STATUS_TEXT: Record<PresetStatus, string> = {
    verified: 'Verified against real server output.',
    likely: 'Based on documentation and other tools, not yet confirmed on a real server. Check your console with the line tester.',
    unverified: 'Best-effort patterns. Check your console with the line tester and adjust them under advanced settings.',
};
type EventKey = 'join' | 'leave' | 'death' | 'advancement' | 'broadcast' | 'server';
type Binding = {
    id: number; guild_name: string; permissions: Permission[]; available_roles: Role[];
    available_channels: Channel[]; adapters: Adapter[]; game_adapter: string | null;
    adapter_config: AdapterConfig | null; event_colors: Partial<Record<EventKey, string>> | null;
    chat_enabled: boolean; chat_channel_id: string | null; integration_channel_id: string | null;
    /** This server's start/stop channel; null uses the Discord server's notification channel. */
    notification_channel_id?: string | null;
    /** The Discord server's notification channel, when the panel reports it. */
    guild_notification_channel_id?: string | null;
    disabled_features: string[];
    /** Preset matching the server's egg or Docker image, only set while no game is chosen. */
    suggested_adapter?: { id: string; source: 'egg' | 'image'; value: string } | null;
    /** The bot's last heartbeat was within 90 seconds when the page loaded. */
    agent_online?: boolean;
    agent_last_seen_at?: string | null;
    /** Set while the bot is no longer in this Discord server. */
    guild_left_at?: string | null;
};
type LibraryEntry = { id: string; name: string; game: string; description: string; author: string; status: PresetStatus };
type Guild = { id: number; name: string };
/** Invite link of a deployed bot, for adding it to the customer's own Discord before /link. */
type Invite = { name: string; url: string };

const PATTERN_KEYS: PatternKey[] = ['chat', 'broadcast', 'join', 'leave', 'advancement', 'death', 'server_ready', 'server_stop'];
const STYLES = ['plain', 'minecraft_json', 'minecraft_legacy', 'terraria', 'unity_rich'];
const ESCAPES = ['none', 'json_string', 'double_quotes'];
const EVENT_DEFAULTS: Record<EventKey, string> = {
    join: '#57F287', leave: '#ED4245', death: '#4F545C', advancement: '#FEE75C', broadcast: '#EB459E', server: '#5865F2',
};
/** Display order of the feature toggles; only features the game supports are shown. */
const FEATURE_ORDER = ['chat_out', 'colors_out', 'chat_in', 'join_leave', 'death', 'advancements', 'server_messages', 'server_status'];
const CAPABILITY_LABELS: Record<string, string> = {
    chat_out: 'Discord → game', chat_in: 'Game → Discord', join_leave: 'Join / leave', death: 'Deaths', colors_out: 'Role colours in-game',
    server_messages: 'Server messages', advancements: 'Advancements', server_status: 'Server start / stop',
};
const fieldClass = 'psync:min-h-10 psync:w-full psync:rounded psync:border psync:border-border psync:bg-background psync:px-3 psync:text-foreground';
const monoClass = `${fieldClass} psync:font-mono psync:text-xs`;

export default function ServerSettings() {
    const { t } = useExtensionTranslation('messages');
    const server = useCurrentServerRequired();
    const [bindings, setBindings] = useState<Binding[] | null>(null);
    const [guilds, setGuilds] = useState<Guild[]>([]);
    const [selectedGuild, setSelectedGuild] = useState('');
    const [linkCode, setLinkCode] = useState('');
    const [canLinkAny, setCanLinkAny] = useState(false);
    const [canConsole, setCanConsole] = useState(false);
    const [busy, setBusy] = useState(false);
    const [invites, setInvites] = useState<Invite[]>([]);
    const [showLink, setShowLink] = useState(false);
    const base = `/api/client/servers/${server.attributes.identifier}/extensions/pterosync-discord/settings`;
    const load = async () => {
        try {
            const { data } = await http.get<{ bindings: Binding[]; guilds: Guild[]; can_link_any?: boolean; can_console?: boolean; invites?: Invite[] }>(base);
            setBindings(data.bindings); setGuilds(data.guilds); setInvites(data.invites ?? []);
            setCanLinkAny(Boolean(data.can_link_any)); setCanConsole(Boolean(data.can_console));
        } catch (error) {
            toast.error(httpErrorToHuman(error));
            setBindings([]);
        }
    };
    useEffect(() => { void load(); }, [server.attributes.identifier]);
    if (!bindings) return <Spinner centered />;
    const link = async () => {
        if ((!selectedGuild && !linkCode.trim()) || busy) return;
        setBusy(true);
        try {
            await http.post(base, linkCode.trim() ? { code: linkCode.trim() } : { guild_id: Number(selectedGuild) });
            setSelectedGuild('');
            setLinkCode('');
            await load();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setBusy(false);
        }
    };
    // The setup guide is open while nothing is linked; afterwards it folds into "Link another Discord server".
    const linked = bindings.length > 0;
    const step = 'psync:flex psync:size-6 psync:shrink-0 psync:items-center psync:justify-center psync:rounded-full psync:border psync:border-border psync:text-xs';
    return <ServerContentBlock title={t('permissions')}>
        <div className="psync:grid psync:gap-6"><div className="psync:grid psync:gap-3 psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-4 psync:text-sm psync:text-foreground">
            {linked
                ? <button type="button" className="psync:w-fit psync:text-sm psync:underline" onClick={() => setShowLink(!showLink)}>{showLink ? 'Hide' : 'Link another Discord server'}</button>
                : <p>{t('no_guild')} Link your Discord server in three steps:</p>}
            {(!linked || showLink) && <ol className="psync:grid psync:gap-3">
                <li className="psync:flex psync:gap-3"><span className={step}>1</span><div className="psync:grid psync:gap-2">
                    <span>Add the bot to your Discord server.</span>
                    {invites.length > 0
                        ? <div className="psync:flex psync:flex-wrap psync:gap-2">{invites.map((invite) => <a key={invite.url} href={invite.url} target="_blank" rel="noreferrer"
                            className="psync:inline-flex psync:min-h-10 psync:items-center psync:rounded psync:border psync:border-primary psync:bg-primary/15 psync:px-4 psync:text-foreground">
                            {invites.length > 1 ? `Add ${invite.name}` : 'Add the bot to your Discord'}</a>)}</div>
                        : <span className="psync:text-muted-foreground">The administrator has not deployed a Discord bot yet.</span>}
                </div></li>
                <li className="psync:flex psync:gap-3"><span className={step}>2</span>
                    <span>In your Discord, run <code>/link</code>. It needs the Manage Server permission and replies with a code.</span></li>
                <li className="psync:flex psync:gap-3"><span className={step}>3</span><div className="psync:grid psync:flex-1 psync:gap-2">
                    <span>Enter the code here.</span>
                    <div className="psync:flex psync:flex-col psync:gap-3 psync:sm:flex-row">
                        <input className="psync:min-h-10 psync:flex-1 psync:rounded psync:border psync:border-border psync:bg-background psync:px-3 psync:font-mono psync:text-foreground"
                            placeholder="Code from /link (e.g. ABCDE-23456)" value={linkCode} onChange={(event) => setLinkCode(event.target.value)} />
                        {canLinkAny && <select className="psync:min-h-10 psync:flex-1 psync:rounded psync:border psync:border-border psync:bg-background psync:px-3 psync:text-foreground" value={selectedGuild} disabled={Boolean(linkCode.trim())} onChange={(event) => setSelectedGuild(event.target.value)}><option value="">{t('select_guild')} (admin)</option>{guilds.map((guild) => <option key={guild.id} value={guild.id}>{guild.name}</option>)}</select>}
                        <Button disabled={busy || (!selectedGuild && !linkCode.trim())} onClick={() => void link()}>{t('link_server')}</Button>
                    </div>
                </div></li>
            </ol>}
        </div>{bindings.map((binding) => <div key={binding.id} className="psync:grid psync:gap-4">
            <BotStatusBanner binding={binding} onShowInvite={() => setShowLink(true)} />
            <IntegrationEditor base={base} binding={binding} canConsole={canConsole} onSaved={load} />
            <RoleEditor base={base} binding={binding} onSaved={load} />
        </div>)}</div>
    </ServerContentBlock>;
}

/** "5 minutes ago" style text for an ISO timestamp; "never" when there is none. */
function timeAgo(iso: string | null | undefined): string {
    const time = iso ? new Date(iso).getTime() : NaN;
    if (Number.isNaN(time)) return 'never';
    const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
    const units: [string, number][] = [['day', 86_400], ['hour', 3_600], ['minute', 60]];
    for (const [unit, size] of units) {
        const count = Math.floor(seconds / size);
        if (count >= 1) return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
    }
    return 'just now';
}

/** Warning above a linked Discord server's settings while its bot is offline or no longer in that Discord server. */
function BotStatusBanner({ binding, onShowInvite }: { binding: Binding; onShowInvite: () => void }) {
    const box = 'psync:rounded-lg psync:border psync:border-yellow-500 psync:bg-yellow-500/10 psync:p-4 psync:text-sm psync:text-yellow-500';
    if (binding.agent_online === false) {
        return <p className={box} role="alert">⚠ {binding.guild_name}: The Discord bot is offline (last seen {timeAgo(binding.agent_last_seen_at)}). Chat relay and Discord commands are paused until it is back. If this lasts, contact your host.</p>;
    }
    if (binding.guild_left_at) {
        return <p className={box} role="alert">⚠ {binding.guild_name}: The bot is no longer in this Discord server. Invite it again with the button above, or unlink this Discord server.{' '}
            <button type="button" className="psync:underline" onClick={onShowInvite}>Show the invite button</button></p>;
    }
    return null;
}

type Advanced = {
    template: string; style: string; escape: string; max_length: string; label: string; strip: string;
    line_prefix: string; patterns: Record<PatternKey, string>; ignore: string; avatar_url: string;
};

function advancedFrom(defaults: AdapterConfig, overrides: AdapterConfig | null): Advanced {
    const out = { ...(defaults.out ?? {}), ...(overrides?.out ?? {}) };
    const input = { ...(defaults.in ?? {}), ...(overrides?.in ?? {}) };
    const patterns = { ...(defaults.in?.patterns ?? {}), ...(overrides?.in?.patterns ?? {}) };
    return {
        template: out.template ?? '', style: out.style ?? 'plain', escape: out.escape ?? 'none',
        max_length: String(out.max_length ?? 256), label: out.label ?? '[Discord]', strip: out.strip ?? '',
        line_prefix: input.line_prefix ?? '',
        patterns: Object.fromEntries(PATTERN_KEYS.map((key) => [key, patterns[key] ?? ''])) as Record<PatternKey, string>,
        ignore: (input.ignore ?? []).join('\n'),
        avatar_url: (overrides?.avatar_url ?? defaults.avatar_url) ?? '',
    };
}

/** Only the fields that differ from the preset are stored as overrides. */
function overridesFrom(defaults: AdapterConfig, form: Advanced): AdapterConfig | null {
    const base = advancedFrom(defaults, null);
    const out: OutConfig = {};
    if (form.template !== base.template) out.template = form.template;
    if (form.style !== base.style) out.style = form.style;
    if (form.escape !== base.escape) out.escape = form.escape;
    if (form.max_length !== base.max_length) out.max_length = Number(form.max_length);
    if (form.label !== base.label) out.label = form.label;
    if (form.strip !== base.strip) out.strip = form.strip;
    const input: InConfig = {};
    if (form.line_prefix !== base.line_prefix) input.line_prefix = form.line_prefix;
    const patterns: Partial<Record<PatternKey, string>> = {};
    PATTERN_KEYS.forEach((key) => { if (form.patterns[key] !== base.patterns[key]) patterns[key] = form.patterns[key]; });
    if (Object.keys(patterns).length) input.patterns = patterns;
    if (form.ignore !== base.ignore) input.ignore = form.ignore.split('\n').map((line) => line.trim()).filter(Boolean);
    const config: AdapterConfig = {};
    if (Object.keys(out).length) config.out = out;
    if (Object.keys(input).length) config.in = input;
    if (form.avatar_url !== base.avatar_url) config.avatar_url = form.avatar_url || null;
    return Object.keys(config).length ? config : null;
}

/** Same rules as the agent (adapters/engine.py): templates, "||" alternatives, "re:"/"^" regexes. */
const AUTO_PREFIXES = [
    /^\[[^\]]*\d{1,2}:\d{2}[^\]]*\](?:\s*\[[^\]]*\])*\s*:?\s*/,
    /^L \d{2}\/\d{2}\/\d{4} - \d{2}:\d{2}:\d{2}:\s*/,
    /^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:[.,:]\d+)?(?:Z|[+-]\d{2}:?\d{2})?:?\s*(?:\[?(?:INFO|WARN(?:ING)?|ERROR|DEBUG)\]?:?\s*)?/,
    /^\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?:?\s*/,
];
const TEMPLATE_TOKENS: Record<string, string> = { player: '(?<player>.+?)', message: '(?<message>.+)', rank: '(?<rank>.+?)', '*': '.*?' };

function templateToRegex(template: string): string {
    const literal = (text: string) => text.split(/\s+/).map((chunk) => chunk.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')).join('\\s+');
    const used = new Set<string>();
    let position = 0;
    let source = '';
    for (const match of template.matchAll(/\{(player|message|rank|\*)\}/g)) {
        const name = match[1];
        source += literal(template.slice(position, match.index)) + (used.has(name) ? '.+?' : TEMPLATE_TOKENS[name]);
        if (name !== '*') used.add(name);
        position = (match.index ?? 0) + match[0].length;
    }
    return `^${source}${literal(template.slice(position))}$`;
}

/** Python named groups use (?P<name>…); JavaScript uses (?<name>…). */
function compilePattern(pattern: string): RegExp[] {
    const value = pattern.trim();
    const regex = (source: string) => new RegExp(source.replace(/\(\?P</g, '(?<').replace(/\(\?P=(\w+)\)/g, '\\k<$1>'));
    if (value.startsWith('re:')) return [regex(value.slice(3))];
    if (value.startsWith('^')) return [regex(value)];
    return value.split('||').map((item) => item.trim()).filter(Boolean).map((item) => new RegExp(templateToRegex(item)));
}

/** The console line as the patterns see it: last carriage-return segment, no colours, no prefix. */
function cleanLine(form: Advanced, line: string): string {
    const parts = line.replace(/[\r\n]+$/, '').split('\r');
    // eslint-disable-next-line no-control-regex
    let cleaned = (parts[parts.length - 1] ?? '').replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');
    const prefixes = form.line_prefix.trim() === 'auto' ? AUTO_PREFIXES : form.line_prefix.trim() ? compilePattern(form.line_prefix) : [];
    for (const prefix of prefixes) {
        const stripped = cleaned.replace(prefix, '');
        if (stripped !== cleaned) { cleaned = stripped; break; }
    }
    return cleaned.trim();
}

/** Common chat shapes, used when the player name and message are not given. */
const CHAT_SHAPES: [RegExp, string][] = [
    [/^<[^>]+> .+$/, '<{player}> {message}'],
    [/^\[[^\]]+\] .+$/, '[{player}] {message}'],
    [/^[^:\s][^:]{0,40}: .+$/, '{player}: {message}'],
    [/^\S+ > .+$/, '{player} > {message}'],
];

/** Turn a sample console line into a template by replacing the player name and message. */
function suggestPattern(form: Advanced, line: string, kind: PatternKey, player: string, message: string): string | null {
    let template = cleanLine(form, line);
    if (!template) return null;
    if (!player.trim() && !message.trim()) {
        if (kind !== 'chat' && kind !== 'broadcast') return template;
        return CHAT_SHAPES.find(([shape]) => shape.test(template))?.[1] ?? null;
    }
    if (message.trim()) {
        const at = template.lastIndexOf(message.trim());
        if (at < 0) return null;
        template = `${template.slice(0, at)}{message}${template.slice(at + message.trim().length)}`;
    }
    if (player.trim()) {
        const at = template.indexOf(player.trim());
        if (at < 0) return null;
        template = `${template.slice(0, at)}{player}${template.slice(at + player.trim().length)}`;
    }
    return template;
}

type ExportedSetup = {
    format: 'pterosync-game'; version: 1; game: string; overrides: AdapterConfig | null;
    event_colors: Partial<Record<EventKey, string>> | null; disabled_features: string[];
};

function testLine(form: Advanced, line: string): string {
    try {
        const cleaned = cleanLine(form, line);
        for (const ignore of form.ignore.split('\n').map((item) => item.trim()).filter(Boolean)) {
            if (compilePattern(ignore).some((pattern) => pattern.test(cleaned))) return `Ignored line: “${cleaned}”`;
        }
        for (const key of PATTERN_KEYS) {
            if (!form.patterns[key].trim()) continue;
            for (const pattern of compilePattern(form.patterns[key])) {
                const match = pattern.exec(cleaned);
                if (match) {
                    const groups = Object.entries(match.groups ?? {}).map(([name, value]) => `${name}=“${value ?? ''}”`).join(', ');
                    return `${key}${groups ? `: ${groups}` : ''}`;
                }
            }
        }
        return `No match for “${cleaned}”`;
    } catch (error) {
        return `Invalid pattern: ${(error as Error).message}`;
    }
}

function IntegrationEditor({ base, binding, canConsole, onSaved }: { base: string; binding: Binding; canConsole: boolean; onSaved: () => Promise<void> }) {
    const adapters = binding.adapters;
    const suggested = binding.game_adapter ? null : adapters.find((item) => item.id === binding.suggested_adapter?.id) ?? null;
    const [adapterId, setAdapterId] = useState(binding.game_adapter ?? suggested?.id ?? '');
    const adapter = useMemo(() => adapters.find((item) => item.id === adapterId) ?? null, [adapters, adapterId]);
    const [chatEnabled, setChatEnabled] = useState(binding.chat_enabled);
    const [disabled, setDisabled] = useState<string[]>(binding.disabled_features ?? []);
    const toggleFeature = (feature: string) => setDisabled((current) => current.includes(feature) ? current.filter((item) => item !== feature) : [...current, feature]);
    const [channelId, setChannelId] = useState(binding.chat_channel_id ?? '');
    const [notificationId, setNotificationId] = useState(binding.notification_channel_id ?? '');
    const [colors, setColors] = useState<Record<EventKey, string>>({ ...EVENT_DEFAULTS, ...(binding.event_colors ?? {}) } as Record<EventKey, string>);
    const [form, setForm] = useState<Advanced>(() => advancedFrom(adapter?.defaults ?? {}, binding.adapter_config));
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [sample, setSample] = useState('');
    const [suggestKind, setSuggestKind] = useState<PatternKey>('chat');
    const [suggestPlayer, setSuggestPlayer] = useState('');
    const [suggestMessage, setSuggestMessage] = useState('');
    const [saving, setSaving] = useState(false);
    const [library, setLibrary] = useState<{ open: boolean; entries: LibraryEntry[] | null; error: string | null }>({ open: false, entries: null, error: null });
    const channels = binding.available_channels;
    const defaultChannel = channels.find((channel) => channel.discord_id === binding.integration_channel_id);
    const chosenChannel = channels.find((channel) => channel.discord_id === (channelId || binding.integration_channel_id));
    const defaultNotification = channels.find((channel) => channel.discord_id === binding.guild_notification_channel_id);

    const selectAdapter = (id: string) => {
        setAdapterId(id);
        if (!id) setChatEnabled(false);
        const next = adapters.find((item) => item.id === id);
        setForm(advancedFrom(next?.defaults ?? {}, null));
    };
    const setPattern = (key: PatternKey, value: string) => setForm((current) => ({ ...current, patterns: { ...current.patterns, [key]: value } }));
    const suggestion = sample ? suggestPattern(form, sample, suggestKind, suggestPlayer, suggestMessage) : null;
    const addSuggestion = () => {
        if (!suggestion) return;
        const existing = form.patterns[suggestKind].trim();
        if (existing.split('||').map((item) => item.trim()).includes(suggestion)) return;
        setPattern(suggestKind, existing && !existing.startsWith('^') && !existing.startsWith('re:') ? `${existing} || ${suggestion}` : suggestion);
        toast.success(`Added to the ${suggestKind} pattern. Save the integration to apply it.`);
    };
    const exportSetup = async () => {
        if (!adapter) return;
        const changedColors = Object.fromEntries(Object.entries(colors).filter(([key, value]) => value.toUpperCase() !== EVENT_DEFAULTS[key as EventKey]));
        const setup: ExportedSetup = {
            format: 'pterosync-game', version: 1, game: adapter.id, overrides: overridesFrom(adapter.defaults, form),
            event_colors: Object.keys(changedColors).length ? changedColors : null, disabled_features: disabled,
        };
        const json = JSON.stringify(setup, null, 2);
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
        link.download = `pterosync-${adapter.id}.json`;
        link.click();
        URL.revokeObjectURL(link.href);
        try { await navigator.clipboard.writeText(json); } catch { /* the download is enough */ }
        toast.success('Game setup exported (also copied to the clipboard).');
    };
    /** Load an exported setup (a file or a library preset) into the form; nothing is saved yet. */
    const applySetup = (setup: Partial<ExportedSetup>) => {
        if (setup.format !== 'pterosync-game' || setup.version !== 1 || typeof setup.game !== 'string') throw new Error('This is not a PteroSync game setup file.');
        const next = adapters.find((item) => item.id === setup.game);
        if (!next) throw new Error(`The agent does not provide the game “${setup.game}”.`);
        setAdapterId(next.id);
        setForm(advancedFrom(next.defaults, setup.overrides ?? null));
        setColors({ ...EVENT_DEFAULTS, ...(setup.event_colors ?? {}) } as Record<EventKey, string>);
        setDisabled(Array.isArray(setup.disabled_features) ? setup.disabled_features.filter((item): item is string => typeof item === 'string') : []);
        setShowAdvanced(true);
    };
    const importSetup = async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        try {
            applySetup(JSON.parse(await file.text()) as Partial<ExportedSetup>);
            toast.success('Game setup imported. Review it and click Save integration.');
        } catch (error) {
            toast.error((error as Error).message);
        }
    };
    const toggleLibrary = async () => {
        if (library.open) { setLibrary((current) => ({ ...current, open: false })); return; }
        setLibrary((current) => ({ ...current, open: true }));
        if (library.entries) return;
        try {
            const { data } = await http.get<{ presets: LibraryEntry[] }>(`${base}/library`);
            setLibrary({ open: true, entries: data.presets, error: null });
        } catch (error) {
            // The panel answers 404 when no library repository is configured.
            const notConfigured = (error as { response?: { status?: number } }).response?.status === 404;
            setLibrary({ open: true, entries: null, error: notConfigured
                ? 'The community preset library is not set up on this panel yet. A panel administrator can enable it in the PteroSync extension settings. Until then, use Import setup with a file.'
                : `The preset library could not be loaded: ${httpErrorToHuman(error)}` });
        }
    };
    const choosePreset = async (entry: LibraryEntry) => {
        let setup: Partial<ExportedSetup>;
        try {
            setup = (await http.get<Partial<ExportedSetup>>(`${base}/library/${entry.id}`)).data;
        } catch (error) {
            toast.error(httpErrorToHuman(error));
            return;
        }
        try {
            applySetup(setup);
            setLibrary((current) => ({ ...current, open: false }));
            toast.success(`“${entry.name}” loaded. Review it and click Save integration.`);
        } catch (error) {
            toast.error((error as Error).message);
        }
    };
    const libraryEntries = (library.entries ?? []).filter((entry) => adapters.some((item) => item.id === entry.game));
    const save = async () => {
        if (saving) return;
        const maxLength = Number(form.max_length);
        if (adapter && (!Number.isInteger(maxLength) || maxLength < 16 || maxLength > 4000)) {
            toast.error('Max length must be a whole number between 16 and 4000.');
            return;
        }
        setSaving(true);
        try {
            const changedColors = Object.fromEntries(Object.entries(colors).filter(([key, value]) => value.toUpperCase() !== EVENT_DEFAULTS[key as EventKey]));
            await http.put(`${base}/${binding.id}/integration`, {
                game_adapter: adapterId || null,
                chat_enabled: chatEnabled && Boolean(adapterId),
                chat_channel_id: channelId || null,
                notification_channel_id: notificationId || null,
                event_colors: Object.keys(changedColors).length ? changedColors : null,
                disabled_features: disabled,
                // Without the agent's catalogue the preset defaults are unknown; keep stored overrides untouched.
                adapter_config: adapter ? overridesFrom(adapter.defaults, form) : adapterId === binding.game_adapter ? binding.adapter_config : null,
            });
            toast.success('Game integration saved.');
            await onSaved();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setSaving(false);
        }
    };
    const unlink = async () => {
        if (saving || !window.confirm(`Unlink ${binding.guild_name}? Its roles and settings for this server are deleted.`)) return;
        setSaving(true);
        try {
            await http.delete(`${base}/${binding.id}`);
            toast.success(`${binding.guild_name} unlinked.`);
            await onSaved();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
            setSaving(false);
        }
    };

    return <div className="psync:grid psync:gap-4 psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
        <div className="psync:flex psync:flex-wrap psync:items-center psync:justify-between psync:gap-2">
            <h2 className="psync:text-lg psync:font-semibold">{binding.guild_name} · Game integration</h2>
            <button type="button" className="psync:text-sm psync:text-red-500 psync:underline psync:disabled:opacity-50" disabled={saving} onClick={() => void unlink()}>Unlink</button>
        </div>
        {adapters.length === 0 && <p className="psync:text-sm psync:text-muted-foreground">The Discord agent has not reported its game adapters yet. Update the agent and wait for its next heartbeat.</p>}
        <div className="psync:grid psync:gap-4 psync:md:grid-cols-2">
            <label className="psync:grid psync:gap-1 psync:text-sm">Game
                <select className={fieldClass} value={adapterId} onChange={(event) => selectAdapter(event.target.value)}>
                    <option value="">Disabled</option>
                    {adapters.map((item) => <option key={item.id} value={item.id}>{item.name}{item.status && item.status !== 'verified' ? ` (${item.status})` : ''}</option>)}
                </select>
                {suggested && adapterId === suggested.id && binding.suggested_adapter && <span className="psync:text-xs psync:text-muted-foreground">
                    Suggested from the server's {binding.suggested_adapter.source === 'egg' ? 'egg' : 'Docker image'}: {binding.suggested_adapter.value}. Click Save integration to use it.
                </span>}
            </label>
            <label className="psync:grid psync:gap-1 psync:text-sm">Chat channel
                <select className={fieldClass} value={channelId} onChange={(event) => setChannelId(event.target.value)}>
                    <option value="">{defaultChannel ? `Integration channel (#${defaultChannel.name})` : 'Integration channel (not set)'}</option>
                    {channels.filter((channel) => channel.can_send || channel.discord_id === channelId).map((channel) => <option key={channel.discord_id} value={channel.discord_id}>#{channel.name}{channel.can_webhook ? '' : ' (no webhook permission)'}</option>)}
                </select>
            </label>
            <label className="psync:grid psync:gap-1 psync:text-sm">Notification channel
                <select className={fieldClass} value={notificationId} onChange={(event) => setNotificationId(event.target.value)}>
                    <option value="">{defaultNotification ? `Discord server default (#${defaultNotification.name})` : "Discord server's default"}</option>
                    {channels.filter((channel) => channel.can_send || channel.discord_id === notificationId).map((channel) => <option key={channel.discord_id} value={channel.discord_id}>#{channel.name}{channel.can_send ? '' : ' (cannot send)'}</option>)}
                </select>
                <span className="psync:text-xs psync:text-muted-foreground">Where start, stop and crash messages for this server are posted.</span>
            </label>
        </div>
        {adapter && <>
            {adapter.notes && <p className="psync:text-sm psync:text-muted-foreground">{adapter.notes}</p>}
            {adapter.status && <p className={`psync:text-sm ${adapter.status === 'verified' ? 'psync:text-muted-foreground' : 'psync:text-yellow-500'}`}>{adapter.status === 'verified' ? '✓ ' : '⚠ '}{STATUS_TEXT[adapter.status]}</p>}
            <div className="psync:flex psync:flex-wrap psync:gap-2">{FEATURE_ORDER.filter((feature) => adapter.capabilities.includes(feature)).map((feature) => {
                const enabled = !disabled.includes(feature);
                return <button key={feature} type="button" aria-pressed={enabled} title={enabled ? 'Enabled — click to turn off' : 'Disabled — click to turn on'}
                    onClick={() => toggleFeature(feature)}
                    className={`psync:rounded psync:border psync:px-2 psync:py-1 psync:text-xs psync:transition ${enabled
                        ? 'psync:border-primary psync:bg-primary/15 psync:text-foreground'
                        : 'psync:border-border psync:text-muted-foreground psync:line-through psync:opacity-50'}`}>
                    {enabled ? '✓ ' : ''}{CAPABILITY_LABELS[feature] ?? feature}
                </button>;
            })}
            </div>
        </>}
        <label className="psync:flex psync:items-center psync:gap-2 psync:text-sm">
            <input type="checkbox" checked={chatEnabled} onChange={() => setChatEnabled(!chatEnabled)} disabled={!adapterId} />
            Relay chat and events between Discord and the server console
        </label>
        {chatEnabled && !chosenChannel && <p className="psync:text-sm psync:text-red-500">Select a chat channel or set the integration channel in Admin → PteroSync.</p>}
        {chatEnabled && chosenChannel && !chosenChannel.can_webhook && <p className="psync:text-sm psync:text-muted-foreground">Without the Manage Webhooks permission, game messages are posted by the bot instead of under the player's name.</p>}
        <div className="psync:flex psync:flex-wrap psync:gap-4">{(Object.keys(EVENT_DEFAULTS) as EventKey[]).map((key) =>
            <label key={key} className="psync:flex psync:items-center psync:gap-2 psync:text-sm psync:capitalize">
                <input type="color" value={colors[key]} onChange={(event) => setColors((current) => ({ ...current, [key]: event.target.value.toUpperCase() }))} />{key}
            </label>)}
        </div>
        {adapter && <div className="psync:grid psync:gap-3">
            <button type="button" className="psync:w-fit psync:text-sm psync:underline" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? 'Hide' : 'Show'} advanced console settings</button>
            {showAdvanced && <div className="psync:grid psync:gap-3">
                {!canConsole && <p className="psync:text-sm psync:text-yellow-500">⚠ The broadcast command settings run on the server console, so changing them requires the console permission on this server.</p>}
                <fieldset disabled={!canConsole} className="psync:grid psync:gap-3 psync:disabled:opacity-60">
                <label className="psync:grid psync:gap-1 psync:text-sm">Broadcast command (variables: {'{line} {json} {author} {message} {role} {prefix} {label}'}; empty disables Discord → game)
                    <input className={monoClass} value={form.template} onChange={(event) => setForm({ ...form, template: event.target.value })} />
                </label>
                <div className="psync:grid psync:gap-3 psync:md:grid-cols-5">
                    <label className="psync:grid psync:gap-1 psync:text-sm">Style
                        <select className={fieldClass} value={form.style} onChange={(event) => setForm({ ...form, style: event.target.value })}>{STYLES.map((style) => <option key={style}>{style}</option>)}</select>
                    </label>
                    <label className="psync:grid psync:gap-1 psync:text-sm">Escaping
                        <select className={fieldClass} value={form.escape} onChange={(event) => setForm({ ...form, escape: event.target.value })}>{ESCAPES.map((escape) => <option key={escape}>{escape}</option>)}</select>
                    </label>
                    <label className="psync:grid psync:gap-1 psync:text-sm">Max length
                        <input className={fieldClass} type="number" min={16} max={4000} value={form.max_length} onChange={(event) => setForm({ ...form, max_length: event.target.value })} />
                    </label>
                    <label className="psync:grid psync:gap-1 psync:text-sm">Label
                        <input className={fieldClass} maxLength={32} value={form.label} onChange={(event) => setForm({ ...form, label: event.target.value })} />
                    </label>
                    <label className="psync:grid psync:gap-1 psync:text-sm" title="Removed from Discord names and messages, e.g. ; which separates console commands in some games">Characters to remove
                        <input className={monoClass} maxLength={32} value={form.strip} onChange={(event) => setForm({ ...form, strip: event.target.value })} />
                    </label>
                </div>
                </fieldset>
                <p className="psync:text-xs psync:text-muted-foreground">Patterns are written like the console line: <code>{'<{player}> {message}'}</code> or <code>{'{player} joined the game'}</code>. Placeholders: <code>{'{player}'}</code>, <code>{'{message}'}</code>, <code>{'{rank}'}</code>, <code>{'{*}'}</code> (anything). Separate alternatives with <code>||</code>. Start with <code>^</code> or <code>re:</code> to use a regular expression.</p>
                <label className="psync:grid psync:gap-1 psync:text-sm"><span>Line prefix to strip (<code>auto</code> removes common timestamps)</span>
                    <input className={monoClass} value={form.line_prefix} onChange={(event) => setForm({ ...form, line_prefix: event.target.value })} />
                </label>
                {PATTERN_KEYS.map((key) => <label key={key} className="psync:grid psync:gap-1 psync:text-sm">{key} pattern
                    <input className={monoClass} value={form.patterns[key]} onChange={(event) => setPattern(key, event.target.value)} />
                </label>)}
                <label className="psync:grid psync:gap-1 psync:text-sm">Ignore lines matching (one pattern per line)
                    <textarea className={`${monoClass} psync:min-h-20 psync:py-2`} value={form.ignore} onChange={(event) => setForm({ ...form, ignore: event.target.value })} />
                </label>
                <label className="psync:grid psync:gap-1 psync:text-sm">Player avatar URL ({'{player}'} is replaced)
                    <input className={monoClass} value={form.avatar_url} onChange={(event) => setForm({ ...form, avatar_url: event.target.value })} />
                </label>
                <label className="psync:grid psync:gap-1 psync:text-sm">Test a console line
                    <input className={monoClass} value={sample} placeholder="[12:00:00] [Server thread/INFO]: <Steve> hello" onChange={(event) => setSample(event.target.value)} />
                </label>
                {sample && <>
                    <p className="psync:font-mono psync:text-xs">{testLine(form, sample)}</p>
                    <div className="psync:grid psync:gap-2 psync:rounded psync:border psync:border-border psync:p-3">
                        <p className="psync:text-sm psync:font-medium">Create a pattern from this line</p>
                        <p className="psync:text-xs psync:text-muted-foreground">Type the player name and message exactly as they appear in the line. For chat lines you can leave both empty to detect them.</p>
                        <div className="psync:grid psync:gap-2 psync:md:grid-cols-3">
                            <select className={fieldClass} value={suggestKind} onChange={(event) => setSuggestKind(event.target.value as PatternKey)}>{PATTERN_KEYS.map((key) => <option key={key} value={key}>{key}</option>)}</select>
                            <input className={fieldClass} placeholder="Player name in this line" value={suggestPlayer} onChange={(event) => setSuggestPlayer(event.target.value)} />
                            <input className={fieldClass} placeholder="Message in this line" value={suggestMessage} onChange={(event) => setSuggestMessage(event.target.value)} />
                        </div>
                        <p className="psync:font-mono psync:text-xs">{suggestion ?? 'The player name or message was not found in the line.'}</p>
                        <div><Button disabled={!suggestion} onClick={addSuggestion}>Add as {suggestKind} pattern</Button></div>
                    </div>
                </>}
            </div>}
        </div>}
        <div className="psync:flex psync:flex-wrap psync:items-center psync:gap-3">
            <Button disabled={saving} onClick={() => void save()}>Save integration</Button>
            <button type="button" className="psync:text-sm psync:underline psync:disabled:opacity-50" disabled={!adapter} onClick={() => void exportSetup()}>Export setup</button>
            <label className="psync:cursor-pointer psync:text-sm psync:underline">Import setup
                <input type="file" accept="application/json,.json" className="psync:hidden" onChange={(event) => void importSetup(event)} />
            </label>
            <button type="button" className="psync:text-sm psync:underline" aria-expanded={library.open} onClick={() => void toggleLibrary()}>Browse presets</button>
        </div>
        {library.open && <div className="psync:grid psync:gap-2 psync:rounded psync:border psync:border-border psync:p-3">
            <p className="psync:text-sm psync:font-medium">Community presets</p>
            {library.error && <p className="psync:text-sm psync:text-muted-foreground">{library.error}</p>}
            {!library.error && !library.entries && <p className="psync:text-sm psync:text-muted-foreground">Loading presets…</p>}
            {library.entries && libraryEntries.length === 0 && <p className="psync:text-sm psync:text-muted-foreground">The library has no presets for the games this agent supports.</p>}
            {libraryEntries.map((entry) => <div key={entry.id} className="psync:flex psync:flex-col psync:gap-2 psync:border-t psync:border-border psync:pt-2 psync:first:border-t-0 psync:first:pt-0 psync:sm:flex-row psync:sm:items-start psync:sm:justify-between">
                <div className="psync:grid psync:gap-1">
                    <p className="psync:text-sm">
                        <span className="psync:font-medium">{entry.name}</span>
                        <span className="psync:text-muted-foreground"> · {adapters.find((item) => item.id === entry.game)?.name ?? entry.game} · </span>
                        <span className={entry.status === 'verified' ? 'psync:text-muted-foreground' : 'psync:text-yellow-500'}>{entry.status}</span>
                        {entry.author && <span className="psync:text-muted-foreground"> · by {entry.author}</span>}
                    </p>
                    {entry.description && <p className="psync:text-xs psync:text-muted-foreground">{entry.description}</p>}
                </div>
                <div className="psync:shrink-0"><Button onClick={() => void choosePreset(entry)}>Use</Button></div>
            </div>)}
        </div>}
    </div>;
}

function RoleEditor({ base, binding, onSaved }: { base: string; binding: Binding; onSaved: () => Promise<void> }) {
    const { t } = useExtensionTranslation('messages');
    const existing = new Map(binding.permissions.map((item) => [item.discord_role_id, item]));
    const [rows, setRows] = useState(() => binding.available_roles.filter((role) => !role.managed).map((role) => {
        const current = existing.get(role.discord_id);
        return {
            role, view: Boolean(current?.can_view), power: Boolean(current?.can_power), console: Boolean(current?.can_console),
            configure: Boolean(current?.can_configure), chat: Boolean(current?.can_chat),
            chat_label: current?.chat_label ?? '', chat_color: current?.chat_color ?? '',
        };
    }));
    const [saving, setSaving] = useState(false);
    const toggle = (index: number, key: 'view' | 'power' | 'console' | 'configure' | 'chat') => setRows((current) => current.map((row, i) => i === index ? { ...row, [key]: !row[key] } : row));
    const setText = (index: number, key: 'chat_label' | 'chat_color', value: string) => setRows((current) => current.map((row, i) => i === index ? { ...row, [key]: value } : row));
    const save = async () => {
        if (saving) return;
        if (rows.some((row) => row.chat_color && !/^#[0-9A-Fa-f]{6}$/.test(row.chat_color))) {
            toast.error('Chat colours must look like #RRGGBB.');
            return;
        }
        setSaving(true);
        try {
            await http.put(`${base}/${binding.id}/roles`, {
                roles: rows.map((row) => ({
                    role_id: row.role.discord_id, view: row.view, power: row.power, console: row.console, configure: row.configure,
                    chat: row.chat, chat_label: row.chat_label.trim() || null, chat_color: row.chat_color || null,
                })),
            });
            toast.success('Discord role permissions saved.');
            await onSaved();
        } catch (error) {
            toast.error(httpErrorToHuman(error));
        } finally {
            setSaving(false);
        }
    };
    return <div className="psync:overflow-auto psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
        <h2 className="psync:mb-2 psync:text-lg psync:font-semibold">{binding.guild_name} · Roles</h2>
        <p className="psync:mb-4 psync:text-sm psync:text-muted-foreground">Chat: roles allowed to talk to the server (if none are ticked, everyone in the chat channel can). Label and colour replace the role name and colour shown in-game.</p>
        <table className="psync:w-full psync:min-w-xl psync:text-left"><thead><tr><th className="psync:py-2">Discord role</th>{['View', 'Power', 'Console', 'Configure', 'Chat'].map((x) => <th className="psync:px-2 psync:text-center" key={x}>{x}</th>)}<th className="psync:px-2">In-game label</th><th className="psync:px-2">Colour</th></tr></thead>
            <tbody>{rows.map((row, index) => <tr key={row.role.discord_id} className="psync:border-t psync:border-border"><td className="psync:py-3">{row.role.name.startsWith('@') ? row.role.name : `@${row.role.name}`}</td>{(['view', 'power', 'console', 'configure', 'chat'] as const).map((key) => <td className="psync:px-2 psync:text-center" key={key}><input aria-label={`${row.role.name}: ${key}`} type="checkbox" checked={row[key]} onChange={() => toggle(index, key)} /></td>)}
                <td className="psync:px-2"><input className={fieldClass} maxLength={32} placeholder={row.role.name} value={row.chat_label} onChange={(event) => setText(index, 'chat_label', event.target.value)} /></td>
                <td className="psync:px-2"><div className="psync:flex psync:items-center psync:gap-2">
                    {row.chat_color ? <>
                        <input aria-label={`${row.role.name}: colour`} type="color" value={row.chat_color} onChange={(event) => setText(index, 'chat_color', event.target.value.toUpperCase())} />
                        <button type="button" className="psync:text-xs psync:underline" onClick={() => setText(index, 'chat_color', '')}>Reset</button>
                    </> : <button type="button" title="The role's own Discord colour is used. Click to choose a different colour."
                        className="psync:rounded psync:border psync:border-dashed psync:border-border psync:px-2 psync:py-1 psync:text-xs psync:text-muted-foreground"
                        onClick={() => setText(index, 'chat_color', '#5865F2')}>Discord colour</button>}
                </div></td>
            </tr>)}</tbody>
        </table>{rows.length === 0 && <p className="psync:py-4 psync:text-sm psync:text-muted-foreground">No assignable Discord roles are available.</p>}
        <div className="psync:mt-4"><Button disabled={saving} onClick={() => void save()}>{t('save')}</Button></div>
    </div>;
}
