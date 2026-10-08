<?php

// Autoload the extension's classes next to the panel's, as the panel does at runtime.
spl_autoload_register(function (string $class): void {
    $prefix = 'PteroSync\\';
    if (str_starts_with($class, $prefix)) {
        $file = '/ext/src/'.str_replace('\\', '/', substr($class, strlen($prefix))).'.php';
        if (is_file($file)) {
            require $file;
        }
    }
});
