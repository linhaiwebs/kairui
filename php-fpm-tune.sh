#!/bin/bash
CONF=/etc/php/8.3/fpm/pool.d/www.conf
# Change static to dynamic, increase children to 50
sed -i 's/pm = static/pm = dynamic/' $CONF
sed -i 's/pm.max_children = 25/pm.max_children = 50/' $CONF
# Add dynamic settings if not present
grep -q 'pm.start_servers' $CONF || sed -i '/pm.max_children/a pm.start_servers = 10' $CONF
grep -q 'pm.min_spare_servers' $CONF || sed -i '/pm.start_servers/a pm.min_spare_servers = 5' $CONF
grep -q 'pm.max_spare_servers' $CONF || sed -i '/pm.min_spare_servers/a pm.max_spare_servers = 20' $CONF

systemctl restart php8.3-fpm
sleep 2
echo "PHP-FPM workers: $(ps aux | grep php-fpm | grep -v grep | wc -l)"
systemctl status php8.3-fpm --no-pager 2>/dev/null | grep Active
