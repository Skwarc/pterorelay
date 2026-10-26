# PHP environment for running the Pterodactyl panel's PHPStan and Pest against the extension.
FROM php:8.4-cli
COPY --from=mlocati/php-extension-installer /usr/bin/install-php-extensions /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends git unzip default-mysql-client && rm -rf /var/lib/apt/lists/* \
    && install-php-extensions intl pdo_mysql zip bcmath gd posix pcntl redis
COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
# The test MySQL container uses a self-signed certificate.
RUN echo "memory_limit=-1" > /usr/local/etc/php/conf.d/memory.ini \
    && mkdir -p /etc/mysql/conf.d && printf '[client]\nskip-ssl\n' > /etc/mysql/conf.d/ci.cnf
# The mounted checkout belongs to another user in CI; Composer reads its version from git.
RUN git config --global --add safe.directory '*'
# Never test on a PHP with the October 2026 remote code execution flaws (CVE-2026-109618/109619).
RUN php -r 'exit(version_compare(PHP_VERSION, "8.4.27", "<") ? 1 : 0);' || (echo "PHP "$(php -r 'echo PHP_VERSION;')" is vulnerable; pull a newer php:8.4-cli" && exit 1)
WORKDIR /panel
