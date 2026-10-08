<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('ext_pterosync_agents', function (Blueprint $table): void {
            $table->id();
            $table->uuid('public_id')->unique();
            $table->string('name', 80);
            $table->text('secret');
            $table->string('version', 40)->nullable();
            $table->timestamp('last_seen_at')->nullable();
            $table->timestamps();
        });

        Schema::create('ext_pterosync_guilds', function (Blueprint $table): void {
            $table->id();
            $table->foreignId('agent_id')->constrained('ext_pterosync_agents')->cascadeOnDelete();
            $table->string('discord_id', 20)->unique();
            $table->string('name', 100);
            $table->string('locale', 10)->default('en');
            $table->string('command_channel_id', 20)->nullable();
            $table->string('notification_channel_id', 20)->nullable();
            $table->timestamps();
        });

        Schema::create('ext_pterosync_server_guild', function (Blueprint $table): void {
            $table->id();
            $table->unsignedInteger('server_id');
            $table->foreignId('guild_id')->constrained('ext_pterosync_guilds')->cascadeOnDelete();
            $table->boolean('enabled')->default(true);
            $table->string('game_adapter', 40)->nullable();
            $table->json('adapter_config')->nullable();
            $table->string('live_channel_id', 20)->nullable();
            $table->string('live_message_id', 20)->nullable();
            $table->timestamps();
            $table->foreign('server_id')->references('id')->on('servers')->cascadeOnDelete();
            $table->unique(['server_id', 'guild_id']);
        });

        Schema::create('ext_pterosync_discord_roles', function (Blueprint $table): void {
            $table->id();
            $table->foreignId('guild_id')->constrained('ext_pterosync_guilds')->cascadeOnDelete();
            $table->string('discord_id', 20);
            $table->string('name', 100);
            $table->integer('position')->default(0);
            $table->boolean('managed')->default(false);
            $table->timestamps();
            $table->unique(['guild_id', 'discord_id']);
        });

        Schema::create('ext_pterosync_role_permissions', function (Blueprint $table): void {
            $table->id();
            $table->foreignId('server_guild_id')->constrained('ext_pterosync_server_guild')->cascadeOnDelete();
            $table->string('discord_role_id', 20);
            $table->boolean('can_view')->default(false);
            $table->boolean('can_power')->default(false);
            $table->boolean('can_console')->default(false);
            $table->boolean('can_configure')->default(false);
            $table->timestamps();
            // Keep the explicit name below MySQL's 64-character identifier limit.
            $table->unique(['server_guild_id', 'discord_role_id'], 'psync_role_permissions_unique');
        });

        Schema::create('ext_pterosync_audit_logs', function (Blueprint $table): void {
            $table->id();
            $table->unsignedInteger('server_id')->nullable();
            $table->string('guild_id', 20);
            $table->string('discord_user_id', 20);
            $table->string('action', 80);
            $table->json('metadata')->nullable();
            $table->boolean('successful')->default(false);
            $table->timestamps();
            $table->index(['guild_id', 'created_at']);
            $table->foreign('server_id')->references('id')->on('servers')->nullOnDelete();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('ext_pterosync_audit_logs');
        Schema::dropIfExists('ext_pterosync_role_permissions');
        Schema::dropIfExists('ext_pterosync_discord_roles');
        Schema::dropIfExists('ext_pterosync_server_guild');
        Schema::dropIfExists('ext_pterosync_guilds');
        Schema::dropIfExists('ext_pterosync_agents');
    }
};
