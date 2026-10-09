<?php

declare(strict_types=1);

namespace Pterodactyl\Tests\Pest\Integration\PteroRelay\ServerSettingsTest;

use Illuminate\Foundation\Testing\DatabaseTransactions;
use Illuminate\Support\Facades\DB;
use Pterodactyl\Models\User;
use Pterodactyl\Tests\Integration\IntegrationTestCase;
use Pterodactyl\Tests\Integration\PteroRelay\Support;

uses(IntegrationTestCase::class, DatabaseTransactions::class);

beforeEach(function (): void {
    $this->extensionDirectory = Support::install();
    $this->agent = Support::agent();
    $this->guild = Support::guild($this->agent);
    Support::role($this->guild, '111111111111111111', '@everyone');
    Support::role($this->guild, '333333333333333333', 'Admin');
});

afterEach(function (): void {
    Support::uninstall($this->extensionDirectory);
});

function settingsUrl(object $server, string $path = ''): string
{
    return "/api/client/servers/{$server->uuid}/extensions/pterorelay-discord/settings{$path}";
}

function linkCode(int $guildId, string $code = 'ABCDE23456', ?\DateTimeInterface $expires = null): void
{
    DB::table('ext_pterorelay_link_codes')->insert([
        'guild_id' => $guildId, 'code' => $code, 'discord_user_id' => '222222222222222222',
        'expires_at' => $expires ?? now()->addMinutes(15), 'created_at' => now(), 'updated_at' => now(),
    ]);
}

function roles(array $overrides = []): array
{
    return ['roles' => [['role_id' => '333333333333333333', 'view' => true, 'power' => false, 'console' => false, 'configure' => false, ...$overrides]]];
}

test('the server owner links their Discord with a link code, once', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    linkCode($this->guild);

    $this->actingAs($owner)->postJson(settingsUrl($server), ['code' => 'abcde-23456'])->assertCreated();
    $this->actingAs($owner)->postJson(settingsUrl($server), ['code' => 'ABCDE-23456'])->assertUnprocessable();

    expect(DB::table('ext_pterorelay_server_guild')->where('server_id', $server->id)->where('guild_id', $this->guild)->exists())->toBeTrue();
});

test('expired link codes are refused', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    linkCode($this->guild, 'ABCDE23456', now()->subMinute());

    $this->actingAs($owner)->postJson(settingsUrl($server), ['code' => 'ABCDE23456'])->assertUnprocessable();
});

test('only panel administrators link a Discord server without a code', function (): void {
    [$owner, $server] = $this->generateTestAccount();

    $this->actingAs($owner)->postJson(settingsUrl($server), ['guild_id' => $this->guild])->assertForbidden();
    $this->actingAs(User::factory()->create(['root_admin' => true]))->postJson(settingsUrl($server), ['guild_id' => $this->guild])->assertCreated();
});

test('subusers without the configure permission cannot link or change anything', function (): void {
    [$user, $server] = $this->generateTestAccount(['websocket.connect']);
    linkCode($this->guild);
    $binding = Support::bind($server, $this->guild);

    $this->actingAs($user)->postJson(settingsUrl($server), ['code' => 'ABCDE23456'])->assertForbidden();
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/roles"), roles())->assertForbidden();
});

test('users cannot see or link servers that are not theirs', function (): void {
    [$owner] = $this->generateTestAccount();
    [, $other] = $this->generateTestAccount();
    linkCode($this->guild);

    $this->actingAs($owner)->postJson(settingsUrl($other), ['code' => 'ABCDE23456'])->assertNotFound();
    $this->actingAs($owner)->getJson(settingsUrl($other))->assertNotFound();
});

test('a binding of another server cannot be edited through this one', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    [, $other] = $this->generateTestAccount();
    $binding = Support::bind($other, $this->guild);

    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/roles"), roles())->assertNotFound();
});

test('Console and Power are never given to @everyone', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);

    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/roles"), roles(['role_id' => '111111111111111111', 'console' => true]))
        ->assertUnprocessable();
});

test('roles from another Discord server are refused', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);

    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/roles"), roles(['role_id' => '777777777777777777']))
        ->assertUnprocessable();
});

