<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            // The address players connect to (host or host:port), shown by /info in Discord.
            $table->string('address', 255)->nullable()->after('notification_channel_id');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->dropColumn('address');
        });
    }
};
