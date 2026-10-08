<?php

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Route;
use Pterodactyl\Models\Server;

$decode = static fn (mixed $value): mixed => is_string($value) ? json_decode($value, true) : $value;
// Only panel administrators may link a server to any Discord server directly; everyone else
// needs a link code that a Discord member with Manage Server created with /link.
$isAdmin = static fn (Request $request): bool => (bool) ($request->user()->root_admin ?? false);
$canPower = static fn (Request $request, Server $server): bool => $request->user()->can('control.start', $server)
    && $request->user()->can('control.stop', $server) && $request->user()->can('control.restart', $server);

// Same rules as adapters/registry.py suggest(): keywords match whole words of the egg name first,
// then of the Docker image ("rust" matches "games:rust" but not "trust"), in catalogue order.
$words = static function (mixed $text): string {
    preg_match_all('/[a-z0-9]+/', strtolower(is_string($text) ? $text : ''), $matches);

    return ' '.implode(' ', $matches[0]).' ';
};
$suggestAdapter = static function (array $catalog, Server $server) use ($words): ?array {
    $sources = ['eggs' => ['egg', $server->egg?->name], 'images' => ['image', $server->image]];
    foreach ($sources as $field => [$source, $value]) {
        $text = $words($value);
        foreach ($catalog as $adapter) {
            if (!is_array($adapter) || !is_string($adapter['id'] ?? null) || !is_array($adapter['match'] ?? null)) {
                continue;
            }
            foreach ((array) ($adapter['match'][$field] ?? []) as $keyword) {
                $keyword = $words($keyword);
                if ($keyword !== '  ' && str_contains($text, $keyword)) {
                    return ['id' => $adapter['id'], 'source' => $source, 'value' => (string) $value];
                }
            }
        }
    }

    return null;
};

// The community preset library: presets/index.json and its files in a public GitHub repository,
// served through jsDelivr. The repository is an extension setting; empty means not configured.
$libraryBase = static function (): string {
    $repository = trim((string) app(\Pterodactyl\Services\Extensions\ExtensionManager::class)->settings('pterosync-discord')->get('repository'));
    abort_if($repository === '', 404, 'The preset library is not configured. An administrator can set its GitHub repository in the PteroSync extension settings.');
    abort_unless(preg_match('/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/', $repository) === 1, 404, 'The preset library repository setting must look like owner/repository.');

    return "https://cdn.jsdelivr.net/gh/{$repository}@main/presets/";
};
$fetchLibrary = static function (string $url): array {
    return Cache::remember('pterosync:library:'.sha1($url), now()->addHour(), static function () use ($url): array {
        try {
            $response = Http::timeout(5)->acceptJson()->get($url);
        } catch (\Throwable) {
            abort(502, 'The preset library could not be reached. Try again later.');
        }
        $data = $response->successful() && strlen($response->body()) <= 262_144 ? $response->json() : null;
        abort_unless(is_array($data), 502, 'The preset library returned an invalid response.');

        return $data;
    });
};
$libraryIndex = static function () use ($libraryBase, $fetchLibrary): array {
    $base = $libraryBase();
    $index = $fetchLibrary($base.'index.json');
    $presets = [];
    foreach ((array) ($index['presets'] ?? []) as $entry) {
        if (!is_array($entry) || !is_string($entry['id'] ?? null) || preg_match('/^[a-z0-9-]{1,64}$/', $entry['id']) !== 1) {
            continue;
        }
        $presets[$entry['id']] = [
            'id' => $entry['id'],
            // Only a plain file name next to index.json, never a path or another host.
            'file' => is_string($entry['file'] ?? null) && preg_match('/^[a-z0-9-]{1,64}\.json$/', $entry['file']) === 1 ? $entry['file'] : "{$entry['id']}.json",
            'name' => mb_substr((string) ($entry['name'] ?? $entry['id']), 0, 100),
            'game' => mb_substr((string) ($entry['game'] ?? ''), 0, 40),
            'description' => mb_substr((string) ($entry['description'] ?? ''), 0, 1000),
            'author' => mb_substr((string) ($entry['author'] ?? ''), 0, 100),
            'status' => in_array($entry['status'] ?? null, ['verified', 'likely', 'unverified'], true) ? $entry['status'] : 'unverified',
        ];
    }

    return [$base, $presets];
};

