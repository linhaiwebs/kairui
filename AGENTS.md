# WordPress Site Manager - Project Knowledge

## Overview
A lightweight web application for managing WordPress sites via 1Panel API.
All UI is in Chinese (中文).

## Architecture
- **Backend**: Flask (Python) - REST API + static file serving
- **Frontend**: Vue.js 3 (CDN) + Tailwind CSS
- **Database**: SQLite (wp_manager.db)
- **External API**: 1Panel v2 API (http://167.172.142.95:3500)

## Key Files
- `backend/app.py` - Flask app entry point (includes global error handlers)
- `backend/config.py` - Configuration (API key, admin credentials)
- `backend/models.py` - SQLite data models and CRUD operations
- `backend/panel_client.py` - 1Panel API client (with connection/timeout error handling)
- `backend/routes.py` - Flask route definitions (all wrapped in try/except)
- `frontend/templates/index.html` - Main HTML template
- `frontend/static/js/api.js` - Frontend API helper (with network error handling)
- `frontend/static/js/app.js` - Vue.js application (896 lines, Chinese UI)
- `frontend/static/css/app.css` - Custom CSS

## Running
```bash
cd /workspace/project/backend
PORT=8011 python3 app.py
```
Server runs on port 8011 (configurable via PORT env var).

## 1Panel API Authentication
- Method: MD5('1panel' + apiKey + timestamp)
- Headers: `1Panel-Token` (MD5 hash) + `1Panel-Timestamp` (unix timestamp)
- API Key: Configured in config.py (default: gk7FQSSTtnudJbImg0E8MdXbmU3v7qF6)
- Server: 167.172.142.95:3500
- Base path: /api/v2/

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
- GET /api/sites/export/csv - Export sites as CSV
- POST /api/wordpress/batch-create - Batch create WordPress sites via 1Panel
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
- Plugin auto-installation via docker cp + wp-cli
- Per-domain error handling (one failure doesn't stop the batch)

## Dependencies
flask, flask-cors, flask-jwt-extended, requests
