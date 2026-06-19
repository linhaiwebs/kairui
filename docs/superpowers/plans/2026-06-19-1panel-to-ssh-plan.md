# 1Panel → SSH Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all 1Panel API calls, replace with paramiko SSH file operations. Add server auto-initialization. Update frontend accordingly.

**Architecture:** New `ssh_client.py` wraps paramiko for file ops (mkdir/read/write/delete) and remote commands (nginx reload, server init). Routes call SSHClient instead of OnePanelClient. Frontend replaces "1Panel" indicators with "服务器" and adds init/test buttons.

**Tech Stack:** Python, paramiko, SFTP, OpenResty

---

### Task 1: Foundation — Requirements + DB Migration

**Files:**
- Modify: `requirements.txt`
- Modify: `backend/models.py`

- [ ] **Step 1: Add paramiko to requirements**

```
paramiko>=3.4.0
```

Add to `requirements.txt` after `websockify` line.

- [ ] **Step 2: Add SSH columns to panel_environments migration**

In `backend/models.py`, find the proxie migrations section (around line 654-659). Add after the existing `proxy_type` migration:

```python
    # Add SSH columns to panel_environments (replaces 1Panel API)
    try:
        pe_cols = [row[1] for row in conn.execute("PRAGMA table_info(panel_environments)").fetchall()]
        if "ssh_password" not in pe_cols:
            conn.execute("ALTER TABLE panel_environments ADD COLUMN ssh_password TEXT DEFAULT ''")
        if "ssh_initialized" not in pe_cols:
            conn.execute("ALTER TABLE panel_environments ADD COLUMN ssh_initialized INTEGER DEFAULT 0")
    except Exception:
        pass
```

- [ ] **Step 3: Commit**

```bash
git add requirements.txt backend/models.py
git commit -m "feat: add paramiko + panel_environments SSH columns"
```

---

### Task 2: SSHClient Class

**Files:**
- Create: `backend/ssh_client.py`

- [ ] **Step 1: Write SSHClient class** — create `backend/ssh_client.py`:

```python
"""SSH client for direct server file operations (replaces 1Panel API)."""
import paramiko
import logging

logger = logging.getLogger(__name__)

class SSHClient:
    def __init__(self, host, port=22, username='root', password=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._ssh = None
        self._sftp = None

    def connect(self):
        if self._ssh:
            return
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh.connect(self.host, self.port, self.username, self.password, timeout=15)
        self._sftp = self._ssh.open_sftp()

    def close(self):
        if self._sftp:
            self._sftp.close()
        if self._ssh:
            self._ssh.close()
        self._ssh = None
        self._sftp = None

    def test_connection(self):
        """Quick test: run echo and return result."""
        self.connect()
        _, stdout, _ = self._ssh.exec_command("echo ok", timeout=10)
        return stdout.read().decode().strip()

    def server_init(self):
        """One-click full server setup: install OpenResty + configure."""
        self.connect()
        commands = [
            "apt update",
            "apt install -y wget gnupg2 ca-certificates",
            "wget -qO - https://openresty.org/package/pubkey.gpg | apt-key add -",
            "echo 'deb http://openresty.org/package/debian $(lsb_release -sc) openresty' > /etc/apt/sources.list.d/openresty.list",
            "apt update",
            "apt install -y openresty",
            "systemctl enable openresty",
            "systemctl start openresty",
            "mkdir -p /www/sites /www/conf.d /www/logs",
            "grep -q 'include /www/conf.d' /usr/local/openresty/nginx/conf/nginx.conf || sed -i '/^http {/a \\    include /www/conf.d/*.conf;' /usr/local/openresty/nginx/conf/nginx.conf",
            "openresty -t && systemctl reload openresty",
        ]
        results = []
        for cmd in commands:
            _, stdout, stderr = self._ssh.exec_command(cmd, timeout=60)
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            status = "OK" if not err or "already" in err or "newest" in err else "ERR"
            results.append({"cmd": cmd[:60], "status": status, "error": err[:200] if status == "ERR" else ""})
            logger.info(f"Server init: {cmd[:60]} -> {status}")
        # Verify 80 port
        _, stdout, _ = self._ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost/", timeout=10)
        http_code = stdout.read().decode().strip()
        results.append({"cmd": "Verify port 80", "status": "OK" if http_code else "WARN", "error": f"HTTP {http_code}"})
        return results

    def reload_nginx(self):
        self.connect()
        self._ssh.exec_command("systemctl reload openresty", timeout=30)

    def mkdir_p(self, path):
        self.connect()
        self._ssh.exec_command(f"mkdir -p {path}", timeout=10)

    def write_file(self, path, content):
        """Write file via SFTP. Creates parent dirs automatically."""
        self.connect()
        import os as _os
        parent = _os.path.dirname(path)
        if parent:
            self._ssh.exec_command(f"mkdir -p {parent}", timeout=10)
        with self._sftp.open(path, 'w') as f:
            f.write(content)

    def read_file(self, path):
        self.connect()
        try:
            with self._sftp.open(path, 'r') as f:
                return f.read().decode('utf-8')
        except FileNotFoundError:
            return None

    def delete_file(self, path):
        self.connect()
        self._ssh.exec_command(f"rm -rf {path}", timeout=10)

    def file_exists(self, path):
        self.connect()
        _, stdout, _ = self._ssh.exec_command(f"test -e {path} && echo yes || echo no", timeout=10)
        return stdout.read().decode().strip() == "yes"


# Connection pool: cache by (host, port)
_ssh_pool = {}

def get_ssh_client(host, port=22, password=''):
    key = (host, port)
    if key not in _ssh_pool:
        client = SSHClient(host, port, 'root', password)
        _ssh_pool[key] = client
    return _ssh_pool[key]
```

