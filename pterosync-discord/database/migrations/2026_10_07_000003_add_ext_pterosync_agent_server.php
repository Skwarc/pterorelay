<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            // The Pterodactyl server the agent itself runs on, if any; it is never controlled by the agent.
            $table->uuid('server_uuid')->nullable()->after('version');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterosync_agents', function (Blueprint $table): void {
            $table->dropColumn('server_uuid');
        });
    }
};
