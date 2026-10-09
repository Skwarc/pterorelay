<?php

declare(strict_types=1);

namespace Pterodactyl\Tests\Pest\Integration\PteroRelay\AgentApiTest;

use Illuminate\Foundation\Testing\DatabaseTransactions;
use Illuminate\Support\Facades\DB;
use Pterodactyl\Contracts\Servers\SendsServerCommands;
use Pterodactyl\Models\Server;
use Pterodactyl\Tests\Integration\IntegrationTestCase;
use Pterodactyl\Tests\Integration\PteroRelay\Support;
use PteroRelay\Models\Agent;

uses(IntegrationTestCase::class, DatabaseTransactions::class);

beforeEach(function (): void {
    $this->extensionDirectory = Support::install();
    $this->agent = Support::agent();
    $this->guild = Support::guild($this->agent);
    [, $this->server] = $this->generateTestAccount();
    $this->sent = [];
    $sent = &$this->sent;
    $this->app->instance(SendsServerCommands::class, new class($sent) implements SendsServerCommands {
        public function __construct(private array &$sent) {}

        public function send(Server $server, string $command): void
        {
            $this->sent[] = $command;
        }
    });
});

afterEach(function (): void {
    Support::uninstall($this->extensionDirectory);
});

function agentCall(object $test, Agent $agent, string $method, string $path, array $body = [], array $headers = []): \Illuminate\Testing\TestResponse
{
    $content = $method === 'GET' ? '' : json_encode($body, JSON_THROW_ON_ERROR);

    $headers = [...Support::signed($agent, $method, $path, $content), ...$headers];

    // json() encodes the body exactly like $content, which the signature covers.
    return $method === 'GET' ? $test->get($path, $headers) : $test->json($method, $path, $body, $headers);
}

function chatBody(array $overrides = []): array
{
    return [
        'guild_id' => '111111111111111111', 'discord_user_id' => '222222222222222222',
        'role_ids' => ['333333333333333333'], 'commands' => ['tellraw @a {"text":"hi"}'], ...$overrides,
    ];
}

test('the bundle version is public and matches the manifest', function (): void {
    $manifest = json_decode((string) file_get_contents(Support::SOURCE.'/extension.json'), true);

    $this->get('/pterorelay-agent/bundle/version')->assertOk()->assertContent($manifest['version']);
});

test('a correctly signed request is accepted and records the agent version', function (): void {
    agentCall($this, $this->agent, 'GET', '/pterorelay-agent/config')->assertOk()->assertJsonPath('version', 1);

    expect($this->agent->fresh()->version)->toBe('test')
        ->and($this->agent->fresh()->last_seen_at)->not->toBeNull();
});

test('requests without a valid signature are refused', function (array $headers): void {
    agentCall($this, $this->agent, 'GET', '/pterorelay-agent/config', [], $headers)->assertUnauthorized();
})->with([
    'wrong signature' => [['X-PteroRelay-Signature' => str_repeat('a', 64)]],
    'unknown agent' => [['X-PteroRelay-Agent' => '00000000-0000-0000-0000-000000000000']],
    'malformed nonce' => [['X-PteroRelay-Nonce' => 'short']],
    'missing headers' => [['X-PteroRelay-Timestamp' => '']],
]);

test('a request signed for another path is refused', function (): void {
    $headers = Support::signed($this->agent, 'GET', '/pterorelay-agent/heartbeat');

    $this->get('/pterorelay-agent/config', $headers)->assertUnauthorized();
});

test('an old timestamp is refused', function (): void {
    $headers = Support::signed($this->agent, 'GET', '/pterorelay-agent/config', '', time() - 120);

    $this->get('/pterorelay-agent/config', $headers)->assertUnauthorized();
});

test('a replayed nonce is refused', function (): void {
    $headers = Support::signed($this->agent, 'GET', '/pterorelay-agent/config', '', null, 'replayed-nonce-0000000000');

    $this->get('/pterorelay-agent/config', $headers)->assertOk();
    $this->get('/pterorelay-agent/config', $headers)->assertStatus(409);
});

test('the heartbeat syncs guilds, roles and channels and skips guilds of other agents', function (): void {
    $other = Support::agent();
    Support::guild($other, '999999999999999999');

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => [
        ['id' => '111111111111111111', 'name' => 'Renamed', 'roles' => [['id' => '333333333333333333', 'name' => 'Admin', 'position' => 1, 'managed' => false]],
            'channels' => [['id' => '444444444444444444', 'name' => 'chat', 'position' => 0, 'can_send' => true, 'can_webhook' => true]]],
        ['id' => '999999999999999999', 'name' => 'Stolen'],
    ]])->assertSuccessful();

    expect(DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->value('name'))->toBe('Renamed')
        ->and(DB::table('ext_pterorelay_discord_roles')->where('guild_id', $this->guild)->count())->toBe(1)
        ->and(DB::table('ext_pterorelay_discord_channels')->where('guild_id', $this->guild)->count())->toBe(1)
        ->and(DB::table('ext_pterorelay_guilds')->where('discord_id', '999999999999999999')->value('agent_id'))->toBe($other->id);
});

