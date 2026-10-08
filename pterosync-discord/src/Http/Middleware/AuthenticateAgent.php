<?php

declare(strict_types=1);

namespace PteroSync\Http\Middleware;

use Closure;
use Illuminate\Support\Facades\Cache;
use Illuminate\Http\Request;
use PteroSync\Models\Agent;
use Symfony\Component\HttpFoundation\Response;

final class AuthenticateAgent
{
    public function handle(Request $request, Closure $next): Response
    {
        $id = (string) $request->header('X-PteroSync-Agent');
        $timestamp = (string) $request->header('X-PteroSync-Timestamp');
        $nonce = (string) $request->header('X-PteroSync-Nonce');
        $signature = (string) $request->header('X-PteroSync-Signature');
        abort_unless(
            preg_match('/^[0-9a-f-]{36}$/i', $id) === 1
            && ctype_digit($timestamp)
            && preg_match('/^[A-Za-z0-9_-]{16,128}$/', $nonce) === 1
            && preg_match('/^[0-9a-f]{64}$/i', $signature) === 1,
            401,
            'Invalid agent authentication headers.',
        );
        abort_if(abs(time() - (int) $timestamp) > 60, 401, 'Expired agent request.');

        /** @var Agent|null $agent */
        $agent = Agent::query()->where('public_id', $id)->first();
        abort_if($agent === null, 401);
        $payload = implode("\n", [$request->method(), '/'.$request->path(), $timestamp, $nonce, hash('sha256', $request->getContent())]);
        abort_unless(hash_equals(hash_hmac('sha256', $payload, $agent->secret), strtolower($signature)), 401);

        abort_unless(Cache::add("pterosync:nonce:{$id}:{$nonce}", true, now()->addMinutes(2)), 409, 'Replayed agent request.');

        $version = trim((string) $request->header('X-PteroSync-Version'));
        $agent->forceFill([
            'last_seen_at' => now(),
            'version' => $version === '' ? null : mb_substr($version, 0, 40),
        ])->save();
        $request->attributes->set('pterosync_agent', $agent);

        return $next($request);
    }
}
