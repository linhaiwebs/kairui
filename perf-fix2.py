import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, wait=3):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(wait)
    out = c.recv(16384).decode(errors='replace')
    c.close()
    return out.strip()

# === Fix 1: Add indexes (ignore duplicate errors) ===
print("=== Fix 1: Add Indexes ===")
indexes = [
    "CREATE INDEX idx_tr_taxonomy ON wp_2_term_relationships(term_taxonomy_id)",
    "CREATE INDEX idx_tr_taxonomy ON wp_4_term_relationships(term_taxonomy_id)",
    "CREATE INDEX idx_tr_taxonomy ON wp_5_term_relationships(term_taxonomy_id)",
    "CREATE INDEX idx_tr_taxonomy ON wp_6_term_relationships(term_taxonomy_id)",
    "CREATE INDEX idx_tr_taxonomy ON wp_7_term_relationships(term_taxonomy_id)",
]
for idx in indexes:
    r = run(f'mysql -u wpuser -pwp_password_2024 wordpress -e "{idx}" 2>&1', wait=2)
    print(f"  {idx[:50]}... => {'OK' if not r else r[:80]}")

# === Fix 2: Check Redis memory ===
print("\n=== Fix 2: Redis Memory ===")
print(run("redis-cli INFO memory 2>/dev/null | grep -E 'used_memory_human|maxmemory_human|evicted_keys|mem_fragmentation'", wait=1))

# Allocate more Redis memory and set eviction policy
print(run("redis-cli CONFIG SET maxmemory 512mb 2>/dev/null", wait=1))
print(run("redis-cli CONFIG SET maxmemory-policy allkeys-lru 2>/dev/null", wait=1))
print("Redis: maxmemory set to 512MB, LRU eviction")

# === Fix 3: Enable FastCGI cache for product/category pages ===
print("\n=== Fix 3: Enable FastCGI Cache ===")
# Remove the query-string cache bypass (wp product pages can have ?add-to-cart which shouldn't bypass)
# Add cache exclusions only for cart/checkout/account/admin URLs
fcgi_fix = """
# Ensure product and category pages get cached (remove query_string bypass for non-cart URLs)
"""
# The key fix: change "if ($query_string ~ \".+\") { set $skip_cache 1; }" to only skip for cart/checkout
conf = open('/etc/nginx/sites-enabled/waystores').read()
if 'query_string ~ \".+\"' in conf:
    conf = conf.replace(
        'if ($query_string ~ \".+\") { set $skip_cache 1; }',
        'if ($query_string ~ \"add-to-cart|wc-ajax|order-received\") { set $skip_cache 1; }'
    )
    # Also fix: fastcgi_cache_bypass should only bypass for skip_cache=1
    conf = conf.replace('fastcgi_cache_bypass 1;', 'fastcgi_cache_bypass $skip_cache;')
    open('/etc/nginx/sites-enabled/waystores','w').write(conf)
    print("FastCGI cache: fixed - now caches product/category pages")

# Reload nginx
print(run("nginx -t 2>&1", wait=1))
print(run("systemctl reload nginx 2>&1", wait=1))

# === Fix 4: Redis flush and restart ===
print("\n=== Fix 4: Redis full reset ===")
print(run("redis-cli -n 1 FLUSHDB 2>/dev/null && echo 'Flushed'", wait=1))
print(run("redis-cli CONFIG SET save '' 2>/dev/null", wait=1))  # Disable RDB saves to reduce CPU

# === Final status ===
print("\n=== Final Status ===")
print(run("top -bn1 | head -3", wait=2))

t.close()
print("\nDone")
