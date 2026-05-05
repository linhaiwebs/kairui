# WordPress Site Manager - Project Knowledge

## Overview
A lightweight web application for managing WordPress sites via 1Panel API.

## Architecture
- **Backend**: Flask (Python) - REST API + static file serving
- **Frontend**: Vue.js 3 (CDN) + Tailwind CSS
- **Database**: SQLite (wp_manager.db)
- **External API**: 1Panel v2 API

## Key Files
- `backend/app.py` - Flask app entry point
- `backend/config.py` - Configuration (API key, admin credentials)
- `backend/models.py` - SQLite data models and CRUD operations
- `backend/panel_client.py` - 1Panel API client
- `backend/routes.py` - Flask route definitions
- `frontend/templates/index.html` - Main HTML template
- `frontend/static/js/api.js` - Frontend API helper
- `frontend/static/js/app.js` - Vue.js application
- `frontend/static/css/app.css` - Custom CSS

## Running
```bash
cd /workspace/project/backend
PORT=8011 python app.py
```

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
- GET /api/sites - List all sites
- POST /api/sites - Create a site
- PUT /api/sites/<id> - Update a site
- DELETE /api/sites/<id> - Delete a site
- GET /api/sites/export/csv - Export sites as CSV
- POST /api/wordpress/batch-create - Batch create WordPress sites via 1Panel
- GET /api/panel/status - Check 1Panel connection
- POST /api/panel/websites/search - Search 1Panel websites
- POST /api/panel/apps/installed/search - Search 1Panel installed apps

## CSV Export Format
Site Name, Url, Admin Name, Admin Password, Tag, Security ID, HTTP Username, HTTP Password, Verify Certificate, SSL Version

## Dependencies
flask, flask-cors, flask-jwt-extended, requests
