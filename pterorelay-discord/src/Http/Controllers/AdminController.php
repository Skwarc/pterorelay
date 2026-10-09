<?php

declare(strict_types=1);

namespace PteroRelay\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;
use Pterodactyl\Services\Extensions\ExtensionManager;
use PteroRelay\Models\Agent;
use Throwable;

final class AdminController
{
    /** Permissions the invite link asks for: View Channels, Send Messages, Embed Links, Read Message History, Add Reactions, Manage Webhooks. */
    public const INVITE_PERMISSIONS = 1024 + 2048 + 16384 + 65536 + 64 + 536870912;

    public function overview(): JsonResponse
    {
        $decode = static fn (mixed $value): mixed => is_string($value) ? json_decode($value, true) : $value;
        // Names for the servers diagnostics refer to, so the admin page can link to them.
        $uuids = Agent::query()->pluck('diagnostics')->flatMap(fn (mixed $value) => collect((array) data_get($decode($value), 'problems', []))->pluck('server'))
            ->filter()->unique()->values();
        $servers = DB::table('servers')->whereIn('uuid', $uuids)->get(['uuid', 'name'])
            ->mapWithKeys(fn (object $server): array => [$server->uuid => ['name' => $server->name, 'identifier' => substr($server->uuid, 0, 8)]]);

        return new JsonResponse([
            'servers' => $servers,
            'extension_version' => self::extensionVersion(),
            'invite_permissions' => self::INVITE_PERMISSIONS,
            'relayed_servers' => DB::table('ext_pterorelay_server_guild')->where('enabled', true)->where('chat_enabled', true)->count(),
            'linked_servers' => DB::table('ext_pterorelay_server_guild')->where('enabled', true)->count(),
            'agents' => Agent::query()->get(['public_id', 'name', 'version', 'last_seen_at', 'server_uuid', 'bot', 'diagnostics'])
                ->map(fn (Agent $agent): array => [
                    'public_id' => $agent->public_id, 'name' => $agent->name, 'version' => $agent->version,
                    'last_seen_at' => $agent->last_seen_at?->toAtomString(), 'server_uuid' => $agent->server_uuid,
                    'bot' => $decode($agent->bot), 'diagnostics' => $decode($agent->diagnostics),
                ]),
            'guilds' => DB::table('ext_pterorelay_guilds')->get()->map(function (object $guild): object {
                $guild->roles = DB::table('ext_pterorelay_discord_roles')->where('guild_id', $guild->id)->orderByDesc('position')->get();
                $guild->channels = DB::table('ext_pterorelay_discord_channels')->where('guild_id', $guild->id)->orderBy('position')
                    ->get(['discord_id', 'name', 'can_send', 'can_webhook']);
                return $guild;
            }),
        ]);
    }

    /** Latest release on GitHub (from the repository setting), cached for six hours. */
    public function updates(): JsonResponse
    {
        $current = self::extensionVersion();
        $repository = (string) (app(ExtensionManager::class)->settings('pterorelay-discord')->get('repository') ?? '');
        if (preg_match('/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/', $repository) !== 1) {
            return new JsonResponse(['current' => $current, 'configured' => false]);
        }
        $latest = Cache::remember("pterorelay:latest-release:{$repository}", now()->addHours(6), function () use ($repository): ?array {
            try {
                $releases = Http::timeout(5)->acceptJson()->get("https://api.github.com/repos/{$repository}/releases", ['per_page' => 10])->throw()->json();
            } catch (Throwable) {
                return null;
            }
            foreach (is_array($releases) ? $releases : [] as $release) {
                if (is_array($release) && empty($release['draft']) && isset($release['tag_name'], $release['html_url'])) {
                    return ['version' => ltrim((string) $release['tag_name'], 'v'), 'url' => (string) $release['html_url']];
                }
            }

            return null;
        });

        return new JsonResponse([
            'current' => $current,
            'configured' => true,
            'latest' => $latest,
            'update_available' => $latest !== null && version_compare(self::comparable($latest['version']), self::comparable($current), '>'),
        ]);
    }