// Invite links for every deployed bot, so customers can add one to their own Discord before /link.
// Only the bot name and application id leave the panel, never anything else about the agent.
$invites = static function () use ($decode): array {
    $invites = [];
    foreach (DB::table('ext_pterosync_agents')->orderBy('id')->pluck('bot') as $bot) {
        $bot = $decode($bot);
        $applicationId = is_array($bot) ? (string) ($bot['application_id'] ?? '') : '';
        if (preg_match('/^\d{1,20}$/', $applicationId) !== 1) {
            continue;
        }
        $invites[] = [
            'name' => mb_substr((string) ($bot['name'] ?? '') ?: 'PteroSync bot', 0, 100),
            'url' => "https://discord.com/oauth2/authorize?client_id={$applicationId}&scope=bot%20applications.commands&permissions="
                .\PteroSync\Http\Controllers\AdminController::INVITE_PERMISSIONS,
        ];
    }

    return $invites;
};

Route::get('/settings', function (Request $request, Server $server) use ($decode, $isAdmin, $suggestAdapter, $invites) {
    abort_unless($request->user()->can('ext.pterosync-discord.view', $server), 403);
    $bindings = DB::table('ext_pterosync_server_guild as binding')
        ->join('ext_pterosync_guilds as guild', 'guild.id', '=', 'binding.guild_id')
        ->join('ext_pterosync_agents as agent', 'agent.id', '=', 'guild.agent_id')
        ->where('binding.server_id', $server->id)
        ->select('binding.*', 'guild.discord_id', 'guild.name as guild_name', 'guild.integration_channel_id',
            'guild.notification_channel_id as guild_notification_channel_id', 'guild.left_at as guild_left_at',
            'agent.adapters', 'agent.last_seen_at as agent_last_seen_at')->get();
    $iso = static fn (mixed $value): ?string => $value === null ? null : \Carbon\CarbonImmutable::parse($value)->toAtomString();
    foreach ($bindings as $binding) {
        // The agent heartbeats every 30 s; three missed heartbeats count as offline.
        $lastSeen = $binding->agent_last_seen_at === null ? null : \Carbon\CarbonImmutable::parse($binding->agent_last_seen_at);
        $binding->agent_online = $lastSeen !== null && $lastSeen->greaterThanOrEqualTo(now()->subSeconds(90));
        $binding->agent_last_seen_at = $iso($binding->agent_last_seen_at);
        $binding->guild_left_at = $iso($binding->guild_left_at);
        $binding->permissions = DB::table('ext_pterosync_role_permissions')->where('server_guild_id', $binding->id)->get();
        $binding->available_roles = DB::table('ext_pterosync_discord_roles')->where('guild_id', $binding->guild_id)->orderByDesc('position')->get();
        $binding->available_channels = DB::table('ext_pterosync_discord_channels')->where('guild_id', $binding->guild_id)->orderBy('position')
            ->get(['discord_id', 'name', 'can_send', 'can_webhook']);
        $binding->adapters = $decode($binding->adapters) ?? [];
        $binding->adapter_config = $decode($binding->adapter_config);
        $binding->event_colors = $decode($binding->event_colors);
        $binding->disabled_features = $decode($binding->disabled_features) ?? [];
        $binding->chat_enabled = (bool) $binding->chat_enabled;
        // Only a hint for the Game dropdown; nothing is stored until the user saves the integration.
        $binding->suggested_adapter = $binding->game_adapter === null && is_array($binding->adapters)
            ? $suggestAdapter($binding->adapters, $server) : null;
    }
    return response()->json([
        'bindings' => $bindings,
        'guilds' => $isAdmin($request) ? DB::table('ext_pterosync_guilds')->get(['id', 'name']) : [],
        'can_link_any' => $isAdmin($request),
        'can_console' => $request->user()->can('control.console', $server),
        'invites' => $invites(),
    ]);
});

Route::get('/settings/library', function (Request $request, Server $server) use ($libraryIndex) {
    abort_unless($request->user()->can('ext.pterosync-discord.view', $server), 403);
    [, $presets] = $libraryIndex();

    return response()->json(['presets' => array_values(array_map(
        static fn (array $entry): array => array_diff_key($entry, ['file' => true]),
        $presets,
    ))]);
});

