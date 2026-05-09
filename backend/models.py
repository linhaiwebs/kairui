import json
import os
import re
import sqlite3
from datetime import datetime

from flask import current_app

# In Docker: data dir is a persistent volume; locally: same dir as this file
_DATA_DIR = os.environ.get("WP_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_DATA_DIR, "wp_manager.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # ---- Auto-migrate old TEXT-id tables to INTEGER-id ----
    _migrate_text_ids_to_int(conn)

    # ---- Add missing columns for existing DBs ----
    _migrate_add_columns(conn)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            db_name TEXT DEFAULT '',
            db_service TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            cf_zone_id TEXT DEFAULT '',
            cf_dns_record_id TEXT DEFAULT '',
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message TEXT DEFAULT '',
            result TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        CREATE TABLE IF NOT EXISTS cloudflare_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            api_token TEXT DEFAULT '',
            api_email TEXT DEFAULT '',
            api_key TEXT DEFAULT '',
            auth_type TEXT DEFAULT 'token',
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Add cf_api_token to global_config defaults
    defaults = {
        "default_admin_name": "admin",
        "default_admin_password": "",
        "default_plugins": "[]",
        "default_themes": "[]",
        "db_service": "mariadb",
        "cf_api_token": "",
        "panel_server_ip": "",
    }
    for key, value in defaults.items():
        cursor.execute(
            "INSERT OR IGNORE INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.utcnow().isoformat()),
        )

    conn.commit()
    conn.close()


def _migrate_add_columns(conn):
    """Add missing columns to existing tables (for upgrades from older versions)."""
    # Add cf_zone_id and cf_dns_record_id to sites
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(sites)").fetchall()]
        if "cf_zone_id" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN cf_zone_id TEXT DEFAULT ''")
        if "cf_dns_record_id" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN cf_dns_record_id TEXT DEFAULT ''")
        if "db_name" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN db_name TEXT DEFAULT ''")
        if "db_service" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN db_service TEXT DEFAULT ''")
    except Exception:
        pass

    # Add cf_api_token and panel_server_ip to global_config
    try:
        for key, value in [("cf_api_token", ""), ("panel_server_ip", "")]:
            exists = conn.execute("SELECT 1 FROM global_config WHERE config_key = ?", (key,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
                    (key, value, datetime.utcnow().isoformat()),
                )
    except Exception:
        pass

    # ---- Cloudflare Accounts migration ----
    # 1) Create table if missing (for existing DBs)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cloudflare_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                api_token TEXT DEFAULT '',
                api_email TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                auth_type TEXT DEFAULT 'token',
                is_default INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
    except Exception:
        pass

    # 2) Add missing columns for older accounts table (e.g. is_default)
    try:
        acct_cols = [row[1] for row in conn.execute("PRAGMA table_info(cloudflare_accounts)").fetchall()]
        if "is_default" not in acct_cols:
            conn.execute("ALTER TABLE cloudflare_accounts ADD COLUMN is_default INTEGER DEFAULT 0")
    except Exception:
        pass

    # 3) Migrate old global_config credentials to cloudflare_accounts
    try:
        old_token = conn.execute(
            "SELECT config_value FROM global_config WHERE config_key = 'cf_api_token'"
        ).fetchone()
        old_email = conn.execute(
            "SELECT config_value FROM global_config WHERE config_key = 'cf_api_email'"
        ).fetchone()
        old_key = conn.execute(
            "SELECT config_value FROM global_config WHERE config_key = 'cf_api_key'"
        ).fetchone()

        has_old_creds = (
            (old_token and old_token["config_value"] and old_token["config_value"].strip())
            or (old_email and old_email["config_value"] and old_email["config_value"].strip())
            or (old_key and old_key["config_value"] and old_key["config_value"].strip())
        )

        if has_old_creds:
            existing = conn.execute("SELECT COUNT(*) as cnt FROM cloudflare_accounts").fetchone()
            if existing["cnt"] == 0:
                auth_type = "token" if (old_token and old_token["config_value"] and old_token["config_value"].strip()) else "global"
                conn.execute(
                    """INSERT INTO cloudflare_accounts
                       (name, api_token, api_email, api_key, auth_type, is_default, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        "默认账号",
                        old_token["config_value"].strip() if old_token and old_token["config_value"] else "",
                        old_email["config_value"].strip() if old_email and old_email["config_value"] else "",
                        old_key["config_value"].strip() if old_key and old_key["config_value"] else "",
                        auth_type,
                        datetime.utcnow().isoformat(),
                        datetime.utcnow().isoformat(),
                    ),
                )
    except Exception:
        pass


def _migrate_text_ids_to_int(conn):
    """Auto-migrate old tables with TEXT ids to INTEGER AUTOINCREMENT.
    
    Detects old schema (id TEXT PRIMARY KEY) and recreates tables with
    INTEGER PRIMARY KEY AUTOINCREMENT. Preserves existing data.
    """
    tables_to_migrate = {
        "sites": """
            CREATE TABLE sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL, url TEXT NOT NULL,
                admin_name TEXT DEFAULT '', admin_password TEXT DEFAULT '',
                tag TEXT DEFAULT '', security_id TEXT DEFAULT '',
                http_username TEXT DEFAULT '', http_password TEXT DEFAULT '',
                verify_certificate INTEGER DEFAULT 1, ssl_version TEXT DEFAULT 'auto',
                panel_website_id INTEGER, panel_app_install_id INTEGER,
                panel_app_detail_id INTEGER, port INTEGER,
                nginx_alias TEXT DEFAULT '', status TEXT DEFAULT 'active',
                created_at TEXT, updated_at TEXT
            )
        """,
        "plugins": """
            CREATE TABLE plugins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, filename TEXT NOT NULL,
                file_path TEXT NOT NULL, file_size INTEGER DEFAULT 0,
                description TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
                created_at TEXT, updated_at TEXT
            )
        """,
        "bg_tasks": """
            CREATE TABLE bg_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                message TEXT DEFAULT '',
                result TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT
            )
        """,
    }

    for table, new_schema in tables_to_migrate.items():
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not row:
                continue
            schema = row["sql"] or ""
            # Check if the table has TEXT PRIMARY KEY (old format)
            if "TEXT PRIMARY KEY" in schema:
                # Get column names
                col_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                cols = [c["name"] for c in col_info]

                # Read existing data
                rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()

                # Drop old table and create new one
                conn.execute(f'DROP TABLE "{table}"')
                conn.execute(new_schema)

                # Re-insert data without the old TEXT id (auto-assign new integer IDs)
                if rows:
                    non_id_cols = [c for c in cols if c != "id"]
                    col_names = ", ".join(non_id_cols)
                    placeholders = ", ".join(["?"] * len(non_id_cols))
                    for row in rows:
                        vals = [row[c] for c in non_id_cols]
                        conn.execute(
                            f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})',
                            vals,
                        )

                # Reset sequence
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
                if rows:
                    conn.execute(
                        "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                        (table, len(rows)),
                    )
                conn.commit()
        except Exception:
            pass  # Non-critical — CREATE TABLE IF NOT EXISTS will handle it


def _reset_sequence_if_empty(conn, table):
    """Reset autoincrement counter to 1 if the table is empty.
    
    This ensures IDs restart from 1 after all rows are deleted.
    If rows remain, the counter stays at max(id)+1.
    """
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if count == 0:
        conn.execute(f"DELETE FROM sqlite_sequence WHERE name = ?", (table,))


def _compact_ids(conn, table):
    """Re-assign sequential IDs so only existing rows have continuous IDs.
    
    After deleting rows, this remaps remaining IDs to 1..N and resets
    the autoincrement counter, so IDs are always compact and continuous.
    """
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY id ASC').fetchall()
    if not rows:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        return {}

    # Build old_id -> new_id mapping
    id_map = {}
    for i, row in enumerate(rows, start=1):
        old_id = row["id"]
        if old_id != i:
            id_map[old_id] = i

    if not id_map:
        return {}  # Already compact

    # Get original table schema to recreate with proper constraints
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    original_schema = schema_row["sql"] if schema_row else None

    # Get column info
    col_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    cols = [c["name"] for c in col_info]
    col_names = ", ".join(cols)

    # Create temporary table with same schema structure
    temp_table = f"_{table}_tmp"
    conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')

    # Recreate with same schema but temp name — handle both quoted and unquoted table names
    if original_schema:
        import re
        temp_schema = re.sub(
            r'CREATE\s+TABLE\s+"?{table}"?'.format(table=re.escape(table)),
            f'CREATE TABLE "{temp_table}"',
            original_schema,
            count=1,
            flags=re.IGNORECASE,
        )
        conn.execute(temp_schema)

    # Insert rows with new IDs
    placeholders = ", ".join(["?"] * len(cols))
    for i, row in enumerate(rows, start=1):
        vals = list(row)
        vals[0] = i  # Replace id with new sequential id
        conn.execute(f'INSERT INTO "{temp_table}" ({col_names}) VALUES ({placeholders})', vals)

    # Swap tables
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{temp_table}" RENAME TO "{table}"')

    # Reset autoincrement
    conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
    conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, len(rows)))

    return id_map


# ---- Site CRUD ----

def create_site(data):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO sites
               (site_name, url, admin_name, admin_password, tag, security_id,
                http_username, http_password, verify_certificate, ssl_version,
                panel_website_id, panel_app_install_id, panel_app_detail_id,
                port, nginx_alias, db_name, db_service,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
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
                data.get("db_name", ""),
                data.get("db_service", ""),
                data.get("status", "active"),
                now,
                now,
            ),
        )
        site_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
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
        rows = conn.execute("SELECT * FROM sites ORDER BY id ASC").fetchall()
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
            "port", "nginx_alias", "db_name", "db_service",
            "status", "cf_zone_id", "cf_dns_record_id",
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
        # Also delete associated bg_tasks
        conn.execute("DELETE FROM bg_tasks WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
        # Compact IDs and update cross-references
        site_id_map = _compact_ids(conn, "sites")
        # Update bg_tasks.site_id with new site IDs
        if site_id_map:
            for old_id, new_id in site_id_map.items():
                conn.execute("UPDATE bg_tasks SET site_id = ? WHERE site_id = ?", (new_id, old_id))
        _compact_ids(conn, "bg_tasks")
        conn.commit()
    finally:
        conn.close()


def update_site_fields(site_id, fields):
    """Update specific fields of a site (lightweight partial update)."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        allowed = {
            "site_name", "url", "admin_name", "admin_password", "tag", "security_id",
            "http_username", "http_password", "verify_certificate", "ssl_version",
            "panel_website_id", "panel_app_install_id", "panel_app_detail_id",
            "port", "nginx_alias", "db_name", "db_service",
            "status", "cf_zone_id", "cf_dns_record_id",
        }
        sets = []
        vals = []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                vals.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(site_id)
        conn.execute(f"UPDATE sites SET {', '.join(sets)} WHERE id = ?", vals)
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
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO plugins (name, filename, file_path, file_size, description, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("name", ""),
                data.get("filename", ""),
                data.get("file_path", ""),
                data.get("file_size", 0),
                data.get("description", ""),
                1 if data.get("enabled", True) else 0,
                now, now,
            ),
        )
        plugin_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
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
        rows = conn.execute("SELECT * FROM plugins ORDER BY id ASC").fetchall()
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
        _compact_ids(conn, "plugins")
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

