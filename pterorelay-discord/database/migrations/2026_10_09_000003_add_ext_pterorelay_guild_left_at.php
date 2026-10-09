<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterorelay_guilds', function (Blueprint $table): void {
            // When the bot stopped reporting this Discord server (it was kicked or removed). Null while the bot is in
            // it; after 30 days the guild is deleted together with its links.
            $table->timestamp('left_at')->nullable()->after('locale');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_guilds', function (Blueprint $table): void {
            $table->dropColumn('left_at');
        });
    }
};