test('a subuser can only give Discord roles the console access they have themselves', function (): void {
    [$user, $server] = $this->generateTestAccount(['ext.pterorelay-discord.configure', 'websocket.connect']);
    $binding = Support::bind($server, $this->guild);

    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/roles"), roles())->assertSuccessful();
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/roles"), roles(['console' => true]))->assertForbidden();
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/roles"), roles(['power' => true]))->assertForbidden();
});

test('the owner sees only the Discord servers linked to their server', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    Support::bind($server, $this->guild);
    Support::guild(Support::agent(), '999999999999999999');

    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();

    expect($response->json('guilds'))->toBeEmpty()
        ->and(count($response->json('bindings')))->toBe(1);
});

test('the owner gets an invite link for every deployed bot', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $agent = Support::agent(['bot' => json_encode(['id' => '444444444444444444', 'name' => 'Game Bot', 'application_id' => '555555555555555555'], JSON_THROW_ON_ERROR)]);
    Support::agent(['bot' => json_encode(['id' => '666666666666666666', 'name' => 'No App'], JSON_THROW_ON_ERROR)]);

    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();

    // The agent from beforeEach has no bot and the second one no application id, so only one invite.
    expect($response->json('invites'))->toBe([[
        'name' => 'Game Bot',
        'url' => 'https://discord.com/oauth2/authorize?client_id=555555555555555555&scope=bot%20applications.commands&permissions=536955968',
    ]])
        ->and($response->getContent())->not->toContain($agent->secret)
        ->and($response->getContent())->not->toContain($agent->public_id);
});

function channel(int $guildId, string $discordId, string $name = 'channel'): void
{
    DB::table('ext_pterorelay_discord_channels')->insert([
        'guild_id' => $guildId, 'discord_id' => $discordId, 'name' => $name, 'can_send' => true,
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

function integration(array $overrides = []): array
{
    return [
        'game_adapter' => 'minecraft-java', 'chat_enabled' => true, 'chat_channel_id' => null,
        'event_colors' => null, 'disabled_features' => [], 'adapter_config' => null, ...$overrides,
    ];
}

test('the owner picks the notification channel of their server', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);
    channel($this->guild, '444444444444444444', 'status');

    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/integration"), integration(['notification_channel_id' => '444444444444444444']))
        ->assertNoContent();
    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->value('notification_channel_id'))->toBe('444444444444444444');

    // Clients that do not send the field keep the stored channel; null resets it to the Discord server's default.
    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/integration"), integration())->assertNoContent();
    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->value('notification_channel_id'))->toBe('444444444444444444');
    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/integration"), integration(['notification_channel_id' => null]))->assertNoContent();
    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->value('notification_channel_id'))->toBeNull();
});

test('a notification channel from another Discord server is refused', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);
    channel(Support::guild(Support::agent(), '999999999999999999'), '888888888888888888');

    $this->actingAs($owner)->putJson(settingsUrl($server, "/{$binding}/integration"), integration(['notification_channel_id' => '888888888888888888']))
        ->assertUnprocessable();
    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->value('notification_channel_id'))->toBeNull();
});

test('the owner sees whether the bot of a linked Discord server is online', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    Support::bind($server, $this->guild);

    $this->agent->forceFill(['last_seen_at' => now()->subSeconds(10)])->save();
    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();
    expect($response->json('bindings.0.agent_online'))->toBeTrue()
        ->and($response->json('bindings.0.agent_last_seen_at'))->toBeString()
        ->and($response->json('bindings.0.guild_left_at'))->toBeNull()
        ->and($response->getContent())->not->toContain($this->agent->public_id);

    $this->agent->forceFill(['last_seen_at' => now()->subMinutes(10)])->save();
    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();
    expect($response->json('bindings.0.agent_online'))->toBeFalse()
        ->and($response->json('bindings.0.agent_last_seen_at'))->toBeString();

    $this->agent->forceFill(['last_seen_at' => null])->save();
    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();
    expect($response->json('bindings.0.agent_online'))->toBeFalse()
        ->and($response->json('bindings.0.agent_last_seen_at'))->toBeNull();
});

