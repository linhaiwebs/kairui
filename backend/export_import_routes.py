

    # ============================================================
    # System Export / Import
    # ============================================================
    @app.route("/api/system/export", methods=["GET"])
    @jwt_required()
    def system_export():
        """Export all system configuration and data as JSON."""
        try:
            db = get_db()
            data = {}

            rows = db.execute("SELECT key, value FROM global_config").fetchall()
            data["global_config"] = {r["key"]: r["value"] for r in rows}

            rows = db.execute("SELECT id, username, password_hash, role, panel_environment_id, created_at FROM users").fetchall()
            data["users"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM sites").fetchall()
            data["sites"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM brand_kits").fetchall()
            data["brand_kits"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM cloudflare_accounts").fetchall()
            data["cloudflare_accounts"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM fingerprint_categories").fetchall()
            data["fingerprint_categories"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM profile_category_mapping").fetchall()
            data["profile_category_mapping"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM panel_environments").fetchall()
            data["panel_environments"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM wordpress_settings").fetchall()
            data["wordpress_settings"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM feed_products").fetchall()
            data["feed_products"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM woocommerce_products").fetchall()
            data["woocommerce_products"] = [dict(r) for r in rows]

            rows = db.execute("SELECT * FROM generated_feed").fetchall()
            data["generated_feed"] = [dict(r) for r in rows]

            try:
                rows = db.execute("SELECT * FROM proxies").fetchall()
                data["proxies"] = [dict(r) for r in rows]
            except:
                pass

            try:
                rows = db.execute("SELECT * FROM google_accounts").fetchall()
                data["google_accounts"] = [dict(r) for r in rows]
            except:
                pass

            data["_meta"] = {
                "exported_at": datetime.utcnow().isoformat(),
                "version": "1.0",
            }

            return jsonify({"code": 200, "data": data})
        except Exception as e:
            logger.error("system_export error: %s", e)
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/system/import", methods=["POST"])
    @jwt_required()
    def system_import():
        """Import system configuration and data from JSON."""
        try:
            data = request.get_json(silent=True) or {}
            if not data or "_meta" not in data:
                return jsonify({"code": 400, "message": "无效的导入数据"}), 400

            db = get_db()
            imported_tables = []

            def upsert_table(table_name, rows, key_cols):
                if not rows:
                    return 0
                count = 0
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    where = " AND ".join(k + " = ?" for k in key_cols if k in row)
                    where_vals = [row[k] for k in key_cols if k in row]
                    if not where:
                        continue
                    existing = db.execute(
                        "SELECT 1 FROM " + table_name + " WHERE " + where, where_vals
                    ).fetchone()
                    cols = list(row.keys())
                    vals = [row[k] for k in cols]
                    if existing:
                        set_clause = ", ".join(k + " = ?" for k in cols)
                        db.execute(
                            "UPDATE " + table_name + " SET " + set_clause + " WHERE " + where,
                            vals + where_vals
                        )
                    else:
                        placeholders = ", ".join("?" for _ in cols)
                        col_names = ", ".join(cols)
                        db.execute(
                            "INSERT INTO " + table_name + " (" + col_names + ") VALUES (" + placeholders + ")",
                            vals
                        )
                    count += 1
                return count

            tables_order = [
                ("global_config", ["key"]),
                ("users", ["id"]),
                ("panel_environments", ["id"]),
                ("cloudflare_accounts", ["id"]),
                ("fingerprint_categories", ["id"]),
                ("profile_category_mapping", ["profile_name", "category_id"]),
                ("brand_kits", ["id"]),
                ("sites", ["id"]),
                ("wordpress_settings", ["id"]),
                ("feed_products", ["id"]),
                ("woocommerce_products", ["id"]),
                ("generated_feed", ["id"]),
            ]

            for table, keys in tables_order:
                if table in data:
                    count = upsert_table(table, data[table], keys)
                    imported_tables.append(table + "(" + str(count) + ")")

            db.commit()
            msg = "导入成功: " + ", ".join(imported_tables)
            return jsonify({"code": 200, "message": msg})
        except Exception as e:
            logger.error("system_import error: %s", e)
            return jsonify({"code": 500, "message": str(e)[:200]}), 500
