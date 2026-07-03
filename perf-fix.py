import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, wait=2):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(wait)
    out = c.recv(16384).decode(errors='replace')
    c.close()
    return out.strip()

# === 1. Add MySQL indexes (safe, no data change) ===
print("=== Step 1: Add Indexes ===")
index_sql = """
ALTER TABLE wp_2_term_relationships ADD INDEX IF NOT EXISTS idx_tr_taxonomy (term_taxonomy_id);
ALTER TABLE wp_2_posts ADD INDEX IF NOT EXISTS idx_posts_type_status (post_type, post_status);
ALTER TABLE wp_4_term_relationships ADD INDEX IF NOT EXISTS idx_tr_taxonomy (term_taxonomy_id);
ALTER TABLE wp_5_term_relationships ADD INDEX IF NOT EXISTS idx_tr_taxonomy (term_taxonomy_id);
ALTER TABLE wp_6_term_relationships ADD INDEX IF NOT EXISTS idx_tr_taxonomy (term_taxonomy_id);
ALTER TABLE wp_7_term_relationships ADD INDEX IF NOT EXISTS idx_tr_taxonomy (term_taxonomy_id);
"""
for line in index_sql.strip().split('\n'):
    line = line.strip()
    if line:
        result = run(f"mysql -u wpuser -pwp_password_2024 wordpress -e \"{line}\" 2>&1", wait=3)
        print(f"  {line[:60]}... => {result[:100]}")

# === 2. Check Redis config ===
print("\n=== Step 2: Redis Config ===")
wp_config = run("grep -E 'WP_REDIS|WP_CACHE|object-cache' /var/www/html/wp-config.php 2>/dev/null", wait=1)
print(wp_config[:500])

# Check if Redis drop-in exists
print(run("ls -la /var/www/html/wp-content/object-cache.php 2>/dev/null", wait=1))

# === 3. Count orphaned postmeta (safe - just counting) ===
print("\n=== Step 3: Orphaned Postmeta Count ===")
for bid in [2,4,5,6,7]:
    count = run(f"mysql -u wpuser -pwp_password_2024 -N -s -e \"SELECT COUNT(*) FROM wp_{bid}_postmeta pm LEFT JOIN wp_{bid}_posts p ON pm.post_id=p.ID WHERE p.ID IS NULL\" 2>/dev/null", wait=2)
    print(f"  Blog {bid}: {count} orphaned postmeta rows")

# === 4. Fix FastCGI cache ===
print("\n=== Step 4: FastCGI Cache Status ===")
fcgi = run("grep -E 'fastcgi_cache|skip_cache' /etc/nginx/sites-enabled/waystores 2>/dev/null | head -5", wait=1)
print(fcgi[:300])

# Check if cache is actually enabled
enabled = "fastcgi_cache WORDPRESS" in fcgi
print(f"  FastCGI cache enabled: {enabled}")

# === 5. Current load ===
print("\n=== Step 5: Current Server Load ===")
print(run("top -bn1 | head -3 && echo '---' && free -h | head -2", wait=2))

t.close()
print("\nDone - review results above")