test('config lists linked servers but never the agent\'s own server', function (): void {
    [, $own] = $this->generateTestAccount();
    Support::bind($this->server, $this->guild);
    Support::bind($own, $this->guild);
    $this->agent->forceFill(['server_uuid' => $own->uuid])->save();

    $servers = agentCall($this, $this->agent, 'GET', '/pterorelay-agent/config')->assertOk()->json('servers');

    expect(array_column($servers, 'uuid'))->toBe([$this->server->uuid]);
});

test('config resolves each server\'s notification channel over the Discord server\'s', function (): void {
    [, $other] = $this->generateTestAccount();
    DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->update(['notification_channel_id' => '444444444444444444']);
    Support::bind($this->server, $this->guild, ['notification_channel_id' => '555555555555555555']);
    Support::bind($other, $this->guild);

    $servers = collect(agentCall($this, $this->agent, 'GET', '/pterorelay-agent/config')->assertOk()->json('servers'))->keyBy('uuid');

    expect($servers[$this->server->uuid]['notification_channel_id'])->toBe('555555555555555555')
        ->and($servers[$other->uuid]['notification_channel_id'])->toBe('444444444444444444');
});

test('chat sends the broadcast command and audits it', function (): void {
    Support::bind($this->server, $this->guild);

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody())->assertNoContent();

    expect($this->sent)->toBe(['tellraw @a {"text":"hi"}'])
        ->and(DB::table('ext_pterorelay_audit_logs')->where('action', 'chat.message')->where('successful', true)->count())->toBe(1);
});

test('chat refuses commands that are not the broadcast command', function (string $command, int $status): void {
    Support::bind($this->server, $this->guild);

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['commands' => [$command]]))->assertStatus($status);

    expect($this->sent)->toBe([]);
})->with([
    'other command' => ['op Attacker', 422],
    'prefix in the middle' => ['stop; tellraw @a {}', 422],
    'newline injection' => ["tellraw @a {}\nop Attacker", 422],
]);

test('chat uses the binding\'s own broadcast command', function (): void {
    Support::bind($this->server, $this->guild, ['adapter_config' => json_encode(['out' => ['template' => 'say {line}']])]);

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['commands' => ['say hi']]))->assertNoContent();
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['commands' => ['tellraw @a {}']]))->assertStatus(422);
});

test('chat is refused when chat is off, the server is unlinked or belongs to another agent', function (string $case): void {
    $agent = $this->agent;
    match ($case) {
        'chat off' => Support::bind($this->server, $this->guild, ['chat_enabled' => false]),
        'unlinked' => null,
        'other agent' => [Support::bind($this->server, $this->guild), $agent = Support::agent()],
    };

    agentCall($this, $agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody())->assertForbidden();
    expect($this->sent)->toBe([]);
})->with(['chat off', 'unlinked', 'other agent']);

test('chat needs a chat role once one is configured', function (): void {
    $binding = Support::bind($this->server, $this->guild);
    Support::role($this->guild, '555555555555555555');
    Support::grant($binding, '555555555555555555', ['can_chat' => true]);

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody())->assertForbidden();
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['role_ids' => ['555555555555555555']]))->assertNoContent();
});

test('chat for @everyone lets members without other roles chat', function (): void {
    $binding = Support::bind($this->server, $this->guild);
    // @everyone's role ID is the guild ID; the agent always sends it.
    Support::grant($binding, '111111111111111111', ['can_chat' => true]);

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['role_ids' => ['111111111111111111']]))->assertNoContent();
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody(['role_ids' => []]))->assertForbidden();
});

test('console commands need a role with Console', function (): void {
    $binding = Support::bind($this->server, $this->guild);
    Support::role($this->guild, '333333333333333333');
    Support::grant($binding, '333333333333333333', ['can_view' => true]);
    $body = ['guild_id' => '111111111111111111', 'discord_user_id' => '222222222222222222', 'role_ids' => ['333333333333333333'], 'command' => 'list'];

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/commands", $body)->assertForbidden();

    DB::table('ext_pterorelay_role_permissions')->update(['can_console' => true]);
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/commands", $body)->assertSuccessful();
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/commands", [...$body, 'command' => "list\nop x"])->assertUnprocessable();

    expect($this->sent)->toBe(['list']);
});

test('the agent can never command its own server', function (): void {
    $binding = Support::bind($this->server, $this->guild);
    Support::role($this->guild, '333333333333333333');
    Support::grant($binding, '333333333333333333', ['can_console' => true]);
    $this->agent->forceFill(['server_uuid' => $this->server->uuid])->save();
    $body = ['guild_id' => '111111111111111111', 'discord_user_id' => '222222222222222222', 'role_ids' => ['333333333333333333'], 'command' => 'list'];

    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/commands", $body)->assertForbidden();
    agentCall($this, $this->agent, 'POST', "/pterorelay-agent/servers/{$this->server->uuid}/chat", chatBody())->assertForbidden();

    expect($this->sent)->toBe([]);
});

