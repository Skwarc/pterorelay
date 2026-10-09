<?php

declare(strict_types=1);

namespace PteroRelay;

use Pterodactyl\Extensions\ExtensionProvider;
use Pterodactyl\Services\Extensions\ExtensionSettingDefinition;
use Pterodactyl\Services\Extensions\ExtensionSettingsDefinition;

final class PteroRelayProvider extends ExtensionProvider
{
    /** Container key of this extension's settings, for code outside the provider. */
    private const SETTINGS = 'pterorelay.settings';

    /** The GitHub repository (owner/name) for update checks and the preset library, or ''. */
    public static function repository(): string
    {
        $repository = trim((string) (app(self::SETTINGS)->get('repository') ?? ''));

        return preg_match('/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/', $repository) === 1 ? $repository : '';
    }

    public function boot(): void
    {
        // Panels changed how other code reads extension settings (ExtensionManager, later the
        // Extensions facade); the provider's own settings() exists in every version.
        $this->app->instance(self::SETTINGS, $this->settings());
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
