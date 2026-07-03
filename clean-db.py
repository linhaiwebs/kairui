import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(sql, wait=8):
    c = t.open_session()
    c.exec_command(sql)
    time.sleep(wait)
    out = c.recv(8192).decode(errors='replace')
    c.close()
    return out.strip()

# Delete orphaned postmeta
for bid in [2, 4, 5, 6, 7]:
    cnt_sql = f"mysql -u wpuser -pwp_password_2024 wordpress -N -s -e \"SELECT COUNT(*) FROM wp_{bid}_postmeta pm LEFT JOIN wp_{bid}_posts p ON pm.post_id=p.ID WHERE p.ID IS NULL\""
    cnt = run(cnt_sql, wait=5)
    print(f"Blog {bid}: {cnt} orphaned rows")

    if cnt and cnt != '0':
        del_sql = f"mysql -u wpuser -pwp_password_2024 wordpress -e \"DELETE pm FROM wp_{bid}_postmeta pm LEFT JOIN wp_{bid}_posts p ON pm.post_id = p.ID WHERE p.ID IS NULL\""
        print(f"  Deleting...")
        run(del_sql, wait=60)
        print(f"  Deleted")

# Optimize tables
print("\nOptimizing tables...")
for tbl in ['wp_2_postmeta', 'wp_4_postmeta', 'wp_5_postmeta', 'wp_6_postmeta', 'wp_7_postmeta']:
    opt_sql = f"mysql -u wpuser -pwp_password_2024 wordpress -e \"OPTIMIZE TABLE {tbl}\""
    print(f"  {tbl}...")
    run(opt_sql, wait=60)

print("\nDone")
t.close()
