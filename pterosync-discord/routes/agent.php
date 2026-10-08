<?php

use Illuminate\Support\Facades\Route;
use PteroSync\Http\Controllers\AgentController;
use PteroSync\Http\Middleware\AuthenticateAgent;

// Agent requests are HMAC-signed, not session-based, so CSRF must not apply.
// Root routes run in the panel's `web` group; exclude every CSRF middleware name
// the panel may use (missing classes are ignored by the router).
$csrfMiddleware = [
    'Illuminate\Foundation\Http\Middleware\PreventRequestForgery',
    'Illuminate\Foundation\Http\Middleware\ValidateCsrfToken',
    'Illuminate\Foundation\Http\Middleware\VerifyCsrfToken',
    'Pterodactyl\Http\Middleware\VerifyCsrfToken',
];

// The agent bundle is public (the agent is open source): the PteroSync Agent egg installs
// from it and agents update to the extension's version when they restart.
$extensionRoot = dirname(__DIR__);
Route::get('/bundle/version', function () use ($extensionRoot) {
    $manifest = json_decode((string) file_get_contents($extensionRoot.'/extension.json'), true);

    return response((string) ($manifest['version'] ?? ''), 200, ['Content-Type' => 'text/plain', 'Cache-Control' => 'no-store']);
});
Route::get('/bundle', function () use ($extensionRoot) {
    // Under resources/ so the panel's own packager (p:extension:pack) ships it too.
    $bundle = $extensionRoot.'/resources/agent/pterosync-agent.zip';
    abort_unless(is_file($bundle), 404, 'This build of the extension does not include the agent bundle.');

    return response()->download($bundle, 'pterosync-agent.zip', ['Content-Type' => 'application/zip', 'Cache-Control' => 'no-store']);
});

Route::withoutMiddleware($csrfMiddleware)->middleware(AuthenticateAgent::class)->group(function (): void {
    Route::post('/heartbeat', [AgentController::class, 'heartbeat']);
    Route::get('/config', [AgentController::class, 'config']);
    Route::get('/servers/{server:uuid}/status', [AgentController::class, 'serviceStatus']);
    Route::post('/servers/{server:uuid}/power', [AgentController::class, 'power']);
    Route::post('/servers/{server:uuid}/commands', [AgentController::class, 'command']);
    Route::post('/servers/{server:uuid}/chat', [AgentController::class, 'chat']);
    Route::get('/servers/{server:uuid}/logs', [AgentController::class, 'logs']);
    Route::get('/servers/{server:uuid}/websocket', [AgentController::class, 'websocket']);
    Route::post('/logs', [AgentController::class, 'logsBatch']);
    Route::post('/guilds/{discordId}/link-codes', [AgentController::class, 'linkCode'])->where('discordId', '\d{1,20}');
});
