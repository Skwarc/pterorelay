<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void
    {
        // One-time codes created in Discord by a member with Manage Server; a server owner needs
        // one to link a server to that Discord server.
        Schema::create('ext_pterorelay_link_codes', function (Blueprint $table): void {
            $table->id();
            $table->foreignId('guild_id')->constrained('ext_pterorelay_guilds')->cascadeOnDelete();
            $table->string('code', 16)->unique();
            $table->string('discord_user_id', 20);
            $table->timestamp('expires_at');
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('ext_pterorelay_link_codes');
    }
};