def create_bg_task(site_id, task_type, status="pending", message="", result=""):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO bg_tasks (site_id, task_type, status, message, result, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (site_id, task_type, status, message, result, now, now),
        )
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return task_id
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


def get_bg_task_by_site(site_id):
    """Get the latest bg_task for a given site_id."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM bg_tasks WHERE site_id = ? ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---- Cloudflare Accounts CRUD ----

def list_cf_accounts(hide_secrets=True):
    """List all Cloudflare accounts. hide_secrets=True masks sensitive fields."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM cloudflare_accounts ORDER BY is_default DESC, id ASC"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if hide_secrets:
                if d.get("api_token"):
                    d["api_token"] = d["api_token"][:8] + "..." if len(d["api_token"]) > 8 else "***"
                if d.get("api_key"):
                    d["api_key"] = "***"
            result.append(d)
        return result
    finally:
        conn.close()


def get_cf_account(account_id):
    """Get a single Cloudflare account by ID (with full credentials)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM cloudflare_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_default_cf_account():
    """Get the default Cloudflare account (with full credentials)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM cloudflare_accounts WHERE is_default = 1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM cloudflare_accounts ORDER BY id ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_cf_account(data):
    """Create a new Cloudflare account."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        # If this is the first account, make it default
        existing = conn.execute("SELECT COUNT(*) as cnt FROM cloudflare_accounts").fetchone()
        is_first = existing["cnt"] == 0

        conn.execute(
            """INSERT INTO cloudflare_accounts
               (name, api_token, api_email, api_key, auth_type, is_default, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("name", ""),
                data.get("api_token", ""),
                data.get("api_email", ""),
                data.get("api_key", ""),
                data.get("auth_type", "token"),
                1 if (data.get("is_default") or is_first) else 0,
                now, now,
            ),
        )

        # If marked as default, unset others
        if data.get("is_default") or is_first:
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "UPDATE cloudflare_accounts SET is_default = 0 WHERE id != ?", (new_id,)
            )

        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return get_cf_account(new_id)
    finally:
        conn.close()


def delete_cf_account(account_id):
    """Delete a Cloudflare account."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM cloudflare_accounts WHERE id = ?", (account_id,))
        conn.commit()
        _compact_ids(conn, "cloudflare_accounts")
        conn.commit()
    finally:
        conn.close()


def set_default_cf_account(account_id):
    """Set a Cloudflare account as the default."""
    conn = get_db()
    try:
        conn.execute("UPDATE cloudflare_accounts SET is_default = 0")
        conn.execute(
            "UPDATE cloudflare_accounts SET is_default = 1 WHERE id = ?", (account_id,)
        )
        conn.commit()
    finally:
        conn.close()
