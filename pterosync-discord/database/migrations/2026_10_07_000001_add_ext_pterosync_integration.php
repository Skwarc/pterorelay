<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            $table->json('adapters')->nullable()->after('version');
        });

        Schema::table('ext_pterosync_guilds', function (Blueprint $table): void {
            $table->string('integration_channel_id', 20)->nullable()->after('notification_channel_id');
        });

        Schema::table('ext_pterosync_server_guild', function (Blueprint $table): void {
            $table->boolean('chat_enabled')->default(false)->after('enabled');
            $table->string('chat_channel_id', 20)->nullable()->after('chat_enabled');
            $table->json('event_colors')->nullable()->after('adapter_config');
        });

        Schema::table('ext_pterosync_role_permissions', function (Blueprint $table): void {
            $table->boolean('can_chat')->default(false)->after('can_configure');
            $table->string('chat_label', 32)->nullable()->after('can_chat');
            $table->string('chat_color', 7)->nullable()->after('chat_label');
        });

        Schema::create('ext_pterosync_discord_channels', function (Blueprint $table): void {
            $table->id();
            $table->foreignId('guild_id')->constrained('ext_pterosync_guilds')->cascadeOnDelete();
            $table->string('discord_id', 20);
            $table->string('name', 100);
            $table->integer('position')->default(0);
            $table->boolean('can_send')->default(false);
            $table->boolean('can_webhook')->default(false);
            $table->timestamps();
            $table->unique(['guild_id', 'discord_id'], 'psync_discord_channels_unique');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('ext_pterosync_discord_channels');

        Schema::table('ext_pterosync_role_permissions', function (Blueprint $table): void {
            $table->dropColumn(['can_chat', 'chat_label', 'chat_color']);
        });

        Schema::table('ext_pterosync_server_guild', function (Blueprint $table): void {
            $table->dropColumn(['chat_enabled', 'chat_channel_id', 'event_colors']);
        });

        Schema::table('ext_pterosync_guilds', function (Blueprint $table): void {
            $table->dropColumn('integration_channel_id');
        });

        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            $table->dropColumn('adapters');
        });
    }
};