Route::get('/settings/library/{presetId}', function (Request $request, Server $server, string $presetId) use ($libraryIndex, $fetchLibrary) {
    abort_unless($request->user()->can('ext.pterosync-discord.view', $server), 403);
    // Only presets listed in the index can be fetched, so the id never builds an arbitrary URL.
    [$base, $presets] = $libraryIndex();
    abort_unless(isset($presets[$presetId]), 404, 'This preset is not in the library.');

    return response()->json($fetchLibrary($base.$presets[$presetId]['file']));
})->where('presetId', '[a-z0-9-]{1,64}');

Route::put('/settings/{bindingId}/roles', function (Request $request, Server $server, int $bindingId) use ($canPower) {
    abort_unless($request->user()->can('ext.pterosync-discord.configure', $server), 403);
    $binding = DB::table('ext_pterosync_server_guild')->where('id', $bindingId)->where('server_id', $server->id)->first();
    abort_if($binding === null, 404);
    $data = $request->validate([
        'roles' => ['required', 'array', 'max:100'],
        'roles.*.role_id' => ['required', 'string', 'max:20'],
        'roles.*.view' => ['required', 'boolean'],
        'roles.*.power' => ['required', 'boolean'],
        'roles.*.console' => ['required', 'boolean'],
        'roles.*.configure' => ['required', 'boolean'],
        'roles.*.chat' => ['sometimes', 'boolean'],
        'roles.*.chat_label' => ['sometimes', 'nullable', 'string', 'max:32'],
        'roles.*.chat_color' => ['sometimes', 'nullable', 'string', 'regex:/^#[0-9A-Fa-f]{6}$/'],
    ]);
    $roleIds = collect((array) $data['roles'])->pluck('role_id');
    abort_if($roleIds->duplicates()->isNotEmpty(), 422, 'Each Discord role may only be configured once.');
    $knownRoleCount = DB::table('ext_pterosync_discord_roles')
        ->where('guild_id', $binding->guild_id)
        ->whereIn('discord_id', $roleIds)
        ->count();
    abort_unless($knownRoleCount === $roleIds->count(), 422, 'One or more Discord roles do not belong to this guild.');

    // Discord roles can only receive powers the user has on this server in Pterodactyl, and never
    // @everyone (its role id equals the guild id).
    $everyone = DB::table('ext_pterosync_guilds')->where('id', $binding->guild_id)->value('discord_id');
    $current = DB::table('ext_pterosync_role_permissions')->where('server_guild_id', $bindingId)->get()->keyBy('discord_role_id');
    foreach ($data['roles'] as $role) {
        $previous = $current->get($role['role_id']);
        abort_if(($role['console'] || $role['power']) && $role['role_id'] === $everyone, 422, 'Console and Power cannot be given to @everyone.');
        abort_if(
            (bool) $role['console'] !== (bool) ($previous->can_console ?? false) && !$request->user()->can('control.console', $server),
            403,
            'Changing Console for Discord roles requires the console permission on this server.',
        );
        abort_if(
            (bool) $role['power'] !== (bool) ($previous->can_power ?? false) && !$canPower($request, $server),
            403,
            'Changing Power for Discord roles requires the start, stop and restart permissions on this server.',
        );
    }

    DB::transaction(function () use ($bindingId, $data): void {
        DB::table('ext_pterosync_role_permissions')->where('server_guild_id', $bindingId)->delete();
        foreach ($data['roles'] as $role) {
            $label = trim((string) ($role['chat_label'] ?? ''));
            DB::table('ext_pterosync_role_permissions')->insert([
                'server_guild_id' => $bindingId,
                'discord_role_id' => $role['role_id'],
                'can_view' => $role['view'], 'can_power' => $role['power'],
                'can_console' => $role['console'], 'can_configure' => $role['configure'],
                'can_chat' => $role['chat'] ?? false,
                'chat_label' => $label === '' ? null : $label,
                'chat_color' => isset($role['chat_color']) ? strtoupper($role['chat_color']) : null,
                'created_at' => now(), 'updated_at' => now(),
            ]);
        }
    });
    return response()->noContent();
});