test('the owner sees when the bot is no longer in a linked Discord server', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    Support::bind($server, $this->guild);
    DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->update(['left_at' => '2026-01-02 03:04:05']);

    $response = $this->actingAs($owner)->getJson(settingsUrl($server))->assertOk();

    expect($response->json('bindings.0.guild_left_at'))->toBe(\Carbon\CarbonImmutable::parse('2026-01-02 03:04:05')->toAtomString());
});

test('the owner unlinks a Discord server and its role permissions go with it', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);
    Support::grant($binding, '333333333333333333', ['can_view' => true]);

    $this->actingAs($owner)->deleteJson(settingsUrl($server, "/{$binding}"))->assertNoContent();

    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_role_permissions')->where('server_guild_id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->exists())->toBeTrue();
});

test('subusers without the configure permission cannot unlink', function (): void {
    [$user, $server] = $this->generateTestAccount(['websocket.connect']);
    $binding = Support::bind($server, $this->guild);

    $this->actingAs($user)->deleteJson(settingsUrl($server, "/{$binding}"))->assertForbidden();

    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->exists())->toBeTrue();
});

test('a binding of another server cannot be unlinked through this one', function (): void {
    [$owner, $server] = $this->generateTestAccount();
    [, $other] = $this->generateTestAccount();
    $binding = Support::bind($other, $this->guild);

    $this->actingAs($owner)->deleteJson(settingsUrl($server, "/{$binding}"))->assertNotFound();

    expect(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->exists())->toBeTrue();
});

test('deleting a server removes its links and role permissions', function (): void {
    [, $server] = $this->generateTestAccount();
    $binding = Support::bind($server, $this->guild);
    Support::grant($binding, '333333333333333333', ['can_view' => true]);
    new \Pterodactyl\Tests\Support\Fakes\FakeDaemonServer();

    $this->app->make(\Pterodactyl\Contracts\Servers\DeletesServers::class)->withForce()->delete($server);

    expect(DB::table('servers')->where('id', $server->id)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_role_permissions')->where('server_guild_id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->exists())->toBeTrue();
});

test('changing the game needs the console permission', function (): void {
    $this->agent->forceFill(['adapters' => json_encode([
        ['id' => 'minecraft-java', 'defaults' => ['out' => ['template' => 'tellraw @a {json}']]],
        ['id' => 'terraria', 'defaults' => ['out' => ['template' => 'say {line}']]],
    ])])->save();
    [$user, $server] = $this->generateTestAccount(['ext.pterorelay-discord.configure', 'websocket.connect']);
    $binding = Support::bind($server, $this->guild);

    // Saving without changing the game or the command is fine.
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/integration"), integration())->assertNoContent();
    // Another game brings another broadcast command (and other stripped characters).
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/integration"), integration(['game_adapter' => 'terraria']))->assertForbidden();

    [$console, $other] = $this->generateTestAccount(['ext.pterorelay-discord.configure', 'websocket.connect', 'control.console']);
    $otherBinding = Support::bind($other, $this->guild);
    $this->actingAs($console)->putJson(settingsUrl($other, "/{$otherBinding}/integration"), integration(['game_adapter' => 'terraria']))->assertNoContent();
});

test('turning the relay on or changing its patterns needs console read access', function (): void {
    [$user, $server] = $this->generateTestAccount(['ext.pterorelay-discord.configure']);
    $binding = Support::bind($server, $this->guild, ['chat_enabled' => false]);

    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/integration"), integration())->assertForbidden();
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/integration"), integration(['chat_enabled' => false]))->assertNoContent();
    $this->actingAs($user)->putJson(settingsUrl($server, "/{$binding}/integration"), integration([
        'chat_enabled' => false, 'adapter_config' => ['in' => ['patterns' => ['broadcast' => '{message}']]],
    ]))->assertForbidden();

    [$reader, $other] = $this->generateTestAccount(['ext.pterorelay-discord.configure', 'websocket.connect']);
    $otherBinding = Support::bind($other, $this->guild, ['chat_enabled' => false]);
    $this->actingAs($reader)->putJson(settingsUrl($other, "/{$otherBinding}/integration"), integration())->assertNoContent();
});
