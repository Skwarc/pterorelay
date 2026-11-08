<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            // Whether /info shows the modpack version, and whether changing it is announced in the chat
            // channel, optionally pinging a Discord role.
            $table->boolean('show_version')->default(true)->after('modpack_version');
            $table->boolean('announce_updates')->default(false)->after('show_version');
            $table->string('update_role_id', 20)->nullable()->after('announce_updates');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->dropColumn(['show_version', 'announce_updates', 'update_role_id']);
        });
    }
};
