# WordPress Site Manager - Project Knowledge

## Overview
A lightweight web application for managing WordPress sites via 1Panel API.
All UI is in Chinese.

## Architecture
- **Backend**: Flask (Python) - REST API + static file serving
- **Frontend**: Vue.js 3 (CDN) + Tailwind CSS
- **Database**: SQLite (wp_manager.db) — IDs are INTEGER AUTOINCREMENT with compact on delete
- **External API**: 1Panel v2 API (http://167.172.142.95:3500)
- **Production Server**: gunicorn + gevent

## Quick Start

### Script
```bash
./start.sh install   # Install deps
./start.sh start     # Production mode
./start.sh dev       # Dev mode
./start.sh restart   # Restart
```

### Docker
```bash
docker compose up -d        # Start
docker compose up -d --build  # Rebuild (after code changes)
docker compose logs -f      # Logs
docker compose down         # Stop
```

## Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| WP_PORT | 8011 | Server port |
| WP_HOST | 0.0.0.0 | Bind address |
| WP_WORKERS | 4 | gunicorn workers |
| WP_DATA_DIR | (backend dir) | Database file directory |
| PANEL_HOST | 167.172.142.95 | 1Panel host |
| PANEL_PORT | 3500 | 1Panel port |
| PANEL_API_KEY | gk7FQSSTtnudJbImg0E8MdXbmU3v7qF6 | 1Panel API key |
| ADMIN_USERNAME | adsadmin | Admin username |
| ADMIN_PASSWORD | Mm123567.. | Admin password |

## Docker Deployment (IMPORTANT)
- **Code in image, data in volumes** (code/data separation)
- Volume: `wp-db:/app/backend/data` — SQLite database
- Volume: `wp-plugins:/app/backend/plugins` — Uploaded plugins
- Volume: `wp-logs:/app/logs` — Logs
- After code changes: must `docker compose up -d --build` to rebuild image
- DB auto-migration: old TEXT-id tables auto-migrated to INTEGER-id on startup

## Database ID System (as of 2026-05-06)
- All tables use `INTEGER PRIMARY KEY AUTOINCREMENT` (not UUID)
- **Compact IDs**: After deleting a record, remaining IDs are renumbered to be continuous
  - e.g. Delete ID=2 from [1,2,3] -> remaining become [1,2]
- **Reset on empty**: When all records deleted, next ID starts from 1
- **Cross-reference update**: `bg_tasks.site_id` updated when site IDs are compacted
- **Auto-migration**: Old DBs with TEXT PRIMARY KEY auto-migrated to INTEGER on startup
- Frontend stops polling for site IDs that no longer exist after compact

## Key Endpoints (all use integer IDs)
- POST /api/auth/login
- GET /api/sites | POST /api/sites
- GET /api/sites/<int:id> | PUT /api/sites/<int:id> | DELETE /api/sites/<int:id>
- POST /api/sites/<int:id>/fix-website
- GET /api/sites/export/csv
- POST /api/wordpress/batch-create (accepts website_group_id, plugin_ids)
- GET /api/wordpress/install-status/<int:site_id>
- GET /api/plugins | POST /api/plugins
- DELETE /api/plugins/<int:id> | POST /api/plugins/<int:id>/toggle
- GET /api/config | PUT /api/config

## 1Panel API Authentication
- Method: MD5('1panel' + apiKey + timestamp)
- Headers: 1Panel-Token + 1Panel-Timestamp
- Server: 167.172.142.95:3500, Base: /api/v2/

## WordPress Installation Workflow
1. Create database via POST /databases (base64 password)
2. Create website + install WP via POST /websites (appType="new")
3. Auto-install WP via HTTP POST to wp-admin/install.php

## Batch Creation Features
- Auto port assignment from base_port
- Port conflict avoidance (1Panel apps + host ports)
- Plugin auto-install via wp-admin HTTP upload + activate
- website_group_id parameter accepted (defaults to auto-detected group)

## Dependencies
Python: flask, flask-cors, flask-jwt-extended, requests, gunicorn, gevent
Frontend: Vue 3 (CDN), Tailwind CSS (CDN), Font Awesome (CDN)
