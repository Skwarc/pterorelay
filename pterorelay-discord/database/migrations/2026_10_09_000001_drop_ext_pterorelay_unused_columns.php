<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        // Never written: live status embeds live in the agent (/setstatusmsg), and Discord
        // itself restricts which channels may use the bot's commands.
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->dropColumn(['live_channel_id', 'live_message_id']);
        });
        Schema::table('ext_pterorelay_guilds', function (Blueprint $table): void {
            $table->dropColumn('command_channel_id');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->string('live_channel_id', 20)->nullable()->after('adapter_config');
            $table->string('live_message_id', 20)->nullable()->after('live_channel_id');
        });
        Schema::table('ext_pterorelay_guilds', function (Blueprint $table): void {
            $table->string('command_channel_id', 20)->nullable()->after('locale');
        });
    }
};
