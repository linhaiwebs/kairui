import json
import os
import sqlite3
import uuid
from datetime import datetime

from flask import current_app

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wp_manager.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY,
            site_name TEXT NOT NULL,
            url TEXT NOT NULL,
            admin_name TEXT DEFAULT '',
            admin_password TEXT DEFAULT '',
            tag TEXT DEFAULT '',
            security_id TEXT DEFAULT '',
            http_username TEXT DEFAULT '',
            http_password TEXT DEFAULT '',
            verify_certificate INTEGER DEFAULT 1,
            ssl_version TEXT DEFAULT 'auto',
            panel_website_id INTEGER,
            panel_app_install_id INTEGER,
            panel_app_detail_id INTEGER,
            port INTEGER,
            nginx_alias TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_key TEXT UNIQUE NOT NULL,
            config_value TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wordpress_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_name TEXT UNIQUE NOT NULL,
            setting_value TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plugins (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bg_tasks (
            id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message TEXT DEFAULT '',
            result TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Insert default global config
    defaults = {
        "default_admin_name": "admin",
        "default_admin_password": "",
        "default_plugins": "[]",
        "default_themes": "[]",
        "db_service": "mariadb",
    }
    for key, value in defaults.items():
        cursor.execute(
            "INSERT OR IGNORE INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.utcnow().isoformat()),
        )

    conn.commit()
    conn.close()


# ---- Site CRUD ----

def create_site(data):
    conn = get_db()
    site_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO sites 
               (id, site_name, url, admin_name, admin_password, tag, security_id,
                http_username, http_password, verify_certificate, ssl_version,
                panel_website_id, panel_app_install_id, panel_app_detail_id,
                port, nginx_alias,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                site_id,
                data.get("site_name", ""),
                data.get("url", ""),
                data.get("admin_name", ""),
                data.get("admin_password", ""),
                data.get("tag", ""),
                data.get("security_id", ""),
                data.get("http_username", ""),
                data.get("http_password", ""),
                1 if data.get("verify_certificate", True) else 0,
                data.get("ssl_version", "auto"),
                data.get("panel_website_id"),
                data.get("panel_app_install_id"),
                data.get("panel_app_detail_id"),
                data.get("port"),
                data.get("nginx_alias", ""),
                data.get("status", "active"),
                now,
                now,
            ),
        )
        conn.commit()
        return get_site(site_id)
    finally:
        conn.close()


def get_site(site_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sites():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM sites ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_site(site_id, data):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        sets = []
        vals = []
        for key in [
            "site_name", "url", "admin_name", "admin_password", "tag", "security_id",
            "http_username", "http_password", "verify_certificate", "ssl_version",
            "panel_website_id", "panel_app_install_id", "panel_app_detail_id",
            "port", "nginx_alias", "status",
        ]:
            if key in data:
                sets.append(f"{key} = ?")
                vals.append(data[key])
        if not sets:
            return get_site(site_id)
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(site_id)
        conn.execute(f"UPDATE sites SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        return get_site(site_id)
    finally:
        conn.close()


def delete_site(site_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    finally:
        conn.close()


# ---- Global Config ----

def get_global_config():
    conn = get_db()
    try:
        rows = conn.execute("SELECT config_key, config_value FROM global_config").fetchall()
        return {r["config_key"]: r["config_value"] for r in rows}
    finally:
        conn.close()


def update_global_config(key, value):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value) if isinstance(value, (list, dict)) else value, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---- Plugin CRUD ----

def create_plugin(data):
    conn = get_db()
    plugin_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO plugins (id, name, filename, file_path, file_size, description, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plugin_id,
                data.get("name", ""),
                data.get("filename", ""),
                data.get("file_path", ""),
                data.get("file_size", 0),
                data.get("description", ""),
                1 if data.get("enabled", True) else 0,
                now, now,
            ),
        )
        conn.commit()
        return get_plugin(plugin_id)
    finally:
        conn.close()


def get_plugin(plugin_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_plugins():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM plugins ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_plugin(plugin_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT file_path FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        if row and row["file_path"] and os.path.isfile(row["file_path"]):
            os.remove(row["file_path"])
        conn.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,))
        conn.commit()
    finally:
        conn.close()


def get_enabled_plugins():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM plugins WHERE enabled = 1").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- Background Tasks CRUD ----

def create_bg_task(task_id, task_type, status="pending", message="", result=""):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO bg_tasks (id, task_type, status, message, result, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, task_type, status, message, result, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_bg_task(task_id, status=None, message=None, result=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        if status is not None:
            conn.execute("UPDATE bg_tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id))
        if message is not None:
            conn.execute("UPDATE bg_tasks SET message = ?, updated_at = ? WHERE id = ?", (message, now, task_id))
        if result is not None:
            conn.execute("UPDATE bg_tasks SET result = ?, updated_at = ? WHERE id = ?", (result, now, task_id))
        conn.commit()
    finally:
        conn.close()


def get_bg_task(task_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM bg_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