- [ ] **Step 2: Commit**

```bash
git add backend/ssh_client.py
git commit -m "feat: add SSHClient for direct server file operations"
```

---

### Task 3: Replace file ops in `_bg_deploy_static`

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: Replace the 1Panel calls in `_bg_deploy_static`** (lines ~3307-3370).

Before (line ~3309):
```python
site_resp = _get_pc().create_static_website(domain=domain, alias=alias)
site_data = site_resp.get("data", {}) if site_resp.get("code") == 200 else {}
site_dir_1panel = site_data.get("site_dir", f"/opt/1panel/apps/openresty/openresty/www/sites/{alias}/index")
```

After:
```python
from ssh_client import get_ssh_client
ssh = get_ssh_client(panel_host, panel_port, panel_api_key)
site_dir_1panel = f"/www/sites/{alias}/index"
ssh.mkdir_p(site_dir_1panel)
```

Before (lines ~3288-3294):
```python
pc = _get_pc()
...
if parent_dir not in created_dirs:
    pc.create_file(parent_dir, is_dir=True) ...
pc.delete_file(remote_path)
pc.create_file(remote_path, is_dir=False)
save_res = pc.save_file(remote_path, content)
if save_res.get("code") == 200: ...
pc.reload_openresty()
```

After:
```python
ssh = get_ssh_client(panel_host, panel_port, panel_api_key)
...
if parent_dir not in created_dirs:
    ssh.mkdir_p(parent_dir)
    created_dirs.add(parent_dir)
ssh.write_file(remote_path, content)
uploaded += 1
...
ssh.reload_nginx()
```

Note: `panel_api_key` field now stores the SSH password (reused field name in function args).

- [ ] **Step 2: Commit**

```bash
git add backend/routes.py
git commit -m "refactor: _bg_deploy_static uses SSHClient instead of 1Panel"
```

---

### Task 4: Replace file ops in `_regenerate_static_site_html`

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: Replace 1Panel calls in `_regenerate_static_site_html`** (lines ~3265-3295).

Replace the try block:
```python
        try:
            env = get_user_panel_environment(site.get("created_by") or 1)
            pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"]) if env else panel_client
        except Exception:
            pc = panel_client
```

With:
```python
        try:
            env = get_user_panel_environment(site.get("created_by") or 1)
            ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
        except Exception:
            return
```

Then replace the file upload loop body (lines ~3284-3294):
```python
for rel_path, content in files.items():
    if rel_path.endswith(".css") or rel_path.endswith(".js"):
        continue
    remote_path = f"{site_dir_1panel}/{rel_path}"
    ssh.write_file(remote_path, content)
    uploaded += 1
```
Replace `pc.reload_openresty()` with `ssh.reload_nginx()`.

- [ ] **Step 2: Commit**

```bash
git add backend/routes.py
git commit -m "refactor: _regenerate_static_site_html uses SSHClient"
```

---

### Task 5: Replace file ops in feed functions

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: Replace in `_sync_feed_to_static_site`** (lines ~3210-3216).

Replace:
```python
env = get_user_panel_environment(site.get("created_by") or 1)
if env and nginx_alias:
    pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"])
    pc.upload_static_site_files(alias=nginx_alias, files={"feed.xml": xml_str}, website_dir=site_dir)
    pc.reload_openresty()
```

