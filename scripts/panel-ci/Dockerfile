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
WORKDIR /panel
