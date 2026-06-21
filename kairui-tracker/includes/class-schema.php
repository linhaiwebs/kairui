<?php
class Kairui_Schema {
    public static function create_tables() {
        global $wpdb;
        $charset = $wpdb->get_charset_collate();

        $events = "CREATE TABLE IF NOT EXISTS {$wpdb->prefix}kairui_events (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            source VARCHAR(255) NOT NULL,
            session_id VARCHAR(64) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            page_url VARCHAR(2048) DEFAULT '',
            product_id BIGINT DEFAULT NULL,
            product_price DECIMAL(10,2) DEFAULT NULL,
            order_id BIGINT DEFAULT NULL,
            order_total DECIMAL(10,2) DEFAULT NULL,
            extra_data JSON DEFAULT NULL,
            client_ip VARCHAR(45) DEFAULT '',
            user_agent VARCHAR(512) DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_source_time (source, created_at),
            INDEX idx_session (session_id),
            INDEX idx_event_type (event_type)
        ) $charset;";

        $hourly = "CREATE TABLE IF NOT EXISTS {$wpdb->prefix}kairui_hourly_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            source VARCHAR(255) NOT NULL,
            hour DATETIME NOT NULL,
            page_views INT DEFAULT 0,
            product_views INT DEFAULT 0,
            add_to_carts INT DEFAULT 0,
            checkouts INT DEFAULT 0,
            orders INT DEFAULT 0,
            revenue DECIMAL(12,2) DEFAULT 0,
            unique_visitors INT DEFAULT 0,
            bounce_count INT DEFAULT 0,
            UNIQUE KEY idx_source_hour (source, hour)
        ) $charset;";

        $attribution = "CREATE TABLE IF NOT EXISTS {$wpdb->prefix}kairui_order_attribution (
            order_id BIGINT PRIMARY KEY,
            source VARCHAR(255) NOT NULL,
            session_id VARCHAR(64) DEFAULT '',
            landing_page VARCHAR(2048) DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) $charset;";

        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta($events);
        dbDelta($hourly);
        dbDelta($attribution);
    }

    public static function cleanup() {
        // Keep tables on deactivation; only remove scheduled hooks
        wp_clear_scheduled_hook('kairui_hourly_aggregate');
    }
}
