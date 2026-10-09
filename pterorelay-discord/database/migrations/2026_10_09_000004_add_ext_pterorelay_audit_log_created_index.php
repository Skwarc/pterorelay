<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        // Audit entries older than 90 days are pruned by age.
        Schema::table('ext_pterorelay_audit_logs', function (Blueprint $table): void {
            $table->index('created_at', 'prelay_audit_logs_created_index');
        });
    }

    public function down(): void
    {
        Schema::table('ext_pterorelay_audit_logs', function (Blueprint $table): void {
            $table->dropIndex('prelay_audit_logs_created_index');
        });
    }
};
