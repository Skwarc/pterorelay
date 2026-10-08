<?php

use Illuminate\Support\Facades\Route;
use PteroSync\Http\Controllers\AdminController;

Route::get('/overview', [AdminController::class, 'overview']);
Route::get('/updates', [AdminController::class, 'updates']);
Route::post('/discord/validate-token', [AdminController::class, 'validateToken']);
Route::put('/guilds/{guildId}', [AdminController::class, 'updateGuild'])->whereNumber('guildId');
Route::post('/agents', [AdminController::class, 'createAgent']);
Route::post('/agents/{publicId}/rotate-secret', [AdminController::class, 'rotateAgentSecret']);
Route::delete('/agents/{publicId}', [AdminController::class, 'deleteAgent']);
