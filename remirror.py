import sqlite3, requests

conn = sqlite3.connect("/app/backend/data/wp_manager.db")
s = conn.execute("SELECT id, url, mirror_target, created_by FROM sites WHERE url='gwniud.shop'").fetchone()
sid = s[0]
target = s[2] or "wayfelr.com"
uid = s[3]
print("Site:", sid, "mirror:", target, "user:", uid)

login = requests.post("http://localhost:8011/api/auth/login", json={"username": "kairui-yuan", "password": "kairui2024"})
token = login.json().get("data", {}).get("token", "")
print("Token:", token[:20] if token else "NONE")

resp = requests.post("http://localhost:8011/api/sites/mirror",
    json={"target_url": "https://" + target, "site_ids": [sid]},
    headers={"Authorization": "Bearer " + token})
print("Status:", resp.status_code)
print("Body:", resp.json())