    /** Checks a bot token with Discord before deploying; the token is not stored. */
    public function validateToken(Request $request): JsonResponse
    {
        $data = $request->validate(['token' => ['required', 'string', 'max:200', 'regex:/^[A-Za-z0-9_.-]+$/']]);
        try {
            $response = Http::timeout(8)->acceptJson()->withHeaders(['Authorization' => 'Bot '.$data['token']])
                ->get('https://discord.com/api/v10/applications/@me');
        } catch (Throwable) {
            abort(502, 'Discord could not be reached.');
        }
        abort_if($response->status() === 401, 422, 'Discord rejected this token. Copy it again from the Developer Portal (Bot → Reset Token).');
        abort_unless($response->successful(), 502, 'Discord returned an unexpected error.');
        $application = $response->json();
        $flags = (int) ($application['flags'] ?? 0);

        return new JsonResponse([
            'application_id' => (string) ($application['id'] ?? ''),
            'name' => (string) ($application['name'] ?? ''),
            'bot_name' => (string) ($application['bot']['username'] ?? ''),
            // GATEWAY_MESSAGE_CONTENT (1 << 19) or GATEWAY_MESSAGE_CONTENT_LIMITED (1 << 18).
            'message_content' => ($flags & (1 << 19)) !== 0 || ($flags & (1 << 18)) !== 0,
            'invite_url' => self::inviteUrl((string) ($application['id'] ?? '')),
        ]);
    }

    public static function inviteUrl(string $applicationId): string
    {
        return 'https://discord.com/oauth2/authorize?'.http_build_query([
            'client_id' => $applicationId, 'scope' => 'bot applications.commands', 'permissions' => self::INVITE_PERMISSIONS,
        ]);
    }

    public static function extensionVersion(): string
    {
        $manifest = json_decode((string) file_get_contents(dirname(__DIR__, 3).'/extension.json'), true);

        return (string) ($manifest['version'] ?? '0.0.0');
    }

    /** "0.3.0-beta.11" compares correctly with version_compare once the pre-release dot is dropped. */
    private static function comparable(string $version): string
    {
        return preg_replace('/-(alpha|beta|rc)\.(\d+)$/', '$1$2', $version) ?? $version;
    }

    public function updateGuild(Request $request, int $guildId): Response
    {
        $data = $request->validate([
            'integration_channel_id' => ['present', 'nullable', 'string', 'regex:/^\d{1,20}$/'],
            'notification_channel_id' => ['present', 'nullable', 'string', 'regex:/^\d{1,20}$/'],
        ]);
        abort_unless(DB::table('ext_pterorelay_guilds')->where('id', $guildId)->exists(), 404);
        foreach ($data as $channelId) {
            abort_if(
                $channelId !== null && !DB::table('ext_pterorelay_discord_channels')->where('guild_id', $guildId)->where('discord_id', $channelId)->exists(),
                422,
                'The channel does not belong to this Discord server.',
            );
        }
        DB::table('ext_pterorelay_guilds')->where('id', $guildId)->update($data + ['updated_at' => now()]);

        return new Response(status: 204);
    }

    public function createAgent(Request $request): JsonResponse
    {
        $data = $request->validate(['name' => ['required', 'string', 'max:80']]);
        $secret = bin2hex(random_bytes(32));
        $agent = Agent::query()->create([
            'public_id' => (string) Str::uuid(),
            'name' => $data['name'],
            'secret' => $secret,
        ]);

        return new JsonResponse(['agent_id' => $agent->public_id, 'secret' => $secret], 201);
    }

    public function rotateAgentSecret(string $publicId): JsonResponse
    {
        /** @var Agent|null $agent */
        $agent = Agent::query()->where('public_id', $publicId)->first();
        abort_if($agent === null, 404);

        $secret = bin2hex(random_bytes(32));
        $agent->forceFill(['secret' => $secret])->save();

        return new JsonResponse(['agent_id' => $agent->public_id, 'secret' => $secret]);
    }

    public function deleteAgent(string $publicId): Response
    {
        $deleted = Agent::query()->where('public_id', $publicId)->delete();
        abort_if($deleted === 0, 404);

        return new Response(status: 204);
    }
}
