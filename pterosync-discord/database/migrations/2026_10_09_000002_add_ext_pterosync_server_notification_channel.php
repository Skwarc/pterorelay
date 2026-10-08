<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterosync_server_guild', function (Blueprint $table): void {
            // Where start, stop and crash messages for this server go; null falls back to the
            // Discord server's notification channel.
            $table->string('notification_channel_id', 20)->nullable()->after('chat_channel_id');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterosync_server_guild', function (Blueprint $table): void {
            $table->dropColumn('notification_channel_id');
        });
    }
};
