<?php

declare(strict_types=1);

namespace PteroRelay\Models;

use Illuminate\Database\Eloquent\Model;

/**
 * @property int $id
 * @property string $public_id
 * @property string $name
 * @property string $secret
 * @property string|null $version
 * @property \Carbon\CarbonImmutable|null $last_seen_at
 * @property string|null $server_uuid
 * @property string|null $adapters
 * @property string|null $bot
 * @property string|null $diagnostics
 */
final class Agent extends Model
{
    protected $table = 'ext_pterorelay_agents';
    protected $fillable = ['public_id', 'name', 'secret', 'version', 'last_seen_at'];
    protected $hidden = ['secret'];
    protected $casts = ['secret' => 'encrypted', 'last_seen_at' => 'immutable_datetime'];

    public function getRouteKeyName(): string
    {
        return 'public_id';
    }
}
