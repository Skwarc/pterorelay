<?php

declare(strict_types=1);

namespace PteroRelay\Http\Controllers;

use Illuminate\Cache\RateLimiter;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Pterodactyl\Contracts\Servers\SendsServerCommands;
use Pterodactyl\Contracts\Servers\ReadsServerLogs;
use Pterodactyl\Contracts\Servers\SendsServerPower;
use Pterodactyl\Facades\Daemon;
use Pterodactyl\Models\Server;
use PteroRelay\Models\Agent;
use Throwable;

final readonly class AgentController
{
    public const AUDIT_RETENTION_DAYS = 90;

    public function __construct(private RateLimiter $limiter) {}

    public function heartbeat(Request $request): JsonResponse
    {
        $data = $request->validate([
            'guilds' => ['sometimes', 'array', 'max:1000'],
            'guilds.*.id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'guilds.*.name' => ['required', 'string', 'max:100'],
            'guilds.*.roles' => ['sometimes', 'array', 'max:500'],
            'guilds.*.roles.*.id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'guilds.*.roles.*.name' => ['required', 'string', 'max:100'],
            'guilds.*.roles.*.position' => ['required', 'integer', 'min:0'],
            'guilds.*.roles.*.managed' => ['required', 'boolean'],
            'guilds.*.channels' => ['sometimes', 'array', 'max:500'],
            'guilds.*.channels.*.id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'guilds.*.channels.*.name' => ['required', 'string', 'max:100'],
            'guilds.*.channels.*.position' => ['required', 'integer', 'min:0'],
            'guilds.*.channels.*.can_send' => ['required', 'boolean'],
            'guilds.*.channels.*.can_webhook' => ['required', 'boolean'],
            'adapters' => ['sometimes', 'array', 'max:50'],
            'adapters.*.id' => ['required', 'string', 'regex:/^[a-z0-9][a-z0-9-]{0,39}$/'],
            'adapters.*.name' => ['required', 'string', 'max:80'],
            'adapters.*.notes' => ['nullable', 'string', 'max:500'],
            'adapters.*.status' => ['nullable', 'string', 'in:verified,likely,unverified'],
            'adapters.*.capabilities' => ['present', 'array', 'max:20'],
            'adapters.*.capabilities.*' => ['string', 'max:20'],
            'adapters.*.defaults' => ['present', 'array'],
            'adapters.*.match' => ['sometimes', 'array:eggs,images'],
            'adapters.*.match.eggs' => ['sometimes', 'array', 'max:50'],
            'adapters.*.match.eggs.*' => ['string', 'max:40'],
            'adapters.*.match.images' => ['sometimes', 'array', 'max:50'],
            'adapters.*.match.images.*' => ['string', 'max:40'],
            'self_server_uuid' => ['sometimes', 'nullable', 'uuid'],
            'bot' => ['sometimes', 'nullable', 'array:id,name,application_id,message_content,guild_count,latency_ms'],
            'bot.id' => ['nullable', 'string', 'regex:/^\d{1,20}$/'],
            'bot.application_id' => ['nullable', 'string', 'regex:/^\d{1,20}$/'],
            'bot.name' => ['nullable', 'string', 'max:100'],
            'bot.message_content' => ['nullable', 'boolean'],
            'bot.guild_count' => ['nullable', 'integer', 'min:0'],
            'bot.latency_ms' => ['nullable', 'integer', 'min:0'],
            'diagnostics' => ['sometimes', 'nullable', 'array:problems,relayed,live,websocket'],
            'diagnostics.websocket' => ['nullable', 'integer', 'min:0'],
            'diagnostics.relayed' => ['nullable', 'integer', 'min:0'],
            'diagnostics.live' => ['nullable', 'integer', 'min:0'],
            'diagnostics.problems' => ['nullable', 'array', 'max:100'],
            'diagnostics.problems.*.kind' => ['required', 'string', 'max:40'],
            'diagnostics.problems.*.server' => ['nullable', 'string', 'max:36'],
            'diagnostics.problems.*.guild' => ['nullable', 'string', 'regex:/^\d{1,20}$/'],
            'diagnostics.problems.*.message' => ['required', 'string', 'max:300'],
            'diagnostics.problems.*.since' => ['nullable', 'string', 'max:40'],
        ]);
        $agent = $this->agent($request);
        foreach (['bot', 'diagnostics'] as $field) {
            if (array_key_exists($field, $data)) {
                $agent->forceFill([$field => $data[$field] === null ? null : json_encode($data[$field], JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE)])->save();
            }
        }
        if (array_key_exists('self_server_uuid', $data) && $data['self_server_uuid'] !== $agent->server_uuid) {
            $agent->forceFill(['server_uuid' => $data['self_server_uuid']])->save();
        }
        if (array_key_exists('adapters', $data)) {
            $catalog = json_encode(array_values($data['adapters']), JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
            abort_if(strlen($catalog) > 262_144, 422, 'Adapter catalogue is too large.');
            $agent->forceFill(['adapters' => $catalog])->save();
        }

        DB::transaction(function () use ($agent, $data): void {
            foreach ($data['guilds'] ?? [] as $guildData) {
                $existing = DB::table('ext_pterorelay_guilds')->where('discord_id', $guildData['id'])->lockForUpdate()->first();
                if ($existing !== null && $existing->agent_id !== $agent->id) {
                    continue; // claimed by another agent; skip it instead of failing the whole heartbeat
                }

                if ($existing === null) {
                    $guildId = DB::table('ext_pterorelay_guilds')->insertGetId([
                        'agent_id' => $agent->id,
                        'discord_id' => $guildData['id'],
                        'name' => $guildData['name'],
                        'locale' => 'en',
                        'created_at' => now(),
                        'updated_at' => now(),
                    ]);
                } else {
                    $guildId = $existing->id;
                    DB::table('ext_pterorelay_guilds')->where('id', $guildId)->update(['name' => $guildData['name'], 'left_at' => null, 'updated_at' => now()]);
                }

                $roleIds = [];
                foreach ($guildData['roles'] ?? [] as $role) {
                    $roleIds[] = $role['id'];
                    DB::table('ext_pterorelay_discord_roles')->upsert([[
                        'guild_id' => $guildId, 'discord_id' => $role['id'], 'name' => $role['name'],
                        'position' => $role['position'], 'managed' => $role['managed'],
                        'created_at' => now(), 'updated_at' => now(),
                    ]], ['guild_id', 'discord_id'], ['name', 'position', 'managed', 'updated_at']);
                }

                if (array_key_exists('roles', $guildData)) {
                    $stale = DB::table('ext_pterorelay_discord_roles')->where('guild_id', $guildId);
                    if ($roleIds !== []) {
                        $stale->whereNotIn('discord_id', $roleIds);
                    }
                    $stale->delete();
                }

                if (array_key_exists('channels', $guildData)) {
                    $channelIds = [];
                    foreach ($guildData['channels'] as $channel) {
                        $channelIds[] = $channel['id'];
                        DB::table('ext_pterorelay_discord_channels')->upsert([[
                            'guild_id' => $guildId, 'discord_id' => $channel['id'], 'name' => $channel['name'],
                            'position' => $channel['position'], 'can_send' => $channel['can_send'],
                            'can_webhook' => $channel['can_webhook'], 'created_at' => now(), 'updated_at' => now(),
                        ]], ['guild_id', 'discord_id'], ['name', 'position', 'can_send', 'can_webhook', 'updated_at']);
                    }
                    $stale = DB::table('ext_pterorelay_discord_channels')->where('guild_id', $guildId);
                    if ($channelIds !== []) {
                        $stale->whereNotIn('discord_id', $channelIds);
                    }
                    $stale->delete();
                }
            }

            if (array_key_exists('guilds', $data)) {
                // Guilds the bot no longer reports were left (kicked or removed); keep them for 30 days, then drop
                // them with everything linked to them (bindings, role permissions, channels, link codes cascade).
                $reported = array_map(fn (array $guild): string => (string) $guild['id'], $data['guilds']);
                DB::table('ext_pterorelay_guilds')->where('agent_id', $agent->id)->whereNull('left_at')
                    ->when($reported !== [], fn ($query) => $query->whereNotIn('discord_id', $reported))
                    ->update(['left_at' => now(), 'updated_at' => now()]);
                DB::table('ext_pterorelay_guilds')->where('agent_id', $agent->id)->whereNotNull('left_at')
                    ->where('left_at', '<', now()->subDays(30))->delete();
            }
        });

        // Audit entries hold Discord user IDs; keep 90 days. At most once an hour, whichever agent comes first.
        if (Cache::add('pterorelay:audit-prune', true, now()->addHour())) {
            DB::table('ext_pterorelay_audit_logs')->where('created_at', '<', now()->subDays(self::AUDIT_RETENTION_DAYS))->limit(10_000)->delete();
        }

        return new JsonResponse(['ok' => true, 'server_time' => now()->toAtomString()]);
    }

    public function config(Request $request): JsonResponse
    {
        $agent = $this->agent($request);
        // Guilds the bot has left are kept for a while but are unreachable, so they are not handed out.
        $guilds = DB::table('ext_pterorelay_guilds')->where('agent_id', $agent->id)->whereNull('left_at')->get([
            'id', 'discord_id', 'name', 'locale', 'notification_channel_id', 'integration_channel_id',
        ]);
        $bindings = DB::table('ext_pterorelay_server_guild as binding')
            ->join('ext_pterorelay_guilds as guild', 'guild.id', '=', 'binding.guild_id')
            ->join('servers', 'servers.id', '=', 'binding.server_id')
            ->where('guild.agent_id', $agent->id)->whereNull('guild.left_at')->where('binding.enabled', true)
            ->when($agent->server_uuid, fn ($query) => $query->where('servers.uuid', '!=', $agent->server_uuid))
            ->select([
                'binding.id', 'binding.guild_id', 'guild.discord_id as guild_discord_id',
                'binding.game_adapter', 'binding.adapter_config', 'binding.chat_enabled', 'binding.event_colors', 'binding.disabled_features',
                DB::raw('COALESCE(binding.chat_channel_id, guild.integration_channel_id) as chat_channel_id'),
                DB::raw('COALESCE(binding.notification_channel_id, guild.notification_channel_id) as notification_channel_id'),
                'servers.uuid', 'servers.name',
            ])->get()->map(function (object $binding): object {
                // Short identifier is the UUID's first segment (uuidShort); derived to avoid relying on the column name.
                $binding->identifier = substr((string) $binding->uuid, 0, 8);
                $binding->roles = DB::table('ext_pterorelay_role_permissions')->where('server_guild_id', $binding->id)
                    ->get(['discord_role_id', 'can_view', 'can_power', 'can_console', 'can_configure', 'can_chat', 'chat_label', 'chat_color']);
                $binding->adapter_config = self::decodeJson($binding->adapter_config);
                $binding->event_colors = self::decodeJson($binding->event_colors);
                $binding->disabled_features = self::decodeJson($binding->disabled_features) ?? [];
                $binding->chat_enabled = (bool) $binding->chat_enabled;

                return $binding;
            });

        return new JsonResponse(['version' => 1, 'guilds' => $guilds, 'servers' => $bindings]);
    }

    public function chat(Request $request, Server $server, SendsServerCommands $commands): Response
    {
        $data = $request->validate([
            'guild_id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'discord_user_id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'role_ids' => ['present', 'array', 'max:250'],
            'role_ids.*' => ['string', 'regex:/^\d{1,20}$/'],
            'commands' => ['required', 'array', 'min:1', 'max:3'],
            'commands.*' => ['required', 'string', 'max:4000', 'not_regex:/[\x00-\x1f\x7f]/'],
        ]);
        $agent = $this->agent($request);
        $this->refuseOwnServer($agent, $server);
        $binding = DB::table('ext_pterorelay_server_guild as binding')
            ->join('ext_pterorelay_guilds as guild', 'guild.id', '=', 'binding.guild_id')
            ->where('guild.agent_id', $agent->id)->where('binding.server_id', $server->id)
            ->where('guild.discord_id', $data['guild_id'])
            ->where('binding.enabled', true)->where('binding.chat_enabled', true)
            ->first(['binding.id', 'binding.game_adapter', 'binding.adapter_config']);
        abort_if($binding === null, 403, 'Chat relay is not enabled for this server and guild.');

        // Chat may only run the configured broadcast command: each command must start with the
        // template's fixed text (for example "say " or "tellraw @a ").
        $template = $this->broadcastTemplate($agent, $binding);
        abort_if($template === null || $template === '', 403, 'Discord to game chat is disabled for this server.');
        $prefix = strstr($template, '{', true);
        $prefix = $prefix === false ? $template : $prefix;
        foreach ($data['commands'] as $command) {
            abort_unless(str_starts_with($command, $prefix), 422, 'The chat command does not match the configured broadcast command.');
        }

        // Without any chat role configured everyone may chat; otherwise one of the roles is required.
        $chatRoles = DB::table('ext_pterorelay_role_permissions')
            ->where('server_guild_id', $binding->id)->where('can_chat', true)->pluck('discord_role_id')->all();
        abort_if($chatRoles !== [] && array_intersect($chatRoles, $data['role_ids']) === [], 403, 'Discord role may not chat with this server.');

        $this->throttle("chat:{$server->uuid}", 30);
        $this->throttle("chat:{$server->uuid}:{$data['discord_user_id']}", 10);

        $successful = false;
        try {
            foreach ($data['commands'] as $command) {
                $commands->send($server, $command);
            }
            $successful = true;
        } catch (Throwable $exception) {
            report($exception);
            abort(502, 'The server console is unavailable.');
        } finally {
            DB::table('ext_pterorelay_audit_logs')->insert([
                'server_id' => $server->id, 'guild_id' => $data['guild_id'], 'discord_user_id' => $data['discord_user_id'],
                'action' => 'chat.message', 'successful' => $successful,
                'metadata' => json_encode(['sha256' => hash('sha256', implode("\n", $data['commands'])), 'length' => mb_strlen(implode('', $data['commands']))], JSON_THROW_ON_ERROR),
                'created_at' => now(), 'updated_at' => now(),
            ]);
        }

        return new Response(status: 204);
    }

    /** One-time code a Discord member with Manage Server (checked by the agent) gives a server owner. */
    public function linkCode(Request $request, string $discordId): JsonResponse
    {
        $data = $request->validate(['discord_user_id' => ['required', 'string', 'regex:/^\d{1,20}$/']]);
        $agent = $this->agent($request);
        $guild = DB::table('ext_pterorelay_guilds')->where('agent_id', $agent->id)->where('discord_id', $discordId)->first(['id']);
        abort_if($guild === null, 404, 'This Discord server is not known to the panel yet.');
        $this->throttle("link:{$discordId}", 10);

        DB::table('ext_pterorelay_link_codes')->where('expires_at', '<=', now())->delete();
        $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        do {
            $code = '';
            for ($i = 0; $i < 10; $i++) {
                $code .= $alphabet[random_int(0, strlen($alphabet) - 1)];
            }
        } while (DB::table('ext_pterorelay_link_codes')->where('code', $code)->exists());
        $expires = now()->addMinutes(15);
        DB::table('ext_pterorelay_link_codes')->insert([
            'guild_id' => $guild->id, 'code' => $code, 'discord_user_id' => $data['discord_user_id'],
            'expires_at' => $expires, 'created_at' => now(), 'updated_at' => now(),
        ]);

        return new JsonResponse(['code' => substr($code, 0, 5).'-'.substr($code, 5), 'expires_at' => $expires->toAtomString()]);
    }

    /** The binding's broadcast command template: its override, else the preset default the agent reported. */
    private function broadcastTemplate(Agent $agent, object $binding): ?string
    {
        $overrides = self::decodeJson($binding->adapter_config) ?? [];
        if (is_array($overrides['out'] ?? null) && array_key_exists('template', $overrides['out'])) {
            return $overrides['out']['template'];
        }
        $catalog = self::decodeJson($agent->adapters) ?? [];
        foreach (is_array($catalog) ? $catalog : [] as $preset) {
            if (($preset['id'] ?? null) === $binding->game_adapter) {
                return $preset['defaults']['out']['template'] ?? null;
            }
        }

        return null;
    }

    public function logs(Request $request, Server $server, ReadsServerLogs $logs): JsonResponse
    {
        abort_unless($this->relaysChat($this->agent($request), $server), 404);

        return new JsonResponse($this->readLogs($server, $logs));
    }

    /**
     * Console lines of several servers in one request: root routes are rate limited per
     * client, so polling every server separately would not scale past a few servers.
     */
    public function logsBatch(Request $request, ReadsServerLogs $logs): JsonResponse
    {
        $data = $request->validate([
            'servers' => ['required', 'array', 'min:1', 'max:25'],
            'servers.*' => ['required', 'uuid', 'distinct'],
        ]);
        $agent = $this->agent($request);
        $servers = Server::query()->whereIn('uuid', $data['servers'])->get()->keyBy('uuid');
        $results = [];
        foreach ($data['servers'] as $uuid) {
            $server = $servers->get($uuid);
            $results[$uuid] = $server !== null && $this->relaysChat($agent, $server)
                ? $this->readLogs($server, $logs)
                : ['available' => false, 'lines' => [], 'error' => 'Chat relay is not enabled for this server.'];
        }

        return new JsonResponse(['servers' => $results]);
    }

    /**
     * Wings websocket credentials for streaming a relayed server's console. The token is issued
     * for the server owner but only carries websocket.connect: Wings sends console output to any
     * authenticated socket, while commands, power actions and install, backup and transfer
     * output each need a permission the token does not have.
     */
    public function websocket(Request $request, Server $server, \Pterodactyl\Services\Nodes\NodeJWTService $jwt): JsonResponse
    {
        $agent = $this->agent($request);
        abort_unless($this->relaysChat($agent, $server), 404);
        $this->refuseOwnServer($agent, $server);
        // The console moves to another node during a transfer; the agent polls until it is done.
        abort_if($server->transfer !== null, 409, 'The server is being transferred.');
        $owner = $server->user;

        $expiresAt = \Carbon\CarbonImmutable::now()->addMinutes(10);
        $token = $jwt->setExpiresAt($expiresAt)
            ->setUser($owner)
            ->setClaims(['server_uuid' => $server->uuid, 'permissions' => ['websocket.connect']])
            ->setScopes(\Pterodactyl\Enum\JwtScope::Websocket)
            // The core websocket controller's identifier, so the panel's token revocations apply too.
            ->handle($server->node, $owner->id.$server->uuid);
        $socket = str_replace(['https://', 'http://'], ['wss://', 'ws://'], $server->node->getConnectionAddress());

        return new JsonResponse([
            'socket' => $socket.sprintf('/api/servers/%s/ws', $server->uuid),
            'token' => $token->toString(),
            'expires_at' => $expiresAt->toAtomString(),
            // Wings only accepts websockets whose Origin is the panel URL from its configuration.
            'origin' => rtrim((string) config('app.url'), '/'),
        ]);
    }

    private function relaysChat(Agent $agent, Server $server): bool
    {
        return DB::table('ext_pterorelay_server_guild as binding')
            ->join('ext_pterorelay_guilds as guild', 'guild.id', '=', 'binding.guild_id')
            ->where('guild.agent_id', $agent->id)->where('binding.server_id', $server->id)
            ->where('binding.enabled', true)->where('binding.chat_enabled', true)->exists();
    }

    /** @return array{available: bool, lines: list<string>, error?: string} */
    private function readLogs(Server $server, ReadsServerLogs $logs): array
    {
        try {
            $lines = $logs->read($server, 100);
        } catch (Throwable $exception) {
            report($exception);

            return [
                'available' => false,
                'lines' => [],
                'error' => class_basename($exception).': '.mb_substr($exception->getMessage(), 0, 300),
            ];
        }
        // @phpstan-ignore function.impossibleType (older panel builds returned the log as one string)
        if (is_string($lines)) {
            $lines = preg_split('/\r?\n/', rtrim($lines, "\r\n")) ?: [];
        }

        return ['available' => true, 'lines' => array_map(static fn (mixed $line): string => (string) $line, array_values(collect($lines)->all()))];
    }

    public function serviceStatus(Request $request, Server $server): JsonResponse
    {
        $this->authorizeAgentServer($this->agent($request), $server);

        return $this->statusResponse($server);
    }

    public function power(Request $request, Server $server, SendsServerPower $power): Response
    {
        $data = $request->validate($this->actorRules() + ['signal' => ['required', 'in:start,stop,restart,kill']]);
        $this->authorizeDiscord($this->agent($request), $server, $data['guild_id'], $data['role_ids'], 'can_power');
        $this->throttle("power:{$server->uuid}:{$data['guild_id']}:{$data['discord_user_id']}", 6);

        return $this->audit($server, $data, 'power.'.$data['signal'], fn () => $power->send($server, $data['signal']));
    }

    public function command(Request $request, Server $server, SendsServerCommands $commands): Response
    {
        $data = $request->validate($this->actorRules() + [
            'command' => ['required', 'string', 'max:500', 'not_regex:/[\x00-\x1f\x7f]/'],
        ]);
        $this->authorizeDiscord($this->agent($request), $server, $data['guild_id'], $data['role_ids'], 'can_console');
        $this->throttle("command:{$server->uuid}:{$data['guild_id']}:{$data['discord_user_id']}", 20);

        return $this->audit($server, $data, 'console.command', fn () => $commands->send($server, $data['command']), [
            'command_sha256' => hash('sha256', $data['command']), 'command_length' => mb_strlen($data['command']),
        ]);
    }

    /** @return array<string, array<int, string>> */
    private function actorRules(): array
    {
        return [
            'guild_id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'discord_user_id' => ['required', 'string', 'regex:/^\d{1,20}$/'],
            'role_ids' => ['required', 'array', 'max:100'],
            'role_ids.*' => ['required', 'string', 'distinct', 'regex:/^\d{1,20}$/'],
        ];
    }

    /** @param list<string> $roleIds */
    private function authorizeDiscord(Agent $agent, Server $server, string $guildId, array $roleIds, string $permission): void
    {
        $this->refuseOwnServer($agent, $server);
        abort_if($roleIds === [], 403, 'No Discord role was supplied.');
        $allowed = DB::table('ext_pterorelay_server_guild as binding')
            ->join('ext_pterorelay_guilds as guild', 'guild.id', '=', 'binding.guild_id')
            ->join('ext_pterorelay_role_permissions as role', 'role.server_guild_id', '=', 'binding.id')
            ->join('ext_pterorelay_discord_roles as known_role', function ($join): void {
                $join->on('known_role.guild_id', '=', 'guild.id')
                    ->on('known_role.discord_id', '=', 'role.discord_role_id');
            })
            ->where('guild.agent_id', $agent->id)->where('binding.server_id', $server->id)
            ->where('binding.enabled', true)->where('guild.discord_id', $guildId)
            ->whereIn('role.discord_role_id', $roleIds)->where("role.{$permission}", true)->exists();
        abort_unless($allowed, 403, 'Discord role does not grant this permission.');
    }

    /** The agent must never stop, restart or send commands to the server it runs on. */
    private function refuseOwnServer(Agent $agent, Server $server): void
    {
        abort_if($agent->server_uuid !== null && $agent->server_uuid === $server->uuid, 403, 'The agent cannot control its own server.');
    }

    private function authorizeAgentServer(Agent $agent, Server $server): void
    {
        $allowed = DB::table('ext_pterorelay_server_guild as binding')
            ->join('ext_pterorelay_guilds as guild', 'guild.id', '=', 'binding.guild_id')
            ->where('guild.agent_id', $agent->id)->where('binding.server_id', $server->id)
            ->where('binding.enabled', true)->exists();
        abort_unless($allowed, 404);
    }

    private function statusResponse(Server $server): JsonResponse
    {
        $details = Daemon::server($server)->details();
        $resources = $details['utilization'] ?? [];

        return new JsonResponse([
            'current_state' => $details['state'] ?? 'unknown',
            'resources' => [
                'memory_bytes' => $resources['memory_bytes'] ?? 0,
                'cpu_absolute' => $resources['cpu_absolute'] ?? 0,
                'disk_bytes' => $resources['disk_bytes'] ?? 0,
                'uptime' => $resources['uptime'] ?? 0,
                'network' => [
                    'rx_bytes' => $resources['network']['rx_bytes'] ?? 0,
                    'tx_bytes' => $resources['network']['tx_bytes'] ?? 0,
                ],
            ],
        ]);
    }

    private static function decodeJson(mixed $value): mixed
    {
        return is_string($value) ? json_decode($value, true, flags: JSON_THROW_ON_ERROR) : $value;
    }

    private function throttle(string $key, int $attempts): void
    {
        $key = 'pterorelay:'.$key;
        abort_if($this->limiter->tooManyAttempts($key, $attempts), 429, 'Too many Discord operations.');
        $this->limiter->hit($key, 60);
    }

    private function agent(Request $request): Agent
    {
        $agent = $request->attributes->get('pterorelay_agent');
        abort_unless($agent instanceof Agent, 401);

        return $agent;
    }

    /**
     * @param array<string,mixed> $data
     * @param array<string,mixed> $metadata
     */
    private function audit(Server $server, array $data, string $action, callable $operation, array $metadata = []): Response
    {
        $successful = false;
        try {
            $operation();
            $successful = true;

            return new Response(status: 204);
        } finally {
            DB::table('ext_pterorelay_audit_logs')->insert([
                'server_id' => $server->id, 'guild_id' => $data['guild_id'],
                'discord_user_id' => $data['discord_user_id'], 'action' => $action,
                'metadata' => $metadata === [] ? null : json_encode($metadata, JSON_THROW_ON_ERROR),
                'successful' => $successful, 'created_at' => now(), 'updated_at' => now(),
            ]);
        }
    }
}