With:
```python
env = get_user_panel_environment(site.get("created_by") or 1)
if env and nginx_alias:
    feed_path = f"{site_dir}/feed.xml"
    if feed_path.startswith("/www/"):
        feed_path = f"/opt/1panel/apps/openresty/openresty{site_dir}" if "/opt/" not in site_dir else site_dir
    # Use the site's static_dir path directly
    ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
    ssh.write_file(f"{feed_path}/feed.xml", xml_str)
    ssh.reload_nginx()
```

Wait — the site_dir handling is complex. Let me simplify. The `static_dir` in DB is `/www/sites/{alias}/index`. SSH handles paths directly. Just use it:

```python
env = get_user_panel_environment(site.get("created_by") or 1)
if env and nginx_alias:
    ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
    ssh.write_file(f"/www/sites/{nginx_alias}/index/feed.xml", xml_str)
    ssh.reload_nginx()
```

- [ ] **Step 2: Replace in `_clean_feed_from_static_site`** (lines ~3237-3244).

Same pattern — use `ssh.write_file` with empty feed XML.

- [ ] **Step 3: Commit**

```bash
git add backend/routes.py
git commit -m "refactor: feed sync/clean uses SSHClient"
```

---

### Task 6: Replace in `remove_site` (static path)

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: Replace website deletion with SSH rm** (around line 2503).

For static sites, replace the 1Panel website deletion with SSH cleanup:
```python
if site.get("site_type") == "static":
    # SSH: remove site directory
    env = get_user_panel_environment(site.get("created_by") or 1)
    if env:
        try:
            ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
            alias = site.get("nginx_alias", domain)
            ssh.delete_file(f"/www/sites/{alias}")
            ssh.delete_file(f"/www/conf.d/{alias}.conf")
            ssh.reload_nginx()
            logger.info(f"SSH: removed site dir for {domain}")
        except Exception as e:
            logger.warning(f"SSH cleanup failed: {e}")
```

Remove the 1Panel website search/delete block entirely.

- [ ] **Step 2: Commit**

```bash
git add backend/routes.py
git commit -m "refactor: remove_site static path uses SSH cleanup"
```

---

### Task 7: Add server init + test endpoints

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: Add `/api/server/init` endpoint**

Add after `panel_*` routes section:
```python
@app.route("/api/server/init/<int:env_id>", methods=["POST"])
@jwt_required()
def server_init(env_id):
    """Initialize a Debian server: install OpenResty + configure."""
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify({"code": 403, "message": "仅管理员可操作"}), 403
    env = get_panel_environment(env_id)
    if not env:
        return jsonify({"code": 404, "message": "环境不存在"}), 404
    try:
        from ssh_client import SSHClient
        ssh = SSHClient(env["host"], env.get("port", 22), 'root', env.get("ssh_password", ""))
        results = ssh.server_init()
        update_panel_environment(env_id, {"ssh_initialized": 1})
        ssh.close()
        return jsonify({"code": 200, "data": {"results": results}, "message": "服务器初始化完成"})
    except Exception as e:
        return jsonify({"code": 500, "message": f"初始化失败: {str(e)[:200]}"}), 500

@app.route("/api/server/test/<int:env_id>", methods=["POST"])
@jwt_required()
def server_test(env_id):
    """Test SSH connection to a server."""
    env = get_panel_environment(env_id)
    if not env:
        return jsonify({"code": 404, "message": "环境不存在"}), 404
    try:
        from ssh_client import SSHClient
        ssh = SSHClient(env["host"], env.get("port", 22), 'root', env.get("ssh_password", ""))
        result = ssh.test_connection()
        ssh.close()
        return jsonify({"code": 200, "data": {"result": result}, "message": "连接成功"})
    except Exception as e:
        return jsonify({"code": 500, "message": f"连接失败: {str(e)[:200]}"}), 500
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes.py
git commit -m "feat: add server init and test SSH endpoints"
```

---

### Task 8: Remove 1Panel code

**Files:**
- Delete: `backend/panel_client.py`
- Modify: `backend/routes.py`

- [ ] **Step 1: Delete panel_client.py**

```bash
rm backend/panel_client.py
```

- [ ] **Step 2: Remove 1Panel passthrough routes** — Delete these route blocks from routes.py:

- `GET /api/panel/status`
- `GET /api/panel/sync`
- `/api/panel/websites/*`
- `/api/panel/apps/*`
- `/api/panel/databases/*`
- `/api/panel/groups/*`
- `/api/panel/environments/current` (keep this one!)

- [ ] **Step 3: Remove WordPress deploy functions** — Delete:
- `_bg_deploy_inner` function
- `batch_create_wordpress` route
- All `search_installed_apps` / `create_database` / `install_app` calls

- [ ] **Step 4: Remove 1Panel import from routes.py** — Remove `from panel_client import ...` and all `OnePanelClient(` references

