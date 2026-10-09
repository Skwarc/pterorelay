<?php

declare(strict_types=1);

namespace PteroRelay;

use Pterodactyl\Extensions\ExtensionProvider;
use Pterodactyl\Services\Extensions\ExtensionSettingDefinition;
use Pterodactyl\Services\Extensions\ExtensionSettingsDefinition;

final class PteroRelayProvider extends ExtensionProvider
{
    public function boot(): void
    {
        $this->excludeAgentRoutesFromCsrf();
        $this->registerApiRoutes();
        $this->registerRootRoutes($this->extensionPath('routes', 'agent.php'), 'pterorelay-agent');
        $this->loadExtensionMigrations();
        $this->loadExtensionTranslations();
        // Shown by the panel under Admin → Extensions → PteroRelay Discord → Settings.
        $this->registerSettings(new ExtensionSettingsDefinition($this->settings(), [
            ExtensionSettingDefinition::make('repository', 'repository', 'Skwarc/pterorelay', ['nullable', 'string', 'max:100', 'regex:/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/'])
                ->label('GitHub repository')
                ->help('owner/name of the PteroRelay repository on GitHub. Used to check for new releases and to load the community preset library.'),
        ]));
        $this->registerPermissions('Manage PteroRelay Discord integration.', [
            'view' => 'View Discord integration status.',
            'power' => 'Control server power from Discord.',
            'console' => 'Send server console commands from Discord.',
            'configure' => 'Configure Discord integration for this server.',
        ]);
    }

    /**
     * Agent requests are HMAC-signed; the static except list is shared by every
     * subclass of the framework CSRF middleware, including the panel's own.
     */
    private function excludeAgentRoutesFromCsrf(): void
    {
        foreach ([
            'Illuminate\Foundation\Http\Middleware\PreventRequestForgery',
            'Illuminate\Foundation\Http\Middleware\ValidateCsrfToken',
        ] as $middleware) {
            if (class_exists($middleware) && method_exists($middleware, 'except')) {
                $middleware::except(['pterorelay-agent/*']);
            }
        }
    }
}
