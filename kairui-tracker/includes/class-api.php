<?php
class Kairui_API {
    public static function init() {
        add_action('rest_api_init', [__CLASS__, 'register_routes']);
    }

    public static function register_routes() {
        // Public endpoint for JS tracker
        register_rest_route('kairui/v1', '/track', [
            'methods' => 'POST',
            'callback' => [__CLASS__, 'handle_track'],
            'permission_callback' => '__return_true',
        ]);

        // Protected analytics endpoints
        $endpoints = [
            ['/analytics/summary', 'GET', 'get_summary'],
            ['/analytics/funnel', 'GET', 'get_funnel'],
            ['/analytics/trend', 'GET', 'get_trend'],
            ['/analytics/top-products', 'GET', 'get_top_products'],
            ['/analytics/orders', 'GET', 'get_orders'],
            ['/analytics/visitors', 'GET', 'get_visitors'],
            ['/analytics/behavior-flow', 'GET', 'get_behavior_flow'],
            ['/analytics/sessions', 'GET', 'get_sessions'],
            ['/analytics/realtime', 'GET', 'get_realtime'],
            ['/analytics/session/(?P<id>[a-f0-9-]+)/timeline', 'GET', 'get_session_timeline'],
        ];

        foreach ($endpoints as [$route, $method, $callback]) {
            register_rest_route('kairui/v1', $route, [
                'methods' => $method,
                'callback' => [__CLASS__, $callback],
                'permission_callback' => [__CLASS__, 'check_api_key'],
            ]);
        }
    }

    public static function check_api_key($request) {
        $key = $request->get_header('X-Kairui-Key');
        return $key && hash_equals(get_option('kairui_api_key', ''), $key);
    }

    // ---- Public: event tracking ----

    public static function handle_track($request) {
        $events = $request->get_json_params();
        if (!is_array($events)) {
            return new WP_Error('invalid', 'Expected array of events', ['status' => 400]);
        }

        $inserted = 0;
        foreach ($events as $e) {
            $id = Kairui_Tracker::record_event(
                sanitize_text_field($e['type'] ?? ''),
                [
                    'product_id' => intval($e['product_id'] ?? 0) ?: null,
                    'product_price' => floatval($e['product_price'] ?? 0) ?: null,
                    'extra' => $e['extra'] ?? null,
                ]
            );
            if ($id) $inserted++;
        }

        return rest_ensure_response(['ok' => $inserted, 'received' => count($events)]);
    }

    // ---- Analytics endpoints ----

    public static function get_summary($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $since = self::period_to_date($period);

        $tbl = $wpdb->prefix . 'kairui_hourly_stats';
        $where = "WHERE hour >= %s";
        $params = [$since];
        if ($source) {
            $where .= " AND source = %s";
            $params[] = $source;
        }

        $sql = $wpdb->prepare(
            "SELECT source,
                SUM(page_views) as page_views,
                SUM(product_views) as product_views,
                SUM(add_to_carts) as add_to_carts,
                SUM(checkouts) as checkouts,
                SUM(orders) as orders,
                SUM(revenue) as revenue,
                SUM(unique_visitors) as visitors,
                SUM(bounce_count) as bounces
            FROM $tbl $where
            GROUP BY source",
            $params
        );
        $rows = $wpdb->get_results($sql, ARRAY_A);

        // Fallback: if hourly_stats is empty, query raw events directly
        if (empty($rows)) {
            $etbl = $wpdb->prefix . 'kairui_events';
            $esql = $wpdb->prepare(
                "SELECT source,
                    SUM(CASE WHEN event_type='page_view' THEN 1 ELSE 0 END) as page_views,
                    SUM(CASE WHEN event_type='product_view' THEN 1 ELSE 0 END) as product_views,
                    SUM(CASE WHEN event_type='add_to_cart' THEN 1 ELSE 0 END) as add_to_carts,
                    SUM(CASE WHEN event_type='begin_checkout' THEN 1 ELSE 0 END) as checkouts,
                    SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) as orders,
                    SUM(CASE WHEN event_type='purchase' THEN order_total ELSE 0 END) as revenue,
                    COUNT(DISTINCT session_id) as visitors,
                    0 as bounces
                FROM $etbl $where
                GROUP BY source",
                $params
            );
            $rows = $wpdb->get_results($esql, ARRAY_A);
        }