Route::put('/settings/{bindingId}/integration', function (Request $request, Server $server, int $bindingId) use ($decode) {
    abort_unless($request->user()->can('ext.pterosync-discord.configure', $server), 403);
    $binding = DB::table('ext_pterosync_server_guild as binding')
        ->join('ext_pterosync_guilds as guild', 'guild.id', '=', 'binding.guild_id')
        ->join('ext_pterosync_agents as agent', 'agent.id', '=', 'guild.agent_id')
        ->where('binding.id', $bindingId)->where('binding.server_id', $server->id)
        ->first(['binding.id', 'binding.guild_id', 'binding.adapter_config', 'binding.game_adapter', 'binding.chat_enabled', 'agent.adapters']);
    abort_if($binding === null, 404);

    $pattern = ['nullable', 'string', 'max:500'];
    $data = $request->validate([
        'game_adapter' => ['present', 'nullable', 'string', 'regex:/^[a-z0-9][a-z0-9-]{0,39}$/'],
        'chat_enabled' => ['required', 'boolean'],
        'chat_channel_id' => ['present', 'nullable', 'string', 'regex:/^\d{1,20}$/'],
        'notification_channel_id' => ['sometimes', 'nullable', 'string', 'regex:/^\d{1,20}$/'],
        'event_colors' => ['present', 'nullable', 'array:join,leave,death,advancement,broadcast,server'],
        'event_colors.*' => ['nullable', 'string', 'regex:/^#[0-9A-Fa-f]{6}$/'],
        'disabled_features' => ['present', 'nullable', 'array', 'max:20'],
        'disabled_features.*' => ['string', 'distinct', 'in:chat_out,colors_out,chat_in,join_leave,death,advancements,server_messages,server_status'],
        'adapter_config' => ['present', 'nullable', 'array:out,in,avatar_url'],
        'adapter_config.out' => ['sometimes', 'array:template,style,escape,max_length,label,strip'],
        'adapter_config.out.strip' => ['sometimes', 'nullable', 'string', 'max:32'],
        'adapter_config.out.template' => ['sometimes', 'nullable', 'string', 'max:1000'],
        'adapter_config.out.style' => ['sometimes', 'string', 'in:plain,minecraft_json,minecraft_legacy,terraria,unity_rich'],
        'adapter_config.out.escape' => ['sometimes', 'string', 'in:none,json_string,double_quotes'],
        'adapter_config.out.max_length' => ['sometimes', 'integer', 'min:16', 'max:4000'],
        'adapter_config.out.label' => ['sometimes', 'nullable', 'string', 'max:32'],
        'adapter_config.in' => ['sometimes', 'array:strip_ansi,line_prefix,patterns,ignore'],
        'adapter_config.in.strip_ansi' => ['sometimes', 'boolean'],
        'adapter_config.in.line_prefix' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns' => ['sometimes', 'array:chat,broadcast,join,leave,advancement,death,server_ready,server_stop'],
        'adapter_config.in.patterns.chat' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.join' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.leave' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.death' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.server_ready' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.broadcast' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.advancement' => ['sometimes', ...$pattern],
        'adapter_config.in.patterns.server_stop' => ['sometimes', ...$pattern],
        'adapter_config.in.ignore' => ['sometimes', 'array', 'max:20'],
        'adapter_config.in.ignore.*' => ['string', 'max:500'],
        'adapter_config.avatar_url' => ['sometimes', 'nullable', 'string', 'max:500', 'regex:/^https:\/\//'],
    ]);

    $catalog = collect((array) ($decode($binding->adapters) ?? []));
    abort_if(
        $data['game_adapter'] !== null && $catalog->isNotEmpty() && !$catalog->contains('id', $data['game_adapter']),
        422,
        'Unknown game adapter.',
    );
    abort_if(
        $data['chat_channel_id'] !== null && !DB::table('ext_pterosync_discord_channels')
            ->where('guild_id', $binding->guild_id)->where('discord_id', $data['chat_channel_id'])->exists(),
        422,
        'The chat channel does not belong to this Discord server.',
    );
    abort_if(
        ($data['notification_channel_id'] ?? null) !== null && !DB::table('ext_pterosync_discord_channels')
            ->where('guild_id', $binding->guild_id)->where('discord_id', $data['notification_channel_id'])->exists(),
        422,
        'The notification channel does not belong to this Discord server.',
    );

    // The broadcast command runs on the server console for every Discord message. The game decides
    // the default command (and which characters are stripped from chat), so changing either needs
    // the console permission in Pterodactyl.
    $config = $data['adapter_config'];
    $stored = $decode($binding->adapter_config) ?? [];
    abort_if(
        (($config['out'] ?? null) != ($stored['out'] ?? null) || $data['game_adapter'] !== $binding->game_adapter)
            && !$request->user()->can('control.console', $server),
        403,
        'Changing the game or the broadcast command requires the console permission on this server.',
    );
    // Relaying reads the server console, and the patterns decide which lines reach Discord: turning
    // the relay on or changing what it matches needs the permission to read the console.
    abort_if(
        ((bool) $data['chat_enabled'] && !(bool) $binding->chat_enabled || ($config['in'] ?? null) != ($stored['in'] ?? null))
            && !$request->user()->can('websocket.connect', $server),
        403,
        'Turning on the relay or changing its patterns requires the permission to read the console on this server.',
    );

    // Patterns are Python regular expressions; reject the ones PCRE cannot compile either.
    $patterns = array_filter([
        $config['in']['line_prefix'] ?? null,
        ...array_values($config['in']['patterns'] ?? []),
        ...($config['in']['ignore'] ?? []),
    ], static fn (mixed $value): bool => is_string($value) && (str_starts_with($value, '^') || str_starts_with($value, 're:')));
    foreach (array_map(static fn (string $value): string => str_starts_with($value, 're:') ? substr($value, 3) : $value, $patterns) as $regex) {
        // \x01 never occurs in a real pattern, so it is a safe delimiter.
        abort_if(@preg_match("\x01{$regex}\x01u", '') === false, 422, "Invalid pattern: {$regex}");
    }

    DB::table('ext_pterosync_server_guild')->where('id', $bindingId)->update([
        'game_adapter' => $data['game_adapter'],
        'chat_enabled' => $data['chat_enabled'],
        'chat_channel_id' => $data['chat_channel_id'],
        'event_colors' => $data['event_colors'] === null ? null : json_encode($data['event_colors'], JSON_THROW_ON_ERROR),
        'disabled_features' => empty($data['disabled_features']) ? null : json_encode(array_values($data['disabled_features']), JSON_THROW_ON_ERROR),
        'adapter_config' => $config === null || $config === [] ? null : json_encode($config, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE),
        'updated_at' => now(),
        // Older clients do not send the field; leave the stored channel alone then.
        ...(array_key_exists('notification_channel_id', $data) ? ['notification_channel_id' => $data['notification_channel_id']] : []),
    ]);

    return response()->noContent();
});

