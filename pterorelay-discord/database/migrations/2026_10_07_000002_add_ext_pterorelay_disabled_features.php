<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->json('disabled_features')->nullable()->after('event_colors');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_server_guild', function (Blueprint $table): void {
            $table->dropColumn('disabled_features');
        });
    }
};
