import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, w=2):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(w)
    out = c.recv(8192).decode(errors='replace')
    c.close()
    return out.strip()

# 1. Fix nginx.conf - remove broken limit_req_zone line
print("=== Fixing nginx.conf ===")
run("sed -i '/limit_req_zone.*product_limit/d' /etc/nginx/nginx.conf", 1)

# 2. Fix waystores - remove the broken location block and rate limit lines
print("=== Fixing waystores ===")
# Read the file and fix it with Python (on the target server)
run("""python3 -c "
lines = open('/etc/nginx/sites-enabled/waystores').readlines()
fixed = []
skip = False
for l in lines:
    if 'limit_req_zone' in l or 'product_limit' in l:
        continue
    if 'location ~ /product/' in l:
        skip = True
        continue
    if skip and l.strip() == '}':
        skip = False
        continue
    if skip:
        continue
    fixed.append(l)
open('/etc/nginx/sites-enabled/waystores','w').writelines(fixed)
print('fixed')
" """, 2)

# 3. Test and start nginx
print("=== Starting nginx ===")
result = run("nginx -t 2>&1; systemctl start nginx 2>&1; systemctl status nginx --no-pager | grep Active", 2)
print(result[:500])

t.close()
