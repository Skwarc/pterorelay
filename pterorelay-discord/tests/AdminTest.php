<?php

declare(strict_types=1);

namespace Pterodactyl\Tests\Pest\Integration\PteroRelay\AdminTest;

use Illuminate\Foundation\Testing\DatabaseTransactions;
use Pterodactyl\Models\User;
use Pterodactyl\Tests\Integration\IntegrationTestCase;
use Pterodactyl\Tests\Integration\PteroRelay\Support;
use PteroRelay\Http\Controllers\AdminController;

uses(IntegrationTestCase::class, DatabaseTransactions::class);

beforeEach(function (): void {
    $this->extensionDirectory = Support::install();
});

afterEach(function (): void {
    Support::uninstall($this->extensionDirectory);
});

test('PHP versions with the October 2026 RCE flaws are flagged', function (string $version, bool $vulnerable): void {
    expect(AdminController::phpAdvisory($version, false)['vulnerable'])->toBe($vulnerable);
})->with([
    '8.4.26' => ['8.4.26', true],
    '8.4.27' => ['8.4.27', false],
    '8.3.35' => ['8.3.35', true],
    '8.3.36' => ['8.3.36', false],
    '8.5.11' => ['8.5.11', true],
    '8.5.12' => ['8.5.12', false],
    '8.2.34' => ['8.2.34', true],
    '8.1.31 (no official fix)' => ['8.1.31', true],
    '8.6.0 (newer branch)' => ['8.6.0', false],
]);

test('the admin overview reports the panel PHP', function (): void {
    $php = $this->actingAs(User::factory()->create(['root_admin' => true]))
        ->getJson('/api/admin/extensions/pterorelay-discord/overview')->assertOk()->json('php');

    expect($php['version'])->toBe(PHP_VERSION)->and($php)->toHaveKeys(['vulnerable', 'fixed_in', 'upload_progress']);
});