Route::post('/settings', function (Request $request, Server $server) use ($isAdmin) {
    abort_unless($request->user()->can('ext.pterosync-discord.configure', $server), 403);
    $data = $request->validate([
        'code' => ['required_without:guild_id', 'nullable', 'string', 'max:32'],
        'guild_id' => ['required_without:code', 'nullable', 'integer', 'exists:ext_pterosync_guilds,id'],
    ]);
    if (!empty($data['code'])) {
        $code = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $data['code']) ?? '');
        $link = DB::table('ext_pterosync_link_codes')->where('code', $code)->where('expires_at', '>', now())->first();
        abort_if($link === null, 422, 'The link code is invalid or has expired. Create a new one with /link in Discord.');
        DB::table('ext_pterosync_link_codes')->where('id', $link->id)->delete();
        $guildId = (int) $link->guild_id;
    } else {
        abort_unless($isAdmin($request), 403, 'Use a link code from /link in Discord to link this server.');
        $guildId = (int) $data['guild_id'];
    }
    $data['guild_id'] = $guildId;
    $existing = DB::table('ext_pterosync_server_guild')
        ->where('server_id', $server->id)->where('guild_id', $data['guild_id'])->first();
    if ($existing === null) {
        $bindingId = DB::table('ext_pterosync_server_guild')->insertGetId([
            'server_id' => $server->id, 'guild_id' => $data['guild_id'], 'enabled' => true,
            'created_at' => now(), 'updated_at' => now(),
        ]);

        return response()->json(['id' => $bindingId, 'created' => true], 201);
    }

    DB::table('ext_pterosync_server_guild')->where('id', $existing->id)->update(['enabled' => true, 'updated_at' => now()]);

    return response()->json(['id' => $existing->id, 'created' => false]);
});

// Unlinks a Discord server from this server; its role permissions go with it (FK cascade).
Route::delete('/settings/{bindingId}', function (Request $request, Server $server, int $bindingId) {
    abort_unless($request->user()->can('ext.pterosync-discord.configure', $server), 403);
    $deleted = DB::table('ext_pterosync_server_guild')->where('id', $bindingId)->where('server_id', $server->id)->delete();
    abort_if($deleted === 0, 404);

    return response()->noContent();
})->where('bindingId', '[0-9]+');