test('link codes are only issued for the agent\'s own guilds', function (): void {
    $code = agentCall($this, $this->agent, 'POST', '/pterorelay-agent/guilds/111111111111111111/link-codes', ['discord_user_id' => '222222222222222222'])
        ->assertOk()->json('code');

    expect($code)->toMatch('/^[A-Z2-9]{5}-[A-Z2-9]{5}$/');

    Support::guild(Support::agent(), '999999999999999999');
    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/guilds/999999999999999999/link-codes', ['discord_user_id' => '222222222222222222'])
        ->assertNotFound();
});

function leftAt(int $guildId): mixed
{
    return DB::table('ext_pterorelay_guilds')->where('id', $guildId)->value('left_at');
}

test('the heartbeat marks guilds the bot no longer reports as left and clears it when they are back', function (): void {
    $kept = Support::guild($this->agent, '222222222222222222');

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => [
        ['id' => '222222222222222222', 'name' => 'Kept'],
    ]])->assertSuccessful();
    expect(leftAt($this->guild))->not->toBeNull()
        ->and(leftAt($kept))->toBeNull();

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => [
        ['id' => '111111111111111111', 'name' => 'Back'], ['id' => '222222222222222222', 'name' => 'Kept'],
    ]])->assertSuccessful();
    expect(leftAt($this->guild))->toBeNull();
});

test('a guild the bot left over 30 days ago is deleted with its links', function (): void {
    $binding = Support::bind($this->server, $this->guild);
    Support::grant($binding, '333333333333333333', ['can_view' => true]);
    $recent = Support::guild($this->agent, '222222222222222222');
    DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->update(['left_at' => now()->subDays(31)]);
    DB::table('ext_pterorelay_guilds')->where('id', $recent)->update(['left_at' => now()->subDays(29)]);

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => []])->assertSuccessful();

    expect(DB::table('ext_pterorelay_guilds')->where('id', $this->guild)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_server_guild')->where('id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_role_permissions')->where('server_guild_id', $binding)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_guilds')->where('id', $recent)->exists())->toBeTrue();
});

test('the heartbeat never marks or deletes guilds of other agents', function (): void {
    $other = Support::agent();
    $present = Support::guild($other, '999999999999999999');
    $old = Support::guild($other, '888888888888888888');
    DB::table('ext_pterorelay_guilds')->where('id', $old)->update(['left_at' => now()->subDays(60)]);

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => [
        ['id' => '111111111111111111', 'name' => 'Test guild'],
    ]])->assertSuccessful();

    expect(leftAt($present))->toBeNull()
        ->and(DB::table('ext_pterorelay_guilds')->where('id', $old)->exists())->toBeTrue();
});

test('a heartbeat without the guilds key leaves guilds alone', function (): void {
    $old = Support::guild($this->agent, '222222222222222222');
    DB::table('ext_pterorelay_guilds')->where('id', $old)->update(['left_at' => now()->subDays(60)]);

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['bot' => null])->assertSuccessful();

    expect(leftAt($this->guild))->toBeNull()
        ->and(DB::table('ext_pterorelay_guilds')->where('id', $old)->exists())->toBeTrue();
});

test('config skips guilds the bot has left and their servers', function (): void {
    [, $other] = $this->generateTestAccount();
    $left = Support::guild($this->agent, '222222222222222222');
    DB::table('ext_pterorelay_guilds')->where('id', $left)->update(['left_at' => now()->subDay()]);
    Support::bind($this->server, $this->guild);
    Support::bind($other, $left);

    $config = agentCall($this, $this->agent, 'GET', '/pterorelay-agent/config')->assertOk();

    expect(array_column($config->json('guilds'), 'discord_id'))->toBe(['111111111111111111'])
        ->and(array_column($config->json('servers'), 'uuid'))->toBe([$this->server->uuid]);
});

test('the heartbeat prunes audit entries older than 90 days', function (): void {
    $row = fn (\DateTimeInterface $at): int => (int) DB::table('ext_pterorelay_audit_logs')->insertGetId([
        'server_id' => $this->server->id, 'guild_id' => '111111111111111111', 'discord_user_id' => '222222222222222222',
        'action' => 'chat.message', 'successful' => true, 'created_at' => $at, 'updated_at' => $at,
    ]);
    $old = $row(now()->subDays(91));
    $recent = $row(now()->subDays(89));
    \Illuminate\Support\Facades\Cache::forget('pterorelay:audit-prune');

    agentCall($this, $this->agent, 'POST', '/pterorelay-agent/heartbeat', ['guilds' => [['id' => '111111111111111111', 'name' => 'Test guild']]])->assertSuccessful();

    expect(DB::table('ext_pterorelay_audit_logs')->where('id', $old)->exists())->toBeFalse()
        ->and(DB::table('ext_pterorelay_audit_logs')->where('id', $recent)->exists())->toBeTrue();
});
