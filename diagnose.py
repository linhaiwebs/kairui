import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(2)
    out = c.recv(16384).decode(errors='replace')
    c.close()
    return out

# 1. MySQL slow queries
print("=== MySQL Slow Query Config ===")
print(run("mysql -u wpuser -pwp_password_2024 -e \"SHOW VARIABLES LIKE '%slow%'\" 2>/dev/null"))

# 2. Top 10 tables by size
print("=== Largest Tables ===")
print(run("mysql -u wpuser -pwp_password_2024 -e \"SELECT TABLE_NAME, ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,2) AS size_mb FROM information_schema.TABLES WHERE TABLE_SCHEMA='wordpress' ORDER BY size_mb DESC LIMIT 10\" 2>/dev/null"))

# 3. Check postmeta count (common WP bottleneck)
print("=== wp_2 Postmeta Count ===")
print(run("mysql -u wpuser -pwp_password_2024 -N -s -e \"SELECT COUNT(*) FROM wp_2_postmeta\" 2>/dev/null"))

# 4. Check for orphaned postmeta
print("=== Orphaned postmeta ===")
print(run("mysql -u wpuser -pwp_password_2024 -N -s -e \"SELECT COUNT(*) FROM wp_2_postmeta pm LEFT JOIN wp_2_posts p ON pm.post_id=p.ID WHERE p.ID IS NULL\" 2>/dev/null"))

# 5. Redis cache hit rate
print("=== Redis Stats ===")
print(run("redis-cli INFO stats 2>/dev/null | grep -E 'keyspace_hits|keyspace_misses|used_memory_human'"))

# 6. WP Cron jobs
print("=== WP Cron count (blog 2) ===")
print(run("mysql -u wpuser -pwp_password_2024 -N -s -e \"SELECT COUNT(*) FROM wp_2_options WHERE option_name LIKE '%cron%'\" 2>/dev/null"))

# 7. Recent slow queries from slow log
print("=== Slow Log (if exists) ===")
print(run("tail -20 /var/log/mysql/slow.log 2>/dev/null || echo 'no slow log'"))

# 8. PHP-FPM status
print("=== PHP-FPM Workers ===")
print(run("ps aux | grep 'php-fpm: pool' | grep -v grep | wc -l"))

t.close()
