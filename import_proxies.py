from models import get_db
import json, os, re
from services.mc_auto_register import get_profiles_root

db = get_db()

# Read all config.json proxies
kits = db.execute("""
    SELECT bk.id, bk.name, bk.cloakbrowser_profile_name
    FROM brand_kits bk
    WHERE bk.cloakbrowser_profile_name IS NOT NULL
    ORDER BY bk.id
""").fetchall()

migrations = []
for k in kits:
    pn = k["cloakbrowser_profile_name"]
    cfg_path = os.path.join(get_profiles_root(), pn, "config.json")
    if not os.path.isfile(cfg_path):
        continue
    with open(cfg_path) as f:
        cfg = json.load(f)
    proxy = (cfg.get("proxy") or "").strip()
    if not proxy:
        continue
    migrations.append({"kit_id": k["id"], "kit_name": k["name"], "proxy_url": proxy})

# Parse proxy URLs and show what will be inserted
print("=== Proxies to import from config.json ===")
seen = set()
for m in migrations:
    url = m["proxy_url"]
    # Parse: socks5://user:pass@host:port
    match = re.match(r"socks5://([^:]+):([^@]+)@([^:]+):(\d+)", url)
    if match:
        user, passwd, host, port = match.groups()
        key = f"{host}:{port}"
        dup = " (DUP)" if key in seen else ""
        seen.add(key)
        print(f"Kit {m[kit_id]:>3} {m[kit_name]:<12} socks5://{user[:10]}...@{host}:{port}{dup}")

print(f"\nTotal: {len(migrations)} proxies, {len(seen)} unique IPs")
print(f"Note: pool ID 101 already has same credentials, will NOT duplicate")
db.close()
