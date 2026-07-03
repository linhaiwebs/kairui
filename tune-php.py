c = open('/etc/php/8.3/fpm/pool.d/www.conf').read()
c = c.replace('pm.max_children = 50', 'pm.max_children = 10')
c = c.replace('pm.start_servers = 10', 'pm.start_servers = 5')
open('/etc/php/8.3/fpm/pool.d/www.conf','w').write(c)
print('Workers reduced')

import subprocess
subprocess.run(['systemctl','restart','mariadb'])
subprocess.run(['systemctl','start','php8.3-fpm'])
print('Services restarted')
