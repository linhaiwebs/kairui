<?php
class Kairui_Aggregator {
    public static function init() {
        add_action('kairui_hourly_aggregate', [__CLASS__, 'run']);
        if (!wp_next_scheduled('kairui_hourly_aggregate')) {
            wp_schedule_event(time(), 'hourly', 'kairui_hourly_aggregate');
        }
    }

    public static function run() {
        global $wpdb;
        $tbl = $wpdb->prefix . 'kairui_events';
        $stats = $wpdb->prefix . 'kairui_hourly_stats';
        $hour = date('Y-m-d H:00:00', strtotime('-1 hour'));

        $sql = $wpdb->prepare(
            "INSERT INTO $stats (source, hour, page_views, product_views, add_to_carts, checkouts, orders, revenue, unique_visitors, bounce_count)
            SELECT source, %s as hour,
                SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'product_view' THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'add_to_cart' THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'begin_checkout' THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'purchase' THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'purchase' THEN order_total ELSE 0 END),
                COUNT(DISTINCT session_id),
                SUM(CASE WHEN event_type = 'bounce' THEN 1 ELSE 0 END)
            FROM $tbl
            WHERE created_at >= %s AND created_at < DATE_ADD(%s, INTERVAL 1 HOUR)
            GROUP BY source
            ON DUPLICATE KEY UPDATE
                page_views = VALUES(page_views),
                product_views = VALUES(product_views),
                add_to_carts = VALUES(add_to_carts),
                checkouts = VALUES(checkouts),
                orders = VALUES(orders),
                revenue = VALUES(revenue),
                unique_visitors = VALUES(unique_visitors),
                bounce_count = VALUES(bounce_count)",
            $hour, $hour, $hour
        );
        $wpdb->query($sql);

        // Cleanup events older than 90 days
        $cutoff = date('Y-m-d H:i:s', strtotime('-90 days'));
        $wpdb->query($wpdb->prepare("DELETE FROM $tbl WHERE created_at < %s", $cutoff));
    }
}
