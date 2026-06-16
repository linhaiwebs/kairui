import json
import logging
import os
import shutil
import re
import sqlite3
from datetime import datetime

from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from services.mc_auto_register import get_profiles_root

logger = logging.getLogger(__name__)

# In Docker: data dir is a persistent volume; locally: same dir as this file
_DATA_DIR = os.environ.get("WP_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_DATA_DIR, "wp_manager.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Enable WAL mode for concurrent read/write across threads/workers
    cursor.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s when DB is locked (prevents "database is locked" on multi-worker startup)
    cursor.execute("PRAGMA busy_timeout = 5000")

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

    # Task sessions + log entries for GMC automation real-time logging
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_sessions (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            site_id INTEGER,
            status TEXT NOT NULL DEFAULT 'running',
            result_json TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_log_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            log_index INTEGER NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            step TEXT DEFAULT ''
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_log_entries_tid ON task_log_entries(task_id, log_index)")

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feed_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            price TEXT DEFAULT '',
            currency TEXT DEFAULT 'USD',
            availability TEXT DEFAULT 'in_stock',
            brand TEXT DEFAULT '',
            gtin TEXT DEFAULT '',
            mpn TEXT DEFAULT '',
            google_product_category TEXT DEFAULT '',
            product_type TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            link TEXT DEFAULT '',
            condition TEXT DEFAULT 'new',
            shipping TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS walmart_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_key TEXT NOT NULL,
            category_label TEXT NOT NULL,
            rank INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            price REAL DEFAULT 0,
            identity_code TEXT DEFAULT '',
            rating_score REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            source_url TEXT DEFAULT '',
            fetched_at TEXT,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS amazon_search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            price TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            thumbnail TEXT DEFAULT '',
            rating_score REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            search_query TEXT DEFAULT '',
            asin TEXT DEFAULT '',
            brand TEXT DEFAULT '',
            breadcrumbs TEXT DEFAULT '',
            features TEXT DEFAULT '',
            original_price TEXT DEFAULT '',
            is_prime INTEGER DEFAULT 0,
            delivery TEXT DEFAULT '',
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generated_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            price TEXT DEFAULT '',
            currency TEXT DEFAULT 'USD',
            brand TEXT DEFAULT '',
            item_id TEXT DEFAULT '',
            ratings TEXT DEFAULT '',
            reviews_count INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            images TEXT DEFAULT '[]',
            features TEXT DEFAULT '[]',
            breadcrumbs TEXT DEFAULT '[]',
            thumbnail TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            category TEXT DEFAULT '',
            extra_data TEXT DEFAULT '{}',
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS woocommerce_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT DEFAULT '',
            regular_price TEXT DEFAULT '',
            sale_price TEXT DEFAULT '',
            description TEXT DEFAULT '',
            short_description TEXT DEFAULT '',
            categories TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            images TEXT DEFAULT '',
            stock_status TEXT DEFAULT 'instock',
            brand TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            extra_data TEXT DEFAULT '{}',
            created_at TEXT
        )
    """)
    try: cursor.execute("ALTER TABLE woocommerce_products ADD COLUMN site_id INTEGER REFERENCES sites(id)")
    except Exception: pass
    try: cursor.execute("ALTER TABLE generated_feed ADD COLUMN site_id INTEGER REFERENCES sites(id)")
    except Exception: pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_woo_products_site ON woocommerce_products(site_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_generated_feed_site ON generated_feed(site_id)")

    # Users table — admin + operator roles
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            created_at TEXT
        )
    """)

    # Insert default admin if users table is empty
    if cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        from config import config
        cursor.execute(
            "INSERT INTO users (username, password, role, created_at) VALUES (?, ?, ?, ?)",
            (config.ADMIN_USERNAME, generate_password_hash(config.ADMIN_PASSWORD), 'admin',
             datetime.utcnow().isoformat()),
        )

    # Migration: add panel_environment_id to users
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN panel_environment_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

    # Fingerprint categories
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fingerprint_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            user_id INTEGER DEFAULT NULL,
            created_at TEXT
        )
    """)

    # 1Panel environments — multi-panel support
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS panel_environments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL DEFAULT '',
            port INTEGER NOT NULL DEFAULT 3500,
            api_key TEXT NOT NULL DEFAULT '',
            is_default INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # Migration: add cf_account_id to panel_environments
    try:
        cursor.execute("ALTER TABLE panel_environments ADD COLUMN cf_account_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

    # Profile-Category mapping (CloakBrowser profiles ← categories)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profile_category_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name TEXT NOT NULL UNIQUE,
            category_id INTEGER DEFAULT NULL,
            FOREIGN KEY (category_id) REFERENCES fingerprint_categories(id)
        )
    """)

    # Static site products — product data for static e-commerce sites (replaces WooCommerce)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS static_site_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            price REAL DEFAULT 0.0,
            sale_price REAL DEFAULT NULL,
            currency TEXT DEFAULT 'USD',
            image_url TEXT DEFAULT '',
            additional_images TEXT DEFAULT '[]',
            category TEXT DEFAULT '',
            brand TEXT DEFAULT '',
            sku TEXT DEFAULT '',
            mpn TEXT DEFAULT '',
            gtin TEXT DEFAULT '',
            availability TEXT DEFAULT 'in_stock',
            condition TEXT DEFAULT 'new',
            shipping_weight TEXT DEFAULT '',
            shipping_weight_unit TEXT DEFAULT 'kg',
            product_url TEXT DEFAULT '',
            variant_data TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_static_site_products_sid ON static_site_products(site_id)")

    # Brand kits — AI-generated brand kits with logo + assets
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brand_kits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            raw_svg TEXT DEFAULT '',
            processed_svg TEXT DEFAULT '',
            colors TEXT DEFAULT '[]',
            typography TEXT DEFAULT '{}',
            directory TEXT DEFAULT '',
            png_256 TEXT DEFAULT '',
            png_512 TEXT DEFAULT '',
            png_1024 TEXT DEFAULT '',
            ico TEXT DEFAULT '',
            webp TEXT DEFAULT '',
            og_image TEXT DEFAULT '',
            brand_md TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            error_message TEXT DEFAULT '',
            woo_config TEXT DEFAULT '{}',
            footer_config TEXT DEFAULT '{}',
            business_info TEXT DEFAULT '{}',
            tax_config TEXT DEFAULT '{}',
            shipping_config TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # Migration: add woo_config and footer_config if missing
    for col in ("woo_config", "footer_config", "business_info", "tax_config", "shipping_config"):
        try:
            cursor.execute(f"ALTER TABLE brand_kits ADD COLUMN {col} TEXT DEFAULT '{{}}'")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Ensure brand-kits directory exists
    _BRAND_KIT_DIR = os.path.join(os.path.dirname(__file__), "brand-kits")
    os.makedirs(_BRAND_KIT_DIR, exist_ok=True)

    # Add cf_api_token to global_config defaults
    defaults = {
        "default_admin_name": "admin",
        "default_admin_password": "",
        "default_plugins": "[]",
        "default_themes": "[]",
        "db_service": "mariadb",
        "cf_api_token": "",
        "panel_server_ip": "",
        "wpcom_token": "",
        "wpcom_connected": "false",
        "wpcom_email": "",
        "deepseek_api_key": "",
        "crawlbase_api_key": "",
        "google_default_country": "US",
        "google_default_timezone": "America/Chicago",
        "fingerprint_enabled": "false",
    }
    # Migration: add google feed/verification columns to sites
    for col, col_type in [
        ("google_feed_url", "TEXT DEFAULT ''"),
        ("google_verification_method", "TEXT DEFAULT ''"),
        ("google_verification_done", "INTEGER DEFAULT 0"),
        ("google_mc_account_id", "TEXT DEFAULT ''"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE sites ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

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
        if "created_by" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN created_by INTEGER DEFAULT NULL")
        if "cloakbrowser_profile_name" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN cloakbrowser_profile_name TEXT DEFAULT NULL")
        if "demo_imported" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN demo_imported INTEGER DEFAULT 0")
        if "demo_name" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN demo_name TEXT DEFAULT ''")
        if "brand_configured" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN brand_configured INTEGER DEFAULT 0")
        if "site_type" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN site_type TEXT DEFAULT 'wordpress'")
        if "static_dir" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN static_dir TEXT DEFAULT ''")
        if "brand_kit_id" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN brand_kit_id INTEGER DEFAULT NULL")
        if "stitch_design_status" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN stitch_design_status TEXT DEFAULT ''")
        if "panel_environment_id" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN panel_environment_id INTEGER DEFAULT NULL")
    except Exception:
        pass

    # Add enriched columns to amazon_search_results
    try:
        asr_cols = [row[1] for row in conn.execute("PRAGMA table_info(amazon_search_results)").fetchall()]
        if "asin" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN asin TEXT DEFAULT ''")
        if "brand" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN brand TEXT DEFAULT ''")
        if "breadcrumbs" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN breadcrumbs TEXT DEFAULT ''")
        if "features" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN features TEXT DEFAULT ''")
        if "original_price" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN original_price TEXT DEFAULT ''")
        if "is_prime" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN is_prime INTEGER DEFAULT 0")
        if "delivery" not in asr_cols:
            conn.execute("ALTER TABLE amazon_search_results ADD COLUMN delivery TEXT DEFAULT ''")
    except Exception:
        pass

    # Add created_by + cloakbrowser_profile_name + proxy to brand_kits
    try:
        bk_cols = [row[1] for row in conn.execute("PRAGMA table_info(brand_kits)").fetchall()]
        if "created_by" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN created_by INTEGER DEFAULT NULL")
        if "cloakbrowser_profile_name" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN cloakbrowser_profile_name TEXT DEFAULT NULL")
        if "proxy" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN proxy TEXT DEFAULT ''")
        if "proxy_id" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN proxy_id INTEGER DEFAULT NULL")
        if "google_account_id" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN google_account_id INTEGER DEFAULT NULL")
        if "html_site" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN html_site TEXT DEFAULT '{}'")
        if "static_style" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN static_style TEXT DEFAULT '{}'")
        if "design_system" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN design_system TEXT DEFAULT '{}'")
        if "design_project_id" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN design_project_id TEXT DEFAULT ''")
        if "design_screens" not in bk_cols:
            conn.execute("ALTER TABLE brand_kits ADD COLUMN design_screens TEXT DEFAULT '{}'")
    except Exception:
        pass

    # Add occupied_kit_id / occupied_kit_name to google_accounts (for DBs created before these columns existed)
    try:
        ga_cols = [row[1] for row in conn.execute("PRAGMA table_info(google_accounts)").fetchall()]
        had_id = "occupied_kit_id" in ga_cols
        had_name = "occupied_kit_name" in ga_cols
        logger.info("google_accounts columns: occupied_kit_id=%s, occupied_kit_name=%s", had_id, had_name)
        if not had_id:
            conn.execute("ALTER TABLE google_accounts ADD COLUMN occupied_kit_id INTEGER DEFAULT NULL")
            logger.info("google_accounts: added occupied_kit_id")
        if not had_name:
            conn.execute("ALTER TABLE google_accounts ADD COLUMN occupied_kit_name TEXT DEFAULT NULL")
            logger.info("google_accounts: added occupied_kit_name")
        # Backfill: google_accounts that are referenced by a brand_kit but have NULL occupancy
        # (fixes data written before the WAL checkpoint fix was applied)
        import time as _time
        for _retry in range(3):
            try:
                cur = conn.execute(
                    "UPDATE google_accounts SET "
                    "occupied_kit_id = (SELECT bk.id FROM brand_kits bk WHERE bk.google_account_id = google_accounts.id LIMIT 1), "
                    "occupied_kit_name = (SELECT bk.name FROM brand_kits bk WHERE bk.google_account_id = google_accounts.id LIMIT 1) "
                    "WHERE occupied_kit_id IS NULL "
                    "AND EXISTS (SELECT 1 FROM brand_kits bk WHERE bk.google_account_id = google_accounts.id)"
                )
                if cur.rowcount > 0:
                    logger.info("google_accounts: backfilled %s account(s) from brand_kits", cur.rowcount)
                else:
                    logger.info("google_accounts: no accounts needed backfill")
                break
            except Exception:
                if _retry < 2:
                    _time.sleep(0.5)
                else:
                    raise
    except Exception as e:
        logger.error("google_accounts migration: %s", e)

    # Cleanup orphaned occupancy — google_accounts/proxies pointing to non-existent brand_kits
    try:
        # Google accounts: release occupancy where brand kit no longer exists
        orphan_ga = conn.execute(
            "UPDATE google_accounts SET occupied_kit_id = NULL, occupied_kit_name = NULL "
            "WHERE occupied_kit_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM brand_kits WHERE id = google_accounts.occupied_kit_id)"
        )
        if orphan_ga.rowcount > 0:
            logger.info("google_accounts: released %d orphaned occupancy", orphan_ga.rowcount)

        # Proxies: release occupancy where brand kit no longer exists
        orphan_px = conn.execute(
            "UPDATE proxies SET occupied_kit_id = NULL, occupied_kit_name = NULL "
            "WHERE occupied_kit_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM brand_kits WHERE id = proxies.occupied_kit_id)"
        )
        if orphan_px.rowcount > 0:
            logger.info("proxies: released %d orphaned occupancy", orphan_px.rowcount)

        conn.commit()
    except Exception as e:
        logger.error("orphaned occupancy cleanup: %s", e)

    # Create proxies table (IP pool from decodo)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_url TEXT NOT NULL,
                ip TEXT DEFAULT '',
                port INTEGER DEFAULT NULL,
                occupied_kit_id INTEGER DEFAULT NULL,
                occupied_kit_name TEXT DEFAULT NULL
            )
        """)
    except Exception:
        pass

    # Create google_accounts table (Google account pool for GMC automation with TOTP 2FA)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS google_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                recovery_email TEXT DEFAULT '',
                totp_secret TEXT DEFAULT '',
                registration_year TEXT DEFAULT '',
                country TEXT DEFAULT '',
                occupied_kit_id INTEGER DEFAULT NULL,
                occupied_kit_name TEXT DEFAULT NULL,
                created_at TEXT,
                updated_at TEXT
            )
        """)
    except Exception:
        pass

    # Auto-seed proxies if empty (uses decodo config from global_config)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0]
        if cnt == 0:
            _seed_proxies_from_config(conn)
            conn.commit()
    except Exception:
        pass

    # Migrate socks5h:// to socks5:// (Chromium doesn't support socks5h scheme)
    try:
        conn.execute(
            "UPDATE proxies SET proxy_url = REPLACE(proxy_url, 'socks5h://', 'socks5://') "
            "WHERE proxy_url LIKE 'socks5h://%'"
        )
        conn.commit()
    except Exception:
        pass

    # Add proxy_type column to proxies table (distinguish socks5/decodo from http/okkproxy)
    try:
        proxy_cols = [row[1] for row in conn.execute("PRAGMA table_info(proxies)").fetchall()]
        if "proxy_type" not in proxy_cols:
            conn.execute("ALTER TABLE proxies ADD COLUMN proxy_type TEXT DEFAULT 'socks5'")
    except Exception:
        pass

    # Create profile_category_mapping table if missing
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_category_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL UNIQUE,
                category_id INTEGER DEFAULT NULL,
                FOREIGN KEY (category_id) REFERENCES fingerprint_categories(id)
            )
        """)
    except Exception:
        pass

    # Create fingerprint_categories table if missing
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fingerprint_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER DEFAULT NULL,
                created_at TEXT
            )
        """)
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

    # Add decodo proxy config to global_config
    try:
        decodo_defaults = [
            ("decodo_username", "spx9vttaji"),
            ("decodo_password", "n10cCtesxLhHi41~nU"),
            ("decodo_host", "dc.decodo.com"),
            ("decodo_port_start", "10001"),
            ("decodo_port_end", "10100"),
        ]
        for key, value in decodo_defaults:
            exists = conn.execute("SELECT 1 FROM global_config WHERE config_key = ?", (key,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
                    (key, value, datetime.utcnow().isoformat()),
                )
    except Exception:
        pass

    # Add proxy_provider + okkproxy config to global_config
    try:
        for key, value in [
            ("proxy_provider", "decodo"),
            ("okkproxy_api_url", "https://start.okkproxy.com/ip/balance/getProxyConfig/1781150797123"),
            ("okkproxy_raw_list", ""),
        ]:
            exists = conn.execute("SELECT 1 FROM global_config WHERE config_key = ?", (key,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?)",
                    (key, value, datetime.utcnow().isoformat()),
                )
    except Exception:
        pass

    # ---- Feed Products migration ----
    try:
        feed_cols = [row[1] for row in conn.execute("PRAGMA table_info(feed_products)").fetchall()]
        for col, defn in [("brand", "TEXT DEFAULT ''"), ("shipping", "TEXT DEFAULT ''")]:
            if col not in feed_cols:
                conn.execute(f"ALTER TABLE feed_products ADD COLUMN {col} {defn}")
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

    # ---- Proxy URL format migration: strip user- prefix and -ip- suffix ----
    # Old: socks5://user-spx9vttaji-ip-1.2.3.4:pass@host:port
    # New: socks5://spx9vttaji:pass@host:port
    try:
        import re as _re
        _bad_pattern = _re.compile(r'^socks5://user-([^:]+)-ip-\d+\.\d+\.\d+\.\d+:(.+)@(.+:\d+)$')
        # 1) proxies table
        rows = conn.execute("SELECT id, proxy_url FROM proxies").fetchall()
        for r in rows:
            m = _bad_pattern.match(r["proxy_url"] or "")
            if m:
                new_url = f"socks5://{m.group(1)}:{m.group(2)}@{m.group(3)}"
                conn.execute("UPDATE proxies SET proxy_url = ? WHERE id = ?", (new_url, r["id"]))
                logger.info("Fixed proxy URL: %s -> %s", r["proxy_url"], new_url)
        # 2) brand_kits table
        rows = conn.execute("SELECT id, proxy FROM brand_kits WHERE proxy != ''").fetchall()
        for r in rows:
            m = _bad_pattern.match(r["proxy"] or "")
            if m:
                new_url = f"socks5://{m.group(1)}:{m.group(2)}@{m.group(3)}"
                conn.execute("UPDATE brand_kits SET proxy = ? WHERE id = ?", (new_url, r["id"]))
                logger.info("Fixed brand_kit proxy: %s -> %s", r["proxy"], new_url)
        # 3) sites table: google_feed_url column is not a proxy, skip
        # 4) profile config.json files on disk
        try:
            import json as _json, glob as _glob
            data_dir = os.environ.get("WP_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
            profiles_root = os.path.join(data_dir, "profiles")
            if os.path.isdir(profiles_root):
                for profile_dir in os.listdir(profiles_root):
                    cfg_path = os.path.join(profiles_root, profile_dir, "config.json")
                    if not os.path.isfile(cfg_path):
                        continue
                    try:
                        with open(cfg_path, "r") as fh:
                            cfg = _json.load(fh)
                        proxy_val = cfg.get("proxy", "")
                        m = _bad_pattern.match(proxy_val or "")
                        if m:
                            new_url = f"socks5://{m.group(1)}:{m.group(2)}@{m.group(3)}"
                            cfg["proxy"] = new_url
                            with open(cfg_path, "w") as fh:
                                _json.dump(cfg, fh, indent=2)
                            logger.info("Fixed profile proxy: %s (%s)", profile_dir, new_url)
                    except Exception:
                        pass
        except Exception:
            pass
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
                status, created_by, cloakbrowser_profile_name,
                site_type, static_dir, brand_kit_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                data.get("created_by"),
                data.get("cloakbrowser_profile_name"),
                data.get("site_type", "static"),
                data.get("static_dir", ""),
                data.get("brand_kit_id"),
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


def list_sites(user_id=None):
    conn = get_db()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM sites WHERE created_by = ? ORDER BY id ASC", (user_id,)
            ).fetchall()
        else:
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
            "google_feed_url", "google_verification_method", "google_verification_done",
            "google_mc_account_id", "cloakbrowser_profile_name",
            "demo_imported", "demo_name", "brand_configured",
            "site_type", "static_dir", "brand_kit_id",
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
        # Release brand kit resources if site has one
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if site:
            # Release Google account from brand kit
            bk = conn.execute(
                "SELECT * FROM brand_kits WHERE id = ?", (site["brand_kit_id"],)
            ).fetchone() if site["brand_kit_id"] else None
            if bk and bk["google_account_id"]:
                conn.execute(
                    "UPDATE google_accounts SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (bk["google_account_id"],),
                )
            if bk and bk["proxy_id"]:
                conn.execute(
                    "UPDATE proxies SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (bk["proxy_id"],),
                )

        # Delete associated records
        conn.execute("DELETE FROM static_site_products WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM bg_tasks WHERE site_id = ?", (site_id,))
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
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
            "google_feed_url", "google_verification_method", "google_verification_done",
            "google_mc_account_id", "cloakbrowser_profile_name",
            "stitch_design_status", "site_type", "static_dir", "brand_kit_id",
            "panel_environment_id",
            "demo_imported", "demo_name", "brand_configured",
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


# ---- User CRUD ----

def get_user_by_username(username):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT u.id, u.username, u.role, u.created_at, u.panel_environment_id, "
            "p.name as panel_env_name FROM users u "
            "LEFT JOIN panel_environments p ON u.panel_environment_id = p.id "
            "ORDER BY u.id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(username, password, role='operator', panel_environment_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO users (username, password, role, panel_environment_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, panel_environment_id, now),
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return get_user_by_id(user_id)
    finally:
        conn.close()


def update_user(user_id, username=None, password=None, role=None, panel_environment_id=None):
    conn = get_db()
    try:
        sets = []
        vals = []
        if username is not None:
            sets.append("username = ?")
            vals.append(username)
        if password is not None:
            sets.append("password = ?")
            vals.append(generate_password_hash(password))
        if role is not None:
            sets.append("role = ?")
            vals.append(role)
        if panel_environment_id is not None:
            sets.append("panel_environment_id = ?")
            vals.append(panel_environment_id)
        if not sets:
            return get_user_by_id(user_id)
        vals.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        return get_user_by_id(user_id)
    finally:
        conn.close()


def delete_user(user_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        _compact_ids(conn, "users")
        conn.commit()
    finally:
        conn.close()


# ---- Fingerprint Category CRUD ----

# ---- Panel Environment CRUD ----

def get_all_panel_environments():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM panel_environments ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_panel_environment(env_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM panel_environments WHERE id = ?", (env_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_panel_environment(data):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO panel_environments (name, host, port, api_key, is_default, cf_account_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data.get("name", ""), data.get("host", ""), data.get("port", 3500),
             data.get("api_key", ""), data.get("is_default", 0), data.get("cf_account_id"), now),
        )
        env_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return get_panel_environment(env_id)
    finally:
        conn.close()


def update_panel_environment(env_id, data):
    conn = get_db()
    try:
        sets = []
        vals = []
        for key in ("name", "host", "port", "api_key", "cf_account_id"):
            if key in data:
                sets.append(f"{key} = ?")
                vals.append(data[key])
        if "is_default" in data:
            sets.append("is_default = ?")
            vals.append(data["is_default"])
        if not sets:
            return get_panel_environment(env_id)
        vals.append(env_id)
        conn.execute(f"UPDATE panel_environments SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        return get_panel_environment(env_id)
    finally:
        conn.close()


def set_default_panel_environment(env_id):
    conn = get_db()
    try:
        conn.execute("UPDATE panel_environments SET is_default = 0")
        conn.execute("UPDATE panel_environments SET is_default = 1 WHERE id = ?", (env_id,))
        conn.commit()
    finally:
        conn.close()


def delete_panel_environment(env_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM panel_environments WHERE id = ?", (env_id,))
        conn.commit()
        # Clear references from users
        conn.execute("UPDATE users SET panel_environment_id = NULL WHERE panel_environment_id = ?", (env_id,))
        conn.commit()
        _compact_ids(conn, "panel_environments")
        conn.commit()
    finally:
        conn.close()


def get_user_panel_environment(user_id):
    """Return the panel environment for a given user, or the default env, or None."""
    conn = get_db()
    try:
        user = conn.execute("SELECT panel_environment_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user["panel_environment_id"]:
            env = conn.execute("SELECT * FROM panel_environments WHERE id = ?", (user["panel_environment_id"],)).fetchone()
            if env:
                return dict(env)
        # Fallback to default
        env = conn.execute("SELECT * FROM panel_environments WHERE is_default = 1 LIMIT 1").fetchone()
        if env:
            return dict(env)
        # Fallback to any
        env = conn.execute("SELECT * FROM panel_environments LIMIT 1").fetchone()
        return dict(env) if env else None
    finally:
        conn.close()


def get_environment_by_cf_account(cf_account_id):
    """Return the panel environment bound to a specific CF account, or None."""
    if not cf_account_id:
        return None
    conn = get_db()
    try:
        env = conn.execute(
            "SELECT * FROM panel_environments WHERE cf_account_id = ? LIMIT 1",
            (int(cf_account_id),),
        ).fetchone()
        return dict(env) if env else None
    finally:
        conn.close()


def create_fingerprint_category(name, user_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO fingerprint_categories (name, user_id, created_at) VALUES (?, ?, ?)",
            (name, user_id, now),
        )
        cat_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return {"id": cat_id, "name": name, "user_id": user_id, "created_at": now}
    finally:
        conn.close()


def get_all_fingerprint_categories():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT fc.*, u.username as owner_username FROM fingerprint_categories fc "
            "LEFT JOIN users u ON fc.user_id = u.id "
            "ORDER BY fc.id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_fingerprint_category(cat_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE profile_category_mapping SET category_id = NULL WHERE category_id = ?",
            (cat_id,),
        )
        conn.execute("DELETE FROM fingerprint_categories WHERE id = ?", (cat_id,))
        conn.commit()
        _compact_ids(conn, "fingerprint_categories")
        conn.commit()
    finally:
        conn.close()


# ---- Profile ↔ Category Mapping (CloakBrowser profiles) ----

def _get_profile_info(profile_name: str) -> dict | None:
    """Read a single CloakBrowser profile's config.json, returning minimal info."""
    try:
        pdir = os.path.join(get_profiles_root(), profile_name)
        cfg_path = os.path.join(pdir, "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return {
                "name": profile_name,
                "platform": config.get("platform", ""),
                "country": config.get("country", ""),
                "gpu": config.get("gpu_renderer", ""),
                "screen": f"{config.get('screen_width', '?')}x{config.get('screen_height', '?')}",
                "timezone": config.get("timezone", ""),
                "google_email": config.get("google_email", ""),
                "proxy": config.get("proxy", ""),
            }
    except Exception:
        pass
    return None


def set_profile_category(profile_name: str, category_id):
    """Assign a category to a CloakBrowser profile."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO profile_category_mapping (profile_name, category_id) VALUES (?, ?) "
            "ON CONFLICT(profile_name) DO UPDATE SET category_id = ?",
            (profile_name, category_id, category_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile_category(profile_name: str) -> dict | None:
    """Get the category assigned to a profile."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT pcm.*, fc.name as category_name "
            "FROM profile_category_mapping pcm "
            "LEFT JOIN fingerprint_categories fc ON pcm.category_id = fc.id "
            "WHERE pcm.profile_name = ?",
            (profile_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_profile_categories() -> list:
    """List all profiles with their category assignments."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT pcm.*, fc.name as category_name "
            "FROM profile_category_mapping pcm "
            "LEFT JOIN fingerprint_categories fc ON pcm.category_id = fc.id "
            "ORDER BY pcm.id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_available_cloakbrowser_profile() -> dict | None:
    """Get the first CloakBrowser profile not assigned to any brand kit."""
    root = get_profiles_root()
    if not os.path.isdir(root):
        return None
    conn = get_db()
    try:
        assigned = set(
            r[0] for r in conn.execute(
                "SELECT cloakbrowser_profile_name FROM brand_kits WHERE cloakbrowser_profile_name IS NOT NULL"
            ).fetchall()
        )
    finally:
        conn.close()
    for name in sorted(os.listdir(root)):
        pdir = os.path.join(root, name)
        if not os.path.isdir(pdir):
            continue
        if not os.path.isfile(os.path.join(pdir, "config.json")):
            continue
        if name not in assigned:
            return _get_profile_info(name)
    return None


def assign_cloakbrowser_profile_to_brand_kit(profile_name: str, kit_id: int):
    """Link a CloakBrowser profile to a brand kit."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE brand_kits SET cloakbrowser_profile_name = ? WHERE id = ?",
            (profile_name, kit_id),
        )
        conn.commit()
    finally:
        conn.close()


def release_cloakbrowser_profile_from_brand_kit(profile_name: str):
    """Unlink a CloakBrowser profile from its brand kit."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE brand_kits SET cloakbrowser_profile_name = NULL WHERE cloakbrowser_profile_name = ?",
            (profile_name,),
        )
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


def get_enabled_themes():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM themes WHERE enabled = 1").fetchall()
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


# ---- Feed Stats Dashboard ----

def get_feed_stats():
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM feed_products").fetchone()[0]
        sites_count = conn.execute(
            "SELECT COUNT(DISTINCT site_id) FROM feed_products"
        ).fetchone()[0]
        in_stock = conn.execute(
            "SELECT COUNT(*) FROM feed_products WHERE availability = 'in_stock'"
        ).fetchone()[0]
        out_of_stock = conn.execute(
            "SELECT COUNT(*) FROM feed_products WHERE availability = 'out_of_stock'"
        ).fetchone()[0]
        preorder = conn.execute(
            "SELECT COUNT(*) FROM feed_products WHERE availability = 'preorder'"
        ).fetchone()[0]
        currencies = conn.execute(
            "SELECT currency, COUNT(*) as cnt FROM feed_products GROUP BY currency ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total_products": total,
            "sites_with_products": sites_count,
            "in_stock": in_stock,
            "out_of_stock": out_of_stock,
            "preorder": preorder,
            "currencies": [{"currency": r["currency"], "count": r["cnt"]} for r in currencies],
        }
    finally:
        conn.close()


# ---- Generated Feed Products ----

def save_generated_feed_product(data: dict) -> int:
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO generated_feed
               (title, price, currency, brand, item_id, ratings, reviews_count,
                description, images, features, breadcrumbs, thumbnail,
                source_url, category, extra_data, site_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("title", ""),
                data.get("price", ""),
                data.get("currency", "USD"),
                data.get("brand", ""),
                data.get("item_id", ""),
                data.get("ratings", ""),
                data.get("reviews_count", 0),
                data.get("description", ""),
                json.dumps(data.get("images", []), ensure_ascii=False),
                json.dumps(data.get("features", []), ensure_ascii=False),
                json.dumps(data.get("breadcrumbs", []), ensure_ascii=False),
                data.get("thumbnail", ""),
                data.get("source_url", ""),
                data.get("category", ""),
                json.dumps(data.get("extra_data", {}), ensure_ascii=False),
                data.get("site_id"),
                now,
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def list_generated_feed(site_id=None) -> list[dict]:
    conn = get_db()
    try:
        if site_id:
            rows = conn.execute(
                "SELECT * FROM generated_feed WHERE site_id = ? ORDER BY id DESC", (site_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM generated_feed ORDER BY id DESC"
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            for field in ("images", "features", "breadcrumbs"):
                try:
                    d[field] = json.loads(d.get(field, "[]"))
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            try:
                d["extra_data"] = json.loads(d.get("extra_data", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["extra_data"] = {}
            results.append(d)
        return results
    finally:
        conn.close()


def delete_generated_feed_items(ids: list[int]) -> int:
    """Delete specific generated feed products by IDs."""
    if not ids:
        return 0
    conn = get_db()
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM generated_feed WHERE id IN ({placeholders})", ids
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()


def clear_generated_feed(site_id=None) -> int:
    conn = get_db()
    try:
        if site_id:
            conn.execute("DELETE FROM generated_feed WHERE site_id = ?", (site_id,))
        else:
            conn.execute("DELETE FROM generated_feed")
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


# ---- WooCommerce Products ----

def save_woocommerce_product(data: dict) -> int:
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO woocommerce_products
               (name, sku, regular_price, sale_price, description, short_description,
                categories, tags, images, stock_status, brand, source_url, extra_data, site_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("name", ""),
                data.get("sku", ""),
                data.get("regular_price", ""),
                data.get("sale_price", ""),
                data.get("description", ""),
                data.get("short_description", ""),
                data.get("categories", ""),
                data.get("tags", ""),
                data.get("images", ""),
                data.get("stock_status", "instock"),
                data.get("brand", ""),
                data.get("source_url", ""),
                json.dumps(data.get("extra_data", {}), ensure_ascii=False),
                data.get("site_id"),
                now,
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def list_woocommerce_products(site_id=None) -> list[dict]:
    conn = get_db()
    try:
        if site_id:
            rows = conn.execute(
                "SELECT * FROM woocommerce_products WHERE site_id = ? ORDER BY id DESC", (site_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM woocommerce_products ORDER BY id DESC"
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["extra_data"] = json.loads(d.get("extra_data", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["extra_data"] = {}
            results.append(d)
        return results
    finally:
        conn.close()


def delete_woocommerce_products(ids: list[int]) -> int:
    if not ids:
        return 0
    conn = get_db()
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM woocommerce_products WHERE id IN ({placeholders})", ids
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()


def clear_woocommerce_products(site_id=None) -> int:
    conn = get_db()
    try:
        if site_id:
            conn.execute("DELETE FROM woocommerce_products WHERE site_id = ?", (site_id,))
        else:
            conn.execute("DELETE FROM woocommerce_products")
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


# ---- Walmart Products ----

def save_walmart_products(category_key: str, category_label: str, products: list[dict]) -> int:
    """Replace all products for *category_key* with *products* (list of normalised dicts)."""
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM walmart_products WHERE category_key = ?", (category_key,))
        count = 0
        for p in products:
            conn.execute(
                """INSERT INTO walmart_products
                   (category_key, category_label, rank, product_name, price,
                    identity_code, rating_score, review_count, source_url,
                    fetched_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    category_key,
                    category_label,
                    p.get("rank", 0),
                    p.get("product_name", ""),
                    p.get("price", 0),
                    p.get("identity_code", ""),
                    p.get("rating_score", 0),
                    p.get("review_count", 0),
                    p.get("source_url", ""),
                    now,
                    now,
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def load_walmart_products(category_key: str = "") -> list[dict]:
    """Load persisted products, optionally filtered by *category_key*."""
    conn = get_db()
    try:
        if category_key:
            rows = conn.execute(
                "SELECT * FROM walmart_products WHERE category_key = ? ORDER BY rank ASC",
                (category_key,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM walmart_products ORDER BY category_key, rank ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_walmart_categories_from_db() -> list[dict]:
    """Return distinct categories that have persisted products."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category_key, category_label, "
            "MAX(fetched_at) as fetched_at, COUNT(*) as product_count "
            "FROM walmart_products GROUP BY category_key ORDER BY category_key"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- Amazon Search Results (爆品导入) ----

def save_amazon_search_results(products: list[dict]) -> int:
    """Batch save Amazon search results."""
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        for p in products:
            conn.execute(
                """INSERT INTO amazon_search_results
                   (product_name, price, source_url, thumbnail, rating_score,
                    review_count, search_query, asin, brand, breadcrumbs,
                    features, original_price, is_prime, delivery, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p.get("product_name", ""),
                    p.get("price", ""),
                    p.get("source_url", ""),
                    p.get("thumbnail", ""),
                    p.get("rating_score", 0),
                    p.get("review_count", 0),
                    p.get("search_query", ""),
                    p.get("asin", ""),
                    p.get("brand", ""),
                    p.get("breadcrumbs", ""),
                    p.get("features", ""),
                    p.get("original_price", ""),
                    1 if p.get("is_prime") else 0,
                    p.get("delivery", ""),
                    now,
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def load_amazon_search_results() -> list[dict]:
    """Load all persisted Amazon search results."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM amazon_search_results ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_amazon_search_results(ids: list[int]) -> int:
    """Delete Amazon search results by IDs. Returns deleted count."""
    if not ids:
        return 0
    conn = get_db()
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"DELETE FROM amazon_search_results WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()


def clear_amazon_search_results() -> int:
    """Clear all Amazon search results."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM amazon_search_results")
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()


# ---- Feed Products (Google Merchant Center) ----

def list_feed_products(site_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM feed_products WHERE site_id = ? ORDER BY id ASC", (site_id,)
        ).fetchall()
        results = [dict(r) for r in rows]

        # Fallback: also return generated_feed products (from WooCommerce feed generation)
        # when the site has no explicit feed_products yet
        if not results:
            gen_rows = conn.execute(
                "SELECT *, ? as site_id FROM generated_feed ORDER BY id DESC", (site_id,)
            ).fetchall()
            for r in gen_rows:
                d = dict(r)
                results.append(d)
        return results
    finally:
        conn.close()


def get_feed_product(product_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM feed_products WHERE id = ?", (product_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_feed_product(data):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO feed_products
               (site_id, title, description, price, currency, availability,
                brand, gtin, mpn, google_product_category, product_type,
                image_url, link, condition, shipping,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["site_id"],
                data.get("title", ""),
                data.get("description", ""),
                data.get("price", ""),
                data.get("currency", "USD"),
                data.get("availability", "in_stock"),
                data.get("brand", ""),
                data.get("gtin", ""),
                data.get("mpn", ""),
                data.get("google_product_category", ""),
                data.get("product_type", ""),
                data.get("image_url", ""),
                data.get("link", ""),
                data.get("condition", "new"),
                data.get("shipping", ""),
                now, now,
            ),
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return get_feed_product(pid)
    finally:
        conn.close()


def update_feed_product(product_id, data):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        row = conn.execute("SELECT * FROM feed_products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            """UPDATE feed_products SET
               title=?, description=?, price=?, currency=?, availability=?,
               brand=?, gtin=?, mpn=?, google_product_category=?, product_type=?,
               image_url=?, link=?, condition=?, shipping=?,
               updated_at=?
               WHERE id=?""",
            (
                data.get("title", row["title"]),
                data.get("description", row["description"]),
                data.get("price", row["price"]),
                data.get("currency", row["currency"]),
                data.get("availability", row["availability"]),
                data.get("brand", row["brand"]),
                data.get("gtin", row["gtin"]),
                data.get("mpn", row["mpn"]),
                data.get("google_product_category", row["google_product_category"]),
                data.get("product_type", row["product_type"]),
                data.get("image_url", row["image_url"]),
                data.get("link", row["link"]),
                data.get("condition", row["condition"]),
                data.get("shipping", row["shipping"]),
                now,
                product_id,
            ),
        )
        conn.commit()
        return get_feed_product(product_id)
    finally:
        conn.close()


def delete_feed_product(product_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM feed_products WHERE id = ?", (product_id,))
        conn.commit()
        _compact_ids(conn, "feed_products")
        conn.commit()
    finally:
        conn.close()


SAMPLE_FEED_PRODUCTS = [
    {
        "title": "经典纯棉男士T恤 - 夏季新款",
        "description": "100%纯棉面料，透气舒适。圆领设计，多色可选。适合日常休闲穿着。",
        "price": "29.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "FashionPlus",
        "gtin": "06123456789012",
        "mpn": "FP-MT-001-BLK",
        "google_product_category": "Apparel & Accessories > Clothing > Shirts & Tops > T-Shirts",
        "product_type": "Men's T-Shirt",
        "image_url": "https://example.com/images/mens-tshirt-black.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:0.00 USD",
    },
    {
        "title": "无线蓝牙耳机 Pro - 主动降噪",
        "description": "蓝牙5.3芯片，40小时续航，主动降噪，IPX5防水。适用于运动、通勤。",
        "price": "79.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "SoundWave",
        "gtin": "06123456789029",
        "mpn": "SW-BT-PRO-BLK",
        "google_product_category": "Electronics > Audio > Headphones > Bluetooth Headphones",
        "product_type": "Wireless Earbuds",
        "image_url": "https://example.com/images/wireless-earbuds-pro.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:5.99 USD",
    },
    {
        "title": "有机绿茶 高山云雾 100g",
        "description": "高山海拔1200米种植，手工采摘，有机认证。清新回甘。",
        "price": "15.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "NatureLeaf",
        "gtin": "06123456789036",
        "mpn": "NL-GT-100G",
        "google_product_category": "Food, Beverages & Tobacco > Beverages > Tea & Infusions > Green Tea",
        "product_type": "Organic Green Tea",
        "image_url": "https://example.com/images/organic-green-tea.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:3.99 USD",
    },
    {
        "title": "瑜伽垫 加厚防滑 NBR材质 6mm",
        "description": "环保NBR材质，双面防滑纹理，附带绑带和背包。适用于瑜伽、普拉提、健身。",
        "price": "24.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "FlexFit",
        "gtin": "06123456789043",
        "mpn": "FF-YM-6MM-PUR",
        "google_product_category": "Sporting Goods > Exercise & Fitness > Yoga & Pilates > Yoga Mats",
        "product_type": "Yoga Mat",
        "image_url": "https://example.com/images/yoga-mat-purple.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:0.00 USD",
    },
    {
        "title": "智能手表 Ultra - GPS+心率+血氧",
        "description": "1.5寸AMOLED屏，GPS定位，心率血氧监测，100+运动模式，14天续航。",
        "price": "199.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "TechBand",
        "gtin": "06123456789050",
        "mpn": "TB-ULTRA-SLV",
        "google_product_category": "Electronics > Wearable Technology > Smartwatches",
        "product_type": "Smartwatch",
        "image_url": "https://example.com/images/smartwatch-ultra.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:9.99 USD",
    },
    {
        "title": "不锈钢保温杯 500ml 真空双层",
        "description": "304不锈钢内胆，12小时保温，8小时保冷。BPA-free杯盖。",
        "price": "19.99 USD",
        "currency": "USD",
        "availability": "in_stock",
        "brand": "ThermoKeep",
        "gtin": "06123456789067",
        "mpn": "TK-SS-500-WHT",
        "google_product_category": "Home & Garden > Kitchen & Dining > Drinkware > Thermoses & Insulated Beverage Containers",
        "product_type": "Insulated Water Bottle",
        "image_url": "https://example.com/images/insulated-bottle-white.jpg",
        "link": "",
        "condition": "new",
        "shipping": "US:4.99 USD",
    },
]


def create_sample_feed_products(site_id, domain=""):
    """Insert GMC sample products for a site. Returns list of created products."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    created = []
    try:
        for p in SAMPLE_FEED_PRODUCTS:
            link = p["link"] or (f"https://{domain}/product/{p['mpn'].lower()}" if domain else "")
            conn.execute(
                """INSERT INTO feed_products
                   (site_id, title, description, price, currency, availability,
                    brand, gtin, mpn, google_product_category, product_type,
                    image_url, link, condition, shipping,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    site_id,
                    p["title"], p["description"], p["price"], p["currency"], p["availability"],
                    p["brand"], p["gtin"], p["mpn"], p["google_product_category"], p["product_type"],
                    p["image_url"], link, p["condition"], p["shipping"],
                    now, now,
                ),
            )
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created.append({
                "id": pid, "site_id": site_id,
                "title": p["title"], "description": p["description"],
                "price": p["price"], "currency": p["currency"],
                "availability": p["availability"], "brand": p["brand"],
                "gtin": p["gtin"], "mpn": p["mpn"],
                "google_product_category": p["google_product_category"],
                "product_type": p["product_type"],
                "image_url": p["image_url"], "link": link,
                "condition": p["condition"], "shipping": p["shipping"],
                "created_at": now, "updated_at": now,
            })
        conn.commit()
        return created
    finally:
        conn.close()


def delete_cf_account(account_id):
    """Delete a Cloudflare account. Clears legacy config if it was the last one."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM cloudflare_accounts WHERE id = ?", (account_id,))
        # If no accounts left, also clear the legacy global_config token
        remaining = conn.execute("SELECT COUNT(*) FROM cloudflare_accounts").fetchone()[0]
        if remaining == 0:
            conn.execute("DELETE FROM global_config WHERE config_key = 'cf_api_token'")
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

# ---- Brand Kit CRUD ----

def _deserialize_brand_kit(d: dict) -> dict:
    """Parse JSON fields in a brand kit record."""
    json_array_fields = {"colors", "html_site"}
    for field in ("colors", "typography", "woo_config", "footer_config",
                  "business_info", "tax_config", "shipping_config",
                  "html_site", "static_style", "design_system"):
        if field in json_array_fields:
            default, empty = "[]", []
        elif field == "html_site":
            default, empty = "{}", {}
        else:
            default, empty = "{}", {}
        try:
            d[field] = json.loads(d.get(field, default))
        except (json.JSONDecodeError, TypeError):
            d[field] = empty
    return d


def create_brand_kit(data: dict) -> dict:
    """Create a new brand kit. Auto-assigns fingerprint env. Returns the created record as dict."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        # Auto-populate brand_name from name if not provided
        if not data.get("brand_name"):
            data["brand_name"] = data.get("name", "")
        # Resolve proxy from proxy_id if provided
        proxy_url = data.get("proxy", "")
        proxy_id = data.get("proxy_id") or None
        if proxy_id and not proxy_url:
            p_row = conn.execute("SELECT proxy_url FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
            if p_row:
                proxy_url = p_row["proxy_url"]

        conn.execute(
            """INSERT INTO brand_kits
               (name, brand_name, description, industry, raw_svg, processed_svg,
                colors, typography, directory, png_256, png_512, png_1024,
                ico, webp, og_image, brand_md, status, error_message,
                woo_config, footer_config, business_info, tax_config, shipping_config,
                html_site, static_style, design_system,
                created_by, cloakbrowser_profile_name, proxy, proxy_id,
                google_account_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("name", ""),
                data.get("brand_name", ""),
                data.get("description", ""),
                data.get("industry", ""),
                data.get("raw_svg", ""),
                data.get("processed_svg", ""),
                json.dumps(data.get("colors", [])),
                json.dumps(data.get("typography", {})),
                data.get("directory", ""),
                data.get("png_256", ""),
                data.get("png_512", ""),
                data.get("png_1024", ""),
                data.get("ico", ""),
                data.get("webp", ""),
                data.get("og_image", ""),
                data.get("brand_md", ""),
                data.get("status", "draft"),
                data.get("error_message", ""),
                json.dumps(data.get("woo_config", {})),
                json.dumps(data.get("footer_config", {})),
                json.dumps(data.get("business_info", {})),
                json.dumps(data.get("tax_config", {})),
                json.dumps(data.get("shipping_config", {})),
                json.dumps(data.get("html_site", {})),
                json.dumps(data.get("static_style", {})),
                json.dumps(data.get("design_system", {})),
                data.get("created_by"),
                data.get("cloakbrowser_profile_name"),
                proxy_url,
                proxy_id,
                data.get("google_account_id") or None,
                now, now,
            ),
        )
        kit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Mark proxy as occupied
        if proxy_id and proxy_url:
            kit_name = data.get("name", "")
            conn.execute(
                "UPDATE proxies SET occupied_kit_id = ?, occupied_kit_name = ? WHERE id = ?",
                (kit_id, kit_name, proxy_id),
            )

        # Mark Google account as occupied
        google_account_id = data.get("google_account_id") or None
        logger.info("create_brand_kit: kit_id=%s google_account_id=%r kit_name=%r", kit_id, google_account_id, data.get("name", ""))
        if google_account_id:
            kit_name = data.get("name", "")
            try:
                cur = conn.execute(
                    "UPDATE google_accounts SET occupied_kit_id = ?, occupied_kit_name = ? WHERE id = ?",
                    (kit_id, kit_name, int(google_account_id)),
                )
                logger.info("create_brand_kit: google_accounts UPDATE affected %s row(s)", cur.rowcount)
            except Exception as e:
                logger.error("create_brand_kit: google_accounts UPDATE failed: %s", e)
        else:
            logger.info("create_brand_kit: google_account_id is falsy — skipping occupancy update")

        conn.commit()
        # Force WAL checkpoint so other connections see the write immediately
        cp = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        logger.info("create_brand_kit: wal_checkpoint result=%s", dict(cp) if cp else None)
        # Verify occupancy was written
        if google_account_id:
            vfy = conn.execute(
                "SELECT occupied_kit_id, occupied_kit_name FROM google_accounts WHERE id = ?",
                (int(google_account_id),),
            ).fetchone()
            logger.info("create_brand_kit: post-commit verify google_account id=%s → occupied_kit_id=%r occupied_kit_name=%r",
                        google_account_id, vfy["occupied_kit_id"] if vfy else None, vfy["occupied_kit_name"] if vfy else None)
        return get_brand_kit(kit_id)
    finally:
        conn.close()


def get_brand_kit(kit_id: int) -> dict | None:
    """Get a single brand kit by ID, including fingerprint env info."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM brand_kits WHERE id = ?", (kit_id,)).fetchone()
        if row:
            d = _deserialize_brand_kit(dict(row))
            # Enrich with profile info if assigned
            if d.get("cloakbrowser_profile_name"):
                d["profile_info"] = _get_profile_info(d["cloakbrowser_profile_name"])
            # Enrich with Google account info if assigned
            if d.get("google_account_id"):
                ga = conn.execute(
                    "SELECT email, password, recovery_email, totp_secret, country, registration_year FROM google_accounts WHERE id = ?",
                    (d["google_account_id"],),
                ).fetchone()
                if ga:
                    d["google_account_email"] = ga["email"]
                    d["google_account_password"] = ga["password"]
                    d["google_account_recovery"] = ga["recovery_email"]
                    d["google_account_totp"] = ga["totp_secret"]
                    d["google_account_country"] = ga["country"]
                    d["google_account_year"] = ga["registration_year"]
            return d
        return None
    finally:
        conn.close()


def list_brand_kits(user_id=None) -> list:
    """List all brand kits ordered by creation time descending. Optionally filter by user_id."""
    conn = get_db()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM brand_kits WHERE created_by = ? ORDER BY id DESC", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM brand_kits ORDER BY id DESC"
            ).fetchall()
        results = []
        for r in rows:
            d = _deserialize_brand_kit(dict(r))
            if d.get("cloakbrowser_profile_name"):
                d["profile_info"] = _get_profile_info(d["cloakbrowser_profile_name"])
            if d.get("google_account_id"):
                ga = conn.execute(
                    "SELECT email FROM google_accounts WHERE id = ?",
                    (d["google_account_id"],),
                ).fetchone()
                d["google_account_email"] = ga["email"] if ga else None
            results.append(d)
        return results
    finally:
        conn.close()


def update_brand_kit(kit_id: int, data: dict) -> dict | None:
    """Update brand kit fields. Returns updated record or None if not found."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        row = conn.execute("SELECT * FROM brand_kits WHERE id = ?", (kit_id,)).fetchone()
        if not row:
            return None
        updatable = [
            "name", "brand_name", "description", "industry",
            "raw_svg", "processed_svg", "colors", "typography",
            "directory", "png_256", "png_512", "png_1024",
            "ico", "webp", "og_image", "brand_md",
            "status", "error_message",
            "woo_config", "footer_config",
            "business_info", "tax_config", "shipping_config",
            "html_site", "static_style", "design_system",
            "cloakbrowser_profile_name", "proxy", "proxy_id",
            "google_account_id",
            "design_project_id", "design_screens",
        ]
        sets = []
        vals = []
        json_fields = ("colors", "typography", "woo_config", "footer_config",
                        "business_info", "tax_config", "shipping_config",
                        "html_site", "static_style", "design_system")
        for key in updatable:
            if key in data:
                sets.append(f"{key} = ?")
                val = data[key]
                if key in json_fields and isinstance(val, (list, dict)):
                    val = json.dumps(val)
                vals.append(val)
        if not sets:
            return get_brand_kit(kit_id)
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(kit_id)

        # Handle proxy_id change: release old, occupy new
        old_proxy_id = row["proxy_id"] if "proxy_id" in row.keys() else None
        new_proxy_id = data.get("proxy_id") or None
        if new_proxy_id and new_proxy_id != old_proxy_id:
            # Release old
            if old_proxy_id:
                conn.execute(
                    "UPDATE proxies SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (old_proxy_id,),
                )
            # Occupy new
            kit_name = data.get("name") or row["name"]
            p_row = conn.execute("SELECT proxy_url FROM proxies WHERE id = ?", (new_proxy_id,)).fetchone()
            if p_row:
                conn.execute(
                    "UPDATE proxies SET occupied_kit_id = ?, occupied_kit_name = ? WHERE id = ?",
                    (kit_id, kit_name, new_proxy_id),
                )
                # Also update proxy URL if not explicitly set
                if "proxy" not in data:
                    sets.append("proxy = ?")
                    vals.insert(-2, p_row["proxy_url"])

        # Handle google_account_id change: release old, occupy new
        old_ga_id = row["google_account_id"] if "google_account_id" in row.keys() else None
        new_ga_id = data.get("google_account_id") or None
        logger.info("update_brand_kit: old_ga=%r new_ga=%r", old_ga_id, new_ga_id)
        if new_ga_id != old_ga_id:
            if old_ga_id:
                conn.execute(
                    "UPDATE google_accounts SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (old_ga_id,),
                )
            if new_ga_id:
                kit_name = data.get("name") or row["name"]
                conn.execute(
                    "UPDATE google_accounts SET occupied_kit_id = ?, occupied_kit_name = ? WHERE id = ?",
                    (kit_id, kit_name, new_ga_id),
                )

        conn.execute(f"UPDATE brand_kits SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        cp = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        logger.info("update_brand_kit: wal_checkpoint result=%s", dict(cp) if cp else None)
        return get_brand_kit(kit_id)
    finally:
        conn.close()


def delete_brand_kit(kit_id: int) -> None:
    """Delete brand kit record and its asset directory. Releases associated fingerprint env, proxy, and Google account."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT directory, cloakbrowser_profile_name, proxy_id, google_account_id FROM brand_kits WHERE id = ?",
            (kit_id,),
        ).fetchone()
        if row:
            # Release CloakBrowser profile if assigned (clear DB link + delete profile dir)
            if row["cloakbrowser_profile_name"]:
                profile_path = os.path.join(get_profiles_root(), row["cloakbrowser_profile_name"])
                if os.path.isdir(profile_path):
                    shutil.rmtree(profile_path, ignore_errors=True)
                release_cloakbrowser_profile_from_brand_kit(row["cloakbrowser_profile_name"])
            if row["directory"]:
                dir_path = os.path.join(os.path.dirname(__file__), row["directory"])
                if os.path.isdir(dir_path):
                    shutil.rmtree(dir_path, ignore_errors=True)
            # Release proxy if assigned
            if row["proxy_id"]:
                conn.execute(
                    "UPDATE proxies SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (row["proxy_id"],),
                )
            # Release Google account if assigned
            if row["google_account_id"]:
                conn.execute(
                    "UPDATE google_accounts SET occupied_kit_id = NULL, occupied_kit_name = NULL WHERE id = ?",
                    (row["google_account_id"],),
                )
        conn.execute("DELETE FROM brand_kits WHERE id = ?", (kit_id,))
        conn.commit()
        _compact_ids(conn, "brand_kits")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Proxy Pool (decodo IP pools)
# ---------------------------------------------------------------------------

# decodo proxy seed data: IPs assigned to ports 10001-10100 (in order)
_DECODO_IPS = [
    "186.179.19.198", "136.0.174.181", "152.232.59.110", "198.56.3.207", "161.0.12.210",
    "213.219.252.5", "67.219.16.150", "152.232.233.102", "89.208.183.189", "204.14.109.105",
    "154.17.105.28", "136.0.55.162", "213.219.252.221", "204.14.110.216", "173.211.50.187",
    "152.232.63.122", "67.219.21.107", "172.93.171.213", "198.56.0.28", "67.219.22.127",
    "190.123.213.12", "154.29.9.24", "173.211.32.147", "172.84.102.238", "168.93.231.253",
    "152.232.239.12", "198.56.2.253", "92.38.208.228", "204.14.106.222", "67.219.18.81",
    "172.84.101.98", "45.73.163.187", "194.93.7.237", "172.84.127.249", "154.17.150.64",
    "185.206.221.172", "67.227.90.72", "172.84.125.56", "152.232.237.73", "213.219.253.230",
    "152.232.238.55", "136.0.71.37", "66.78.4.47", "67.219.19.234", "173.211.49.160",
    "67.227.90.106", "157.254.67.5", "96.9.207.153", "198.190.7.94", "66.78.4.184",
    "66.78.63.112", "152.232.237.194", "72.57.107.31", "67.207.173.75", "173.211.36.14",
    "154.29.12.221", "173.46.94.47", "67.207.173.94", "154.29.12.239", "198.56.6.175",
    "136.0.31.137", "23.27.90.97", "72.57.106.123", "172.84.99.232", "23.27.82.19",
    "157.254.65.160", "184.174.10.31", "195.184.232.79", "172.84.100.215", "66.78.2.18",
    "172.93.171.15", "136.0.163.250", "142.111.236.151", "185.202.171.0", "173.211.49.59",
    "89.208.55.233", "172.84.125.37", "184.174.13.214", "185.202.170.85", "161.0.8.154",
    "185.206.220.105", "161.0.13.73", "157.254.69.239", "152.232.61.66", "66.78.7.137",
    "157.254.65.221", "184.174.11.110", "168.93.220.100", "92.38.210.84", "172.84.98.43",
    "185.163.210.175", "185.206.223.179", "23.27.71.135", "136.0.147.26", "152.232.234.58",
    "172.84.99.68", "154.17.105.63", "95.163.150.131", "67.227.90.152", "173.211.50.182",
]

_DECODO_DEFAULT_USERNAME = "spx9vttaji"
_DECODO_DEFAULT_PASSWORD = "n10cCtesxLhHi41~nU"
_DECODO_DEFAULT_HOST = "dc.decodo.com"
_DECODO_PORT_START = 10001


# Default okkproxy seed entries (IP:PORT:USERNAME:PASSWORD format → HTTP proxy)
_OKKPROXY_DEFAULT_LIST = (
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USCFiZlVsGhjPrbfcm-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-US4udVt71NJiMK3eSI-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USdbAXogM8TEzjO34R-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-UScP4g8vxDik67KQIz-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USb0Fop5ahQ9g8xJTi-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-US34EfN8ZwsMbngP5K-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USZn0y4CgawfeGk3z6-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-US0CaQguvcTUdqfPR8-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USOfKrQhAFyC7nwbUa-sesstime-60:I17811506913496\n"
    "49.51.189.254:9999:td-customer-I17811506913496-country-us-sessid-USq6KxutRQYHcNw8VU-sesstime-60:I17811506913496"
)


def _build_proxy_url(ip: str, port: int, username: str, password: str, host: str = "",
                     proxy_type: str = "socks5") -> str:
    """Build proxy URL. SOCKS5: scheme://user:pass@host:port. HTTP: scheme://user:pass@ip:port."""
    from urllib.parse import quote
    u = quote(username, safe="")
    p = quote(password, safe="")
    if proxy_type == "http":
        return f"http://{u}:{p}@{ip}:{port}"
    return f"socks5://{u}:{p}@{host}:{port}"


def _seed_proxies_from_config(conn) -> int:
    """Seed proxies from config: decodo (SOCKS5) or okkproxy (HTTP). Returns count."""
    count = 0
    # Read proxy provider type
    provider_row = conn.execute(
        "SELECT config_value FROM global_config WHERE config_key = 'proxy_provider'"
    ).fetchone()
    provider = (provider_row["config_value"] if provider_row else "decodo").strip()

    if provider == "okkproxy":
        # Read okkproxy raw list
        raw_row = conn.execute(
            "SELECT config_value FROM global_config WHERE config_key = 'okkproxy_raw_list'"
        ).fetchone()
        raw_text = (raw_row["config_value"] if raw_row else "").strip()
        if not raw_text:
            raw_text = _OKKPROXY_DEFAULT_LIST
        count = _parse_proxy_text(conn, raw_text, "http")
    else:
        # decodo (SOCKS5)
        cfg = {}
        try:
            rows = conn.execute(
                "SELECT config_key, config_value FROM global_config WHERE config_key LIKE 'decodo_%'"
            ).fetchall()
            for r in rows:
                cfg[r["config_key"]] = r["config_value"]
        except Exception:
            pass
        username = (cfg.get("decodo_username") or _DECODO_DEFAULT_USERNAME).strip()
        password = (cfg.get("decodo_password") or _DECODO_DEFAULT_PASSWORD).strip()
        host = (cfg.get("decodo_host") or _DECODO_DEFAULT_HOST).strip()
        try:
            port_start = int(cfg.get("decodo_port_start") or _DECODO_PORT_START)
        except (ValueError, TypeError):
            port_start = _DECODO_PORT_START

        for i, ip in enumerate(_DECODO_IPS):
            port = port_start + i
            url = _build_proxy_url(ip, port, username, password, host, "socks5")
            exists = conn.execute("SELECT 1 FROM proxies WHERE proxy_url = ?", (url,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO proxies (proxy_url, ip, port, proxy_type) VALUES (?, ?, ?, 'socks5')",
                    (url, ip, port),
                )
                count += 1
    return count


def _parse_proxy_text(conn, text: str, proxy_type: str = "http") -> int:
    """Parse raw proxy text (IP:PORT:USERNAME:PASSWORD) and insert into proxies table."""
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(':')
        if len(parts) != 4:
            continue
        ip, port_str, username, password = parts
        try:
            port = int(port_str)
        except ValueError:
            continue
        url = _build_proxy_url(ip, port, username, password, "", proxy_type)
        exists = conn.execute("SELECT 1 FROM proxies WHERE proxy_url = ?", (url,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO proxies (proxy_url, ip, port, proxy_type) VALUES (?, ?, ?, ?)",
                (url, ip, port, proxy_type),
            )
            count += 1
    return count


def import_proxies_from_text(text: str, proxy_type: str = "http") -> int:
    """Public API: import proxies from raw text (IP:PORT:USERNAME:PASSWORD format)."""
    conn = get_db()
    try:
        count = _parse_proxy_text(conn, text, proxy_type)
        return count
    finally:
        conn.commit()
        conn.close()


def list_proxies() -> list:
    """List all proxies with occupancy info — bidirectional check."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT p.id, p.proxy_url, p.ip, p.port, p.proxy_type, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = p.occupied_kit_id) "
            "       THEN p.occupied_kit_id ELSE NULL END,"
            "  (SELECT bk.id FROM brand_kits bk WHERE bk.proxy_id = p.id LIMIT 1)"
            ") AS occupied_kit_id, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = p.occupied_kit_id) "
            "       THEN p.occupied_kit_name ELSE NULL END,"
            "  (SELECT bk.name FROM brand_kits bk WHERE bk.proxy_id = p.id LIMIT 1)"
            ") AS occupied_kit_name "
            "FROM proxies p ORDER BY p.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_available_proxies() -> list:
    """List all proxies with occupancy info — bidirectional check."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT p.id, p.proxy_url, p.ip, p.port, p.proxy_type, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = p.occupied_kit_id) "
            "       THEN p.occupied_kit_id ELSE NULL END,"
            "  (SELECT bk.id FROM brand_kits bk WHERE bk.proxy_id = p.id LIMIT 1)"
            ") AS occupied_kit_id, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = p.occupied_kit_id) "
            "       THEN p.occupied_kit_name ELSE NULL END,"
            "  (SELECT bk.name FROM brand_kits bk WHERE bk.proxy_id = p.id LIMIT 1)"
            ") AS occupied_kit_name "
            "FROM proxies p ORDER BY p.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reseed_proxies() -> int:
    """Regenerate proxy pool from current global_config (decodo or okkproxy)."""
    conn = get_db()
    try:
        return _seed_proxies_from_config(conn)
    finally:
        conn.commit()
        conn.close()


def get_proxy(proxy_id: int) -> dict | None:
    """Get a single proxy by ID."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Google Account Pool (for GMC automation with TOTP 2FA)
# ---------------------------------------------------------------------------

def list_google_accounts() -> list:
    """List all Google accounts with occupancy info — bidirectional check.

    Uses occupied_kit_id if valid, otherwise falls back to checking which brand_kit
    references this account. This handles cases where create_brand_kit's occupancy
    UPDATE didn't persist (WAL race, old code, migration, etc).
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ga.id, ga.email, ga.password, ga.recovery_email, ga.totp_secret, "
            "ga.registration_year, ga.country, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = ga.occupied_kit_id) "
            "       THEN ga.occupied_kit_id ELSE NULL END,"
            "  (SELECT bk.id FROM brand_kits bk WHERE bk.google_account_id = ga.id LIMIT 1)"
            ") AS occupied_kit_id, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = ga.occupied_kit_id) "
            "       THEN ga.occupied_kit_name ELSE NULL END,"
            "  (SELECT bk.name FROM brand_kits bk WHERE bk.google_account_id = ga.id LIMIT 1)"
            ") AS occupied_kit_name, "
            "ga.created_at, ga.updated_at FROM google_accounts ga ORDER BY ga.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_available_google_accounts() -> list:
    """List all Google accounts with occupancy info — bidirectional check."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ga.id, ga.email, ga.country, ga.registration_year, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = ga.occupied_kit_id) "
            "       THEN ga.occupied_kit_id ELSE NULL END,"
            "  (SELECT bk.id FROM brand_kits bk WHERE bk.google_account_id = ga.id LIMIT 1)"
            ") AS occupied_kit_id, "
            "COALESCE("
            "  CASE WHEN EXISTS (SELECT 1 FROM brand_kits WHERE id = ga.occupied_kit_id) "
            "       THEN ga.occupied_kit_name ELSE NULL END,"
            "  (SELECT bk.name FROM brand_kits bk WHERE bk.google_account_id = ga.id LIMIT 1)"
            ") AS occupied_kit_name "
            "FROM google_accounts ga ORDER BY ga.id"
        ).fetchall()
        occupied = [dict(r) for r in rows if r["occupied_kit_id"]]
        logger.info("get_available_google_accounts: %d occupied out of %d total",
                    len(occupied), len(rows))
        for r in occupied:
            logger.info("  occupied: id=%s email=%s kit_name=%s",
                        r["id"], r["email"], r.get("occupied_kit_name"))
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_google_account(account_id: int) -> dict | None:
    """Get a single Google account by ID (full credentials, internal use)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM google_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def import_google_accounts_from_txt(text: str) -> int:
    """Parse TXT content (email|password|recovery_email|base32_secret|year|country)
    and insert new Google accounts. Skips duplicates by email. Returns count."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    count = 0
    try:
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            email = parts[0]
            password = parts[1]
            recovery_email = parts[2] if len(parts) > 2 else ""
            totp_secret = parts[3] if len(parts) > 3 else ""
            registration_year = parts[4] if len(parts) > 4 else ""
            country = parts[5] if len(parts) > 5 else ""
            exists = conn.execute(
                "SELECT 1 FROM google_accounts WHERE email = ?", (email,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO google_accounts "
                "(email, password, recovery_email, totp_secret, registration_year, country, "
                "occupied_kit_id, occupied_kit_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (email, password, recovery_email, totp_secret, registration_year, country, now, now),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def delete_google_account(account_id: int) -> bool:
    """Delete a Google account by ID. Returns True if deleted."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM google_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM google_accounts WHERE id = ?", (account_id,))
        conn.commit()
        _compact_ids(conn, "google_accounts")
        conn.commit()
        return True
    finally:
        conn.close()


def release_google_account(kit_id: int) -> None:
    """Release the Google account bound to a brand kit."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE google_accounts SET occupied_kit_id = NULL, occupied_kit_name = NULL "
            "WHERE occupied_kit_id = ?", (kit_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# Static Site Products — CRUD (replaces WooCommerce products)
# ============================================================

def create_static_site_product(data: dict) -> dict:
    """Create a product for a static site."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT INTO static_site_products
               (site_id, title, description, price, sale_price, currency,
                image_url, additional_images, category, brand, sku, mpn, gtin,
                availability, condition, shipping_weight, shipping_weight_unit,
                product_url, variant_data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("site_id"),
                data.get("title", ""),
                data.get("description", ""),
                data.get("price", 0.0),
                data.get("sale_price"),
                data.get("currency", "USD"),
                data.get("image_url", ""),
                json.dumps(data.get("additional_images", [])),
                data.get("category", ""),
                data.get("brand", ""),
                data.get("sku", ""),
                data.get("mpn", ""),
                data.get("gtin", ""),
                data.get("availability", "in_stock"),
                data.get("condition", "new"),
                data.get("shipping_weight", ""),
                data.get("shipping_weight_unit", "kg"),
                data.get("product_url", ""),
                json.dumps(data.get("variant_data", {})),
                now, now,
            ),
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return get_static_site_product(pid)
    finally:
        conn.close()


def get_static_site_product(product_id: int) -> dict | None:
    """Get a single product by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM static_site_products WHERE id = ?", (product_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for f in ("additional_images", "variant_data"):
            try:
                d[f] = json.loads(d.get(f, "[]"))
            except (json.JSONDecodeError, TypeError):
                d[f] = [] if f == "additional_images" else {}
        return d
    finally:
        conn.close()


def list_static_site_products(site_id: int) -> list:
    """List all products for a given site."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM static_site_products WHERE site_id = ? ORDER BY id",
            (site_id,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["additional_images"] = json.loads(d.get("additional_images", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["additional_images"] = []
            try:
                d["variant_data"] = json.loads(d.get("variant_data", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["variant_data"] = {}
            result.append(d)
        return result
    finally:
        conn.close()


def update_static_site_product(product_id: int, data: dict) -> dict | None:
    """Update a product. Returns updated record or None if not found."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        row = conn.execute(
            "SELECT * FROM static_site_products WHERE id = ?", (product_id,)
        ).fetchone()
        if not row:
            return None
        updatable = [
            "title", "description", "price", "sale_price", "currency",
            "image_url", "additional_images", "category", "brand",
            "sku", "mpn", "gtin", "availability", "condition",
            "shipping_weight", "shipping_weight_unit", "product_url",
            "variant_data",
        ]
        json_fields = {"additional_images", "variant_data"}
        sets = []
        vals = []
        for key in updatable:
            if key in data:
                sets.append(f"{key} = ?")
                val = data[key]
                if key in json_fields and isinstance(val, (list, dict)):
                    val = json.dumps(val)
                vals.append(val)
        if not sets:
            return get_static_site_product(product_id)
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(product_id)
        conn.execute(
            f"UPDATE static_site_products SET {', '.join(sets)} WHERE id = ?", vals
        )
        conn.commit()
        return get_static_site_product(product_id)
    finally:
        conn.close()


def delete_static_site_product(product_id: int) -> bool:
    """Delete a product. Returns True if deleted."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM static_site_products WHERE id = ?", (product_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM static_site_products WHERE id = ?", (product_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def import_products_to_site(site_id: int, products: list) -> int:
    """Bulk import products from screening results to a static site.
    Returns count of newly created products."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    count = 0
    try:
        for p in products:
            conn.execute(
                """INSERT INTO static_site_products
                   (site_id, title, description, price, sale_price, currency,
                    image_url, additional_images, category, brand, sku, mpn, gtin,
                    availability, condition, shipping_weight, shipping_weight_unit,
                    product_url, variant_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    site_id,
                    p.get("title", ""),
                    p.get("description", ""),
                    p.get("price", 0.0),
                    p.get("sale_price"),
                    p.get("currency", "USD"),
                    p.get("image_url", ""),
                    json.dumps(p.get("additional_images", p.get("images", []))),
                    p.get("category", ""),
                    p.get("brand", ""),
                    p.get("sku", ""),
                    p.get("mpn", ""),
                    p.get("gtin", ""),
                    p.get("availability", "in_stock"),
                    p.get("condition", "new"),
                    p.get("shipping_weight", ""),
                    p.get("shipping_weight_unit", "kg"),
                    p.get("product_url", ""),
                    json.dumps(p.get("variant_data", {})),
                    now, now,
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


