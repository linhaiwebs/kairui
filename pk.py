from models import get_db
db = get_db()
total = db.execute("SELECT COUNT(*) FROM proxies").fetchone()[0]
by_status = db.execute("SELECT status, COUNT(*) FROM proxies GROUP BY status").fetchall()
print("Total proxies:", total)
for r in by_status:
    print("  status=%s: %d" % (r["status"], r["COUNT(*)"]))
# Show first 3 and last 3
rows = db.execute("SELECT id, ip, port, proxy_type, occupied_kit_id, occupied_kit_name FROM proxies ORDER BY id LIMIT 3").fetchall()
print("\nFirst 3:")
for r in rows:
    print("  %s | %s:%s | %s | kit=%s" % (r["id"], r["ip"], r["port"], r["proxy_type"], r["occupied_kit_name"] or "-"))
rows = db.execute("SELECT id, ip, port, proxy_type, occupied_kit_id, occupied_kit_name FROM proxies ORDER BY id DESC LIMIT 3").fetchall()
print("\nLast 3:")
for r in rows:
    print("  %s | %s:%s | %s | kit=%s" % (r["id"], r["ip"], r["port"], r["proxy_type"], r["occupied_kit_name"] or "-"))
# Check global config for proxy lists
gc = db.execute("SELECT config_key, config_value FROM global_config WHERE config_key LIKE %proxy% OR config_key LIKE %okk%").fetchall()
print("\nGlobal config proxy entries:")
for g in gc:
    print("  %s = %s" % (g["config_key"], (g["config_value"] or "")[:80]))
db.close()