        $sources = [];
        $totals = ['visitors'=>0,'page_views'=>0,'orders'=>0,'revenue'=>0];
        foreach ($rows as $r) {
            $r['conversion_rate'] = $r['visitors'] > 0 ? round($r['orders'] / $r['visitors'] * 100, 1) : 0;
            $r['aov'] = $r['orders'] > 0 ? round($r['revenue'] / $r['orders'], 2) : 0;
            $r['bounce_rate'] = $r['visitors'] > 0 ? round($r['bounces'] / $r['visitors'] * 100, 1) : 0;
            $sources[] = $r;
            $totals['visitors'] += $r['visitors'];
            $totals['orders'] += $r['orders'];
            $totals['revenue'] += $r['revenue'];
            $totals['page_views'] += $r['page_views'];
        }

        return rest_ensure_response([
            'period' => $period,
            'sources' => $sources,
            'totals' => $totals,
        ]);
    }

    public static function get_funnel($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        if (!$source) return new WP_Error('required', 'source parameter required', ['status' => 400]);
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_events';

        $steps = ['visit' => 'page_view', 'product_view' => 'product_view', 'add_to_cart' => 'add_to_cart', 'checkout' => 'begin_checkout', 'purchase' => 'purchase'];
        $funnel = [];
        $prev = null;
        foreach ($steps as $label => $event) {
            $sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM $tbl WHERE source=%s AND event_type=%s AND created_at >= %s", $source, $event, $since);
            $count = (int) $wpdb->get_var($sql);
            $drop = $prev ? round(($prev - $count) / max($prev, 1) * 100, 1) : 0;
            $funnel[] = ['step' => $label, 'count' => $count, 'drop_off' => $drop];
            $prev = $count;
        }
        return rest_ensure_response(['source' => $source, 'funnel' => $funnel]);
    }

    public static function get_trend($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '30d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_hourly_stats';
        $where = "WHERE hour >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }
        $sql = $wpdb->prepare("SELECT DATE(hour) as date, SUM(page_views) as visitors, SUM(orders) as orders, SUM(revenue) as revenue FROM $tbl $where GROUP BY DATE(hour) ORDER BY date", $params);
        return rest_ensure_response(['trend' => $wpdb->get_results($sql, ARRAY_A)]);
    }

    public static function get_top_products($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_events';
        $where = "WHERE created_at >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }
        $sql = $wpdb->prepare(
            "SELECT product_id, COUNT(*) as views,
                SUM(CASE WHEN event_type='add_to_cart' THEN 1 ELSE 0 END) as carts,
                SUM(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) as orders,
                SUM(CASE WHEN event_type='purchase' THEN order_total ELSE 0 END) as revenue
            FROM $tbl $where AND product_id IS NOT NULL
            GROUP BY product_id ORDER BY views DESC LIMIT 10",
            $params
        );
        return rest_ensure_response(['products' => $wpdb->get_results($sql, ARRAY_A)]);
    }

    public static function get_orders($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $page = max(1, intval($request->get_param('page') ?? 1));
        $per = 20;
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_order_attribution';
        $where = "WHERE created_at >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }
        $count_sql = $wpdb->prepare("SELECT COUNT(*) FROM $tbl $where", $params);
        $total = (int) $wpdb->get_var($count_sql);
        $offset = ($page - 1) * $per;
        $sql = $wpdb->prepare("SELECT * FROM $tbl $where ORDER BY created_at DESC LIMIT %d OFFSET %d", array_merge($params, [$per, $offset]));
        $orders = $wpdb->get_results($sql, ARRAY_A);
        return rest_ensure_response(['orders' => $orders, 'total' => $total, 'page' => $page]);
    }

    public static function get_visitors($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_events';
        $where = "WHERE created_at >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }

        $total_sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM $tbl $where", $params);
        $total_visitors = (int) $wpdb->get_var($total_sql);

        // Returning visitors: sessions with >1 distinct day
        $returning_sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM (SELECT session_id, COUNT(DISTINCT DATE(created_at)) as days FROM $tbl $where GROUP BY session_id HAVING days > 1) sub", $params);
        $returning = (int) $wpdb->get_var($returning_sql);

        return rest_ensure_response([
            'source' => $source ?: 'all',
            'total_visitors' => $total_visitors,
            'returning_visitors' => $returning,
            'new_visitors' => $total_visitors - $returning,
        ]);
    }

    public static function get_behavior_flow($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_events';
        $where = "WHERE created_at >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }

        $entry_sql = $wpdb->prepare("SELECT page_url, COUNT(DISTINCT session_id) as visitors FROM $tbl $where AND event_type='page_view' GROUP BY page_url ORDER BY visitors DESC LIMIT 10", $params);
        return rest_ensure_response(['top_entry_pages' => $wpdb->get_results($entry_sql, ARRAY_A)]);
    }

    public static function get_sessions($request) {
        global $wpdb;
        $period = sanitize_text_field($request->get_param('period') ?? '7d');
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $page = max(1, intval($request->get_param('page') ?? 1));
        $per = 20;
        $since = self::period_to_date($period);
        $tbl = $wpdb->prefix . 'kairui_events';
        $where = "WHERE created_at >= %s";
        $params = [$since];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }
        $count_sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM $tbl $where", $params);
        $total = (int) $wpdb->get_var($count_sql);
        $offset = ($page - 1) * $per;
        $sql = $wpdb->prepare(
            "SELECT session_id, MIN(source) as source, MIN(page_url) as entry_page,
                COUNT(*) as pages, MAX(created_at) as last_active,
                SUM(CASE WHEN event_type='product_view' THEN 1 ELSE 0 END) as products_viewed,
                MAX(CASE WHEN event_type='add_to_cart' THEN 1 ELSE 0 END) as added_to_cart,
                MAX(CASE WHEN event_type='begin_checkout' THEN 1 ELSE 0 END) as reached_checkout,
                MAX(CASE WHEN event_type='purchase' THEN 1 ELSE 0 END) as converted,
                MAX(order_id) as order_id
            FROM $tbl $where GROUP BY session_id ORDER BY last_active DESC LIMIT %d OFFSET %d",
            array_merge($params, [$per, $offset])
        );
        return rest_ensure_response(['sessions' => $wpdb->get_results($sql, ARRAY_A), 'total' => $total, 'page' => $page]);
    }

    public static function get_session_timeline($request) {
        global $wpdb;
        $sid = $request->get_param('id');
        $tbl = $wpdb->prefix . 'kairui_events';
        $sql = $wpdb->prepare("SELECT source, event_type, page_url, product_id, product_price, order_id, order_total, created_at FROM $tbl WHERE session_id=%s ORDER BY created_at", $sid);
        $events = $wpdb->get_results($sql, ARRAY_A);
        if (!$events) return new WP_Error('not_found', 'Session not found', ['status' => 404]);
        $timeline = array_map(function($e) {
            return [
                'time' => date('H:i:s', strtotime($e['created_at'])),
                'event' => $e['event_type'],
                'url' => $e['page_url'] ?? '',
                'product_id' => $e['product_id'] ? intval($e['product_id']) : null,
                'product_price' => $e['product_price'] ? floatval($e['product_price']) : null,
                'order_id' => $e['order_id'] ? intval($e['order_id']) : null,
                'order_total' => $e['order_total'] ? floatval($e['order_total']) : null,
            ];
        }, $events);
        $first = $events[0];
        return rest_ensure_response([
            'session_id' => $sid,
            'source' => $first['source'] ?? '',
            'entry_page' => $first['page_url'] ?? '',
            'timeline' => $timeline,
        ]);
    }

    public static function get_realtime($request) {
        global $wpdb;
        $source = sanitize_text_field($request->get_param('source') ?? '');
        $tbl = $wpdb->prefix . 'kairui_events';
        $cutoff = date('Y-m-d H:i:s', strtotime('-5 minutes'));
        $where = "WHERE event_type='heartbeat' AND created_at >= %s";
        $params = [$cutoff];
        if ($source) { $where .= " AND source=%s"; $params[] = $source; }
        $sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM $tbl $where", $params);
        $online = (int) $wpdb->get_var($sql);
        return rest_ensure_response(['online_now' => $online]);
    }

    private static function period_to_date($period) {
        switch ($period) {
            case 'today': return date('Y-m-d 00:00:00');
            case 'yesterday': return date('Y-m-d 00:00:00', strtotime('-1 day'));
            case '7d': return date('Y-m-d H:00:00', strtotime('-7 days'));
            case '30d': return date('Y-m-d H:00:00', strtotime('-30 days'));
            default: return date('Y-m-d H:00:00', strtotime('-7 days'));
        }
    }
}
