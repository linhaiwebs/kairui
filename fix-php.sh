#!/bin/bash
# Fix PHP worker limits
CONF=/etc/php/8.3/fpm/pool.d/www.conf
# Get current settings
grep -q 'pm.max_children' $CONF || echo "pm.max_children = 50" >> $CONF
# Set to 20 workers, static mode
python3 -c "
c = open('$CONF').read()
import re
c = re.sub(r'pm\s*=\s*\w+', 'pm = static', c)
c = re.sub(r'pm\.max_children\s*=\s*\d+', 'pm.max_children = 20', c)
open('$CONF','w').write(c)
print('Workers: 20 static')
"
systemctl restart php8.3-fpm
sleep 2
ps aux | grep 'php-fpm: pool' | grep -v grep | wc -l
echo 'workers running'
free -h | head -2
