<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            // Reported by the agent on every heartbeat: the bot account and current problems.
            $table->json('bot')->nullable()->after('server_uuid');
            $table->json('diagnostics')->nullable()->after('bot');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            $table->dropColumn(['bot', 'diagnostics']);
        });
    }
};
