<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            // Where players download the mods or modpack (https), shown by /info in Discord.
            $table->string('mods_url', 500)->nullable()->after('address');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->dropColumn('mods_url');
        });
    }
};