- [ ] **Step 5: Commit**

```bash
git add backend/panel_client.py backend/routes.py
git commit -m "chore: remove 1Panel API code - panel_client + WP routes + passthrough endpoints"
```

---

### Task 9: Frontend — replace 1Panel UI with Server UI

**Files:**
- Modify: `frontend/static/js/app.js`

- [ ] **Step 1: Remove 1Panel state variables**

Delete:
- `panelConnected` (line ~27)
- `panelWebsites`, `panelInstalledApps`, `panelGroups`
- `panelGroups` in `wizardCreateSite`

- [ ] **Step 2: Replace top header 1Panel indicator**

Change:
```html
<span :class="panelConnected ? 'text-[#146c2e]' : 'text-error'">
    <span class="material-symbols-outlined" style="font-size:14px">{{ panelConnected ? 'cloud_done' : 'cloud_off' }}</span>
    {{ panelConnected ? '1Panel' : '离线' }}
</span>
```
To:
```html
<span class="text-[#146c2e]">
    <span class="material-symbols-outlined" style="font-size:14px">dns</span>
    {{ panelEnvironments.length || 0 }} 服务器
</span>
```

- [ ] **Step 3: Update system settings "1Panel 环境" tab → "服务器环境"**

Rename tab label from "1Panel 环境" to "服务器环境" (line ~4344 area).

Add SSH password field to env form (next to API key):
```html
<label>SSH 密码</label>
<input v-model="panelEnvForm.ssh_password" type="password" class="...">
```

Add port field defaulting to 22.

Add status indicator per env: `{{ env.ssh_initialized ? '🟢 已就绪' : '⏳ 未初始化' }}`

Add "初始化" button:
```html
<button v-if="!env.ssh_initialized" @click="handleServerInit(env)" 
    class="text-xs bg-green-100 text-green-700 px-2 py-1 rounded hover:bg-green-200">
    <i class="fas fa-rocket mr-1"></i>初始化
</button>
```

Add "测试连接" button:
```html
<button @click="handleServerTest(env)" class="text-xs text-primary hover:text-primary px-2 py-1">
    <i class="fas fa-plug mr-1"></i>测试
</button>
```

- [ ] **Step 4: Add JS functions for init/test**

```javascript
async function handleServerInit(env) {
    if (!confirm('将在此服务器安装OpenResty并配置站点环境，继续？')) return;
    loading.value = true;
    try {
        const r = await API.request('POST', '/api/server/init/' + env.id);
        if (r.code === 200) { showToast('初始化完成'); await loadPanelEnvironments(); }
        else showToast(r.message, 'error');
    } catch(e) { showToast('失败', 'error'); }
    loading.value = false;
}
async function handleServerTest(env) {
    const r = await API.request('POST', '/api/server/test/' + env.id);
    showToast(r.code === 200 ? '连接成功: ' + r.data.result : r.message, r.code === 200 ? 'success' : 'error');
}
```

- [ ] **Step 5: Remove 1Panel buttons**

Search and remove:
- "同步1Panel" button (sites page header)
- Panel sync button (settings page)
- "1Panel 已连接" message in wizard
- Sidebar "统计总览" menu item

- [ ] **Step 6: Update return statement**

Remove: `panelConnected, panelWebsites, panelInstalledApps, panelGroups`
Add: `handleServerInit, handleServerTest`

- [ ] **Step 7: Remove related API calls from `api.js`**

Delete methods that call `/api/panel/*` (panelStatus, panelSync, panelWebsites, etc.)

- [ ] **Step 8: Commit**

```bash
git add frontend/static/js/app.js frontend/static/js/api.js
git commit -m "refactor: frontend - replace 1Panel UI with SSH server UI"
```

---

### Task 10: Dockerfile + Deploy

**Files:**
- Modify: `Dockerfile` (if exists)

- [ ] **Step 1: Ensure paramiko is installed**

No Dockerfile changes needed — `pip install -r requirements.txt` handles it.

- [ ] **Step 2: Deploy and verify**

```bash
git push
ssh root@163.123.236.110 "cd /root/kairui && git pull && docker compose up -d --build"
```

- [ ] **Step 3: Add SSH password to existing environments**

For kairui-yuan (env_id=1), kairui-hui (env_id=2), kairui-adong (env_id=3):
```sql
UPDATE panel_environments SET ssh_password = '<user-provided-password>' WHERE id = 1;
```

- [ ] **Step 4: Test site creation with SSH**

Create a test site via wizard → verify files appear on server `/www/sites/{domain}/index/`

- [ ] **Step 5: Test server init**

Click "初始化" on an environment → verify OpenResty installed and responding on port 80.

---

