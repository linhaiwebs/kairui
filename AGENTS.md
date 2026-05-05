# WordPress Site Manager - Project Knowledge

## Overview
A lightweight web application for managing WordPress sites via 1Panel API.
All UI is in Chinese (中文).

## Architecture
- **Backend**: Flask (Python) - REST API + static file serving
- **Frontend**: Vue.js 3 (CDN) + Tailwind CSS
- **Database**: SQLite (wp_manager.db)
- **External API**: 1Panel v2 API (http://167.172.142.95:3500)
- **Production Server**: gunicorn + gevent

## Quick Start (一键部署)

### 方式一：启动脚本
```bash
./start.sh install   # 安装依赖
./start.sh start     # 生产模式启动 (后台)
./start.sh dev       # 开发模式启动 (前台)
./start.sh status    # 查看状态
./start.sh stop      # 停止
./start.sh restart   # 重启
```

### 方式二：Makefile
```bash
make install    # 安装依赖
make start      # 生产模式启动
make dev        # 开发模式启动
make status     # 查看状态
make stop       # 停止
make restart    # 重启
```

### 方式三：Docker
```bash
docker compose up -d        # 启动
docker compose logs -f      # 日志
docker compose down         # 停止
```

### 方式四：Systemd (生产环境)
```bash
sudo cp wp-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wp-manager
sudo systemctl start wp-manager
```

## Environment Variables (环境变量)
| 变量 | 默认值 | 说明 |
|------|--------|------|
| WP_PORT | 8011 | 服务端口 |
| WP_HOST | 0.0.0.0 | 绑定地址 |
| WP_WORKERS | 4 | gunicorn worker 数 |
| PANEL_HOST | 167.172.142.95 | 1Panel 主机 |
| PANEL_PORT | 3500 | 1Panel 端口 |
| PANEL_API_KEY | gk7FQSSTtnudJbImg0E8MdXbmU3v7qF6 | 1Panel API密钥 |
| ADMIN_USERNAME | adsadmin | 管理员用户名 |
| ADMIN_PASSWORD | Mm123567.. | 管理员密码 |
| SECRET_KEY | wp-manager-secret-key... | Flask密钥 |
| JWT_SECRET_KEY | jwt-secret-key... | JWT密钥 |

## Key Files
- `backend/app.py` - Flask app entry point (includes global error handlers)
- `backend/config.py` - Configuration (reads from env vars)
- `backend/models.py` - SQLite data models and CRUD operations
- `backend/panel_client.py` - 1Panel API client (with connection/timeout error handling)
- `backend/routes.py` - Flask route definitions (all wrapped in try/except)
- `frontend/templates/index.html` - Main HTML template
- `frontend/static/js/api.js` - Frontend API helper (with network error handling)
- `frontend/static/js/app.js` - Vue.js application (Chinese UI)
- `frontend/static/css/app.css` - Custom CSS
- `start.sh` - 一键启动脚本
- `Dockerfile` - Docker容器构建
- `docker-compose.yml` - Docker Compose编排
- `Makefile` - Make命令
- `requirements.txt` - Python依赖
- `wp-manager.service` - Systemd服务文件
- `.env.example` - 环境变量模板

## Running
```bash
cd /workspace/project/backend
PORT=8011 python3 app.py    # 开发模式
```
Production: `./start.sh start` (uses gunicorn + gevent)

## 1Panel API Authentication
- Method: MD5('1panel' + apiKey + timestamp)
- Headers: `1Panel-Token` (MD5 hash) + `1Panel-Timestamp` (unix timestamp)
- API Key: Configured in config.py (default: gk7FQSSTtnudJbImg0E8MdXbmU3v7qF6)
- Server: 167.172.142.95:3500
- Base path: /api/v2/

## Critical 1Panel API Patterns

### WordPress Installation Workflow (CORRECT - as of 2026-05)
1. **Create database first** via `POST /databases` with base64-encoded password
   - The `database` field must be the 1Panel DB service name (e.g., "mariadb")
   - Password must be base64-encoded: `base64.b64encode(password.encode()).decode()`
2. **Install WordPress** via `POST /apps/install` with:
   - `PANEL_DB_HOST` = 1Panel database name (e.g., "mariadb"), NOT container name
   - 1Panel auto-resolves this to the actual service address and sets PANEL_DB_PORT
   - `advanced: true` and `allowPort: true` for external access
3. **Create deployment website** via `POST /websites` with:
   - `WebsiteGroupID: 1` (1Panel requires this, even if no groups exist)
   - `domains: [{"domain": "example.com", "port": 80}]` (array of objects, NOT strings)
   - `appType: "installed"` + `appInstallID: <id>` to link to installed WP app
4. **Auto-install WordPress** via HTTP POST to wp-admin/install.php

### 1Panel API Field Naming Conventions
**CRITICAL**: 1Panel uses Go struct tags with specific capitalization:
- `WebsiteGroupID` (NOT `webSiteGroupID`) - capital W and G
- `OrderBy` / `Order` (NOT `orderBy` / `order`) - capital O
- `Page` / `PageSize` for databases/search (but lowercase `page`/`pageSize` for apps)
- `DeleteApp` / `DeleteBackup` / `ForceDelete` / `DeleteDB` (NOT camelCase)
- `DeleteUser` / `ForceDelete` for databases
- `IPV6` (all caps) for website creation
- `appInstallID` (camelCase) for website creation
- **Rule of thumb**: Check swagger at `/swagger/doc.json` or test empirically

### 1Panel API Endpoint Notes
- `POST /websites/search` requires `OrderBy` and `Order` (capital O) as required fields
- `POST /databases/search` requires `Page`, `PageSize`, `OrderBy`, `Order`, `Type`, `Database`
- `POST /websites` creates a deployment website (NOT `/websites/create`)
- `POST /websites/del` deletes a website (with DeleteApp to also remove the linked app)
- `POST /apps/installed/op` operates on installed apps (operate: "delete", "start", "stop", "restart")
- `GET /websites/groups` returns website groups (returns 500 "record not found" if no groups)

### Common 1Panel API Gotchas
- `/databases` requires base64-encoded password
- `/databases/search` requires `Type`, `Database`, `OrderBy`, `Order` params (all capitalized)
- `PANEL_DB_HOST` must be the 1Panel DB service NAME, not container name — 1Panel resolves it via `databaseRepo.Get(commonRepo.WithByName(hostName))`
- `.env` file changes via `files/save` don't persist through 1Panel rebuilds
- Empty `CPUS`/`MEMORY_LIMIT` in `.env` causes Docker Compose parse failure (UpErr)
- The update endpoint is `/apps/installed/params/update`, NOT `/apps/installed/update`
- `services` in install request should be `{dbServiceName: dbServiceName}`
- "标识已存在" error: alias conflicts with existing app/website, must be unique
- "域名已被网站使用" error: domain already assigned to another website
- Deleting a website with `DeleteApp: true` also removes the linked app
- Website group ID=1 works as default even without creating a group first

## Admin Login
- Username: adsadmin
- Password: Mm123567..

## Key Endpoints
- POST /api/auth/login - Admin login
- GET /api/auth/check - Check auth token
- GET /api/sites - List all sites
- POST /api/sites - Create a site (local DB only)
- PUT /api/sites/<id> - Update a site
- DELETE /api/sites/<id> - Delete a site
- POST /api/sites/<id>/fix-website - Fix missing 1Panel website for existing WP app
- GET /api/sites/export/csv - Export sites as CSV
- POST /api/wordpress/batch-create - Batch create WordPress sites via 1Panel
- GET /api/wordpress/install-status/<site_id> - Check WP install status (polling)
- GET /api/panel/status - Check 1Panel connection
- POST /api/panel/websites/search - Search 1Panel websites
- POST /api/panel/apps/installed/search - Search 1Panel installed apps
- GET /api/plugins - List plugins
- POST /api/plugins - Upload plugin (.zip)
- DELETE /api/plugins/<id> - Delete plugin
- POST /api/plugins/<id>/toggle - Toggle plugin enabled/disabled
- GET /api/config - Get global config
- PUT /api/config - Save global config

## CSV Export Format
Site Name, Url, Admin Name, Admin Password, Tag, Security ID, HTTP Username, HTTP Password, Verify Certificate, SSL Version

## Error Handling
- All route handlers wrapped in try/except with Chinese error messages
- Global Flask error handlers for 404, 405, 500, and unhandled exceptions
- 1Panel client handles ConnectionError, Timeout, and RequestException
- Frontend API client catches network errors and returns {code:503}

## Batch Creation Features
- Auto port assignment starting from base_port (default 8081)
- Port conflict avoidance: checks 1Panel installed apps + host ports (ss)
- Plugin auto-installation via wp-admin HTTP upload + activate (not docker)
- Per-domain error handling (one failure doesn't stop the batch)

## WordPress Auto-Install (Background Thread)
- After 1Panel creates the WP app, a background thread auto-completes WP installation
- Uses HTTP POST to wp-admin/install.php (bypasses the install page)
- Status tracked in SQLite `bg_tasks` table (works across gunicorn workers)
- Frontend polls `/api/wordpress/install-status/<site_id>` every 10s

## Plugin Installation Flow
1. User uploads .zip plugin file via /api/plugins
2. On site creation, plugin_ids are passed to batch-create
3. Background thread: after WP install completes → login to wp-admin → upload plugin zip → activate
4. Uses HTTP-based installation (no docker commands needed for remote servers)
5. install_plugins_to_site(site_url, admin_user, admin_password, plugin_ids)

## Known Working Test Sites (as of 2026-05-05)
- test1.lhwebs.com:8081 - Full E2E test (1Panel website id=19, WP app id=59)
- All verified: create → deploy website → WP auto-install → plugin install → CSV export → delete

## Step 3 Fix (commit 29d20df)
- **Issue**: 1Panel deployment website (OpenResty reverse proxy) was not being created during batch-create
- **Root cause**: create_website could fail silently (only warning, no retry), and errors weren't logged
- **Fix**: Added 3-attempt retry with detailed logging, alias conflict handling
- **Added**: POST /api/sites/<id>/fix-website endpoint to repair sites missing 1Panel websites
- **Frontend**: Orange "缺网站" badge + wrench button for sites with WP app but no website

## Dependencies
Python: flask, flask-cors, flask-jwt-extended, requests, gunicorn, gevent
Frontend: Vue 3 (CDN), Tailwind CSS (CDN), Font Awesome (CDN)
