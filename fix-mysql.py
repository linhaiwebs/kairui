import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, w=3):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(w)
    out = c.recv(8192).decode(errors='replace')
    c.close()
    return out.strip()

# 1. Fix query_string bypass
print("=== Fix query_string cache bypass ===")
run("sed -i 's/if ($query_string ~ \".*\") { set $skip_cache 1; }/if ($query_string ~ \"add-to-cart|wc-ajax|order-received\") { set $skip_cache 1; }/' /etc/nginx/sites-enabled/waystores", 1)
run("nginx -t && systemctl reload nginx", 2)
print("Nginx reloaded")

# 2. Reduce MySQL buffer pool
print("\n=== Reduce MySQL memory ===")
run("""mysql -u wpuser -pwp_password_2024 -e "SET GLOBAL innodb_buffer_pool_size=2147483648" 2>/dev/null""", 2)
print("Buffer pool set to 2GB")

# 3. Restart PHP
print("\n=== Restart PHP ===")
run("systemctl restart php8.3-fpm", 3)

# 4. Status
time.sleep(3)
print("\n=== Status ===")
print(run("top -bn1 | head -3", 2))

t.close()
