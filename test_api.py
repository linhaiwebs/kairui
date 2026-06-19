import requests, json
# Login first
r = requests.post("http://localhost:8011/api/auth/login", json={"username":"kairui-hui","password":""}, timeout=10)
print("Login:", r.status_code, r.json().get("code"))
token = r.json().get("data",{}).get("token","")
if token:
    # Mirror pkbb.shop (id=64)
    r2 = requests.post("http://localhost:8011/api/sites/mirror",
        json={"target_url":"https://example.com","site_ids":[64]},
        headers={"Authorization":"Bearer "+token}, timeout=30)
    print("Mirror:", r2.status_code)
    print(r2.text[:500])
