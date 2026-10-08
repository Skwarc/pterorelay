<?php

declare(strict_types=1);

namespace Pterodactyl\Tests\Integration\PteroSync;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Str;
use Pterodactyl\Models\Extension;
use Pterodactyl\Models\Server;
use Pterodactyl\Services\Extensions\ExtensionManager;
use Pterodactyl\Services\Extensions\ExtensionProviderLoader;
use Pterodactyl\Services\Extensions\ExtensionRepository;
use PteroSync\Models\Agent;

/**
 * Installs the extension into the panel under test and builds PteroSync fixtures.
 * scripts/panel_check.py mounts the extension at /ext and runs its migrations first.
 */
final class Support
{
    public const SOURCE = '/ext';

    public static function install(): string
    {
        $directory = sys_get_temp_dir().'/pterosync-extensions-'.uniqid();
        File::ensureDirectoryExists($directory);
        symlink(self::SOURCE, $directory.'/pterosync-discord');
        config([
            'extensions.enabled' => true,
            'extensions.directory' => $directory,
            'extensions.assets_directory' => $directory.'/assets',
        ]);
        app()->forgetInstance(ExtensionRepository::class);
        app()->forgetInstance(ExtensionProviderLoader::class);
        app()->forgetInstance(ExtensionManager::class);

        $manifest = json_decode((string) file_get_contents(self::SOURCE.'/extension.json'), true, flags: JSON_THROW_ON_ERROR);
        Extension::query()->create(['identifier' => 'pterosync-discord', 'version' => $manifest['version'], 'enabled' => true]);
        app(ExtensionProviderLoader::class)->registerProviders(app(ExtensionRepository::class)->enabled());
        app('router')->getRoutes()->refreshNameLookups();

        return $directory;
    }

    public static function uninstall(string $directory): void
    {
        @unlink($directory.'/pterosync-discord');
        File::deleteDirectory($directory);
    }

    /** @param array<string, mixed> $attributes */
    public static function agent(array $attributes = []): Agent
    {
        $agent = new Agent();
        $agent->forceFill([
            'public_id' => (string) Str::uuid(),
            'name' => 'Test agent',
            'secret' => Str::random(48),
            'adapters' => json_encode([
                ['id' => 'minecraft-java', 'defaults' => ['out' => ['template' => 'tellraw @a {json}']]],
            ], JSON_THROW_ON_ERROR),
            ...$attributes,
        ])->save();

        return $agent;
    }

    public static function guild(Agent $agent, string $discordId = '111111111111111111'): int
    {
        return (int) DB::table('ext_pterosync_guilds')->insertGetId([
            'agent_id' => $agent->id, 'discord_id' => $discordId, 'name' => 'Test guild',
            'created_at' => now(), 'updated_at' => now(),
        ]);
    }

    /** @param array<string, mixed> $attributes */
    public static function bind(Server $server, int $guildId, array $attributes = []): int
    {
        return (int) DB::table('ext_pterosync_server_guild')->insertGetId([
            'server_id' => $server->id, 'guild_id' => $guildId, 'enabled' => true,
            'chat_enabled' => true, 'game_adapter' => 'minecraft-java',
            'created_at' => now(), 'updated_at' => now(), ...$attributes,
        ]);
    }

    public static function role(int $guildId, string $discordId, string $name = 'Role'): void
    {
        DB::table('ext_pterosync_discord_roles')->insert([
            'guild_id' => $guildId, 'discord_id' => $discordId, 'name' => $name,
            'created_at' => now(), 'updated_at' => now(),
        ]);
    }

    /** @param array<string, bool> $permissions */
    public static function grant(int $bindingId, string $roleId, array $permissions): void
    {
        DB::table('ext_pterosync_role_permissions')->insert([
            'server_guild_id' => $bindingId, 'discord_role_id' => $roleId,
            'created_at' => now(), 'updated_at' => now(), ...$permissions,
        ]);
    }

    /**
     * Headers signed the way agent_client.py signs them.
     *
     * @return array<string, string>
     */
    public static function signed(Agent $agent, string $method, string $path, string $body = '', ?int $timestamp = null, ?string $nonce = null): array
    {
        $timestamp ??= time();
        $nonce ??= Str::random(24);
        $payload = implode("\n", [strtoupper($method), $path, (string) $timestamp, $nonce, hash('sha256', $body)]);

        return [
            'X-PteroSync-Agent' => $agent->public_id,
            'X-PteroSync-Timestamp' => (string) $timestamp,
            'X-PteroSync-Nonce' => $nonce,
            'X-PteroSync-Signature' => hash_hmac('sha256', $payload, $agent->secret),
            'X-PteroSync-Version' => 'test',
            'Content-Type' => 'application/json',
            'Accept' => 'application/json',
        ];
    }
}
