#!/bin/bash
# Fix PHP memory limit
sed -i 's/memory_limit = 2048M/memory_limit = 256M/' /etc/php/8.3/fpm/php.ini
grep memory_limit /etc/php/8.3/fpm/php.ini | head -3
systemctl start php8.3-fpm
free -h | head -2
