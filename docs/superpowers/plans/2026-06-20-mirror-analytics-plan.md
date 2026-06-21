# 镜像站来源分析引擎 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 WordPress 插件 + 凯瑞仪表盘分析系统，追踪镜像站全链路用户行为并展示商业级分析报表。

**Architecture:** WordPress 插件（前端 JS 埋点 + 后端 REST API）→ 凯瑞投流后端调用 API → 仪表盘展示。两阶段独立交付。

**Tech Stack:** WordPress PHP (原生 API / WP-Cron / WooCommerce hooks), Vanilla JavaScript (tracker), Chart.js (前端图表), Vue 3 (仪表盘)。

---

## Phase 1: WordPress 插件 kairui-tracker

### Task 1: 插件骨架与激活

**Files:**
- Create: `kairui-tracker/kairui-tracker.php`
- Create: `kairui-tracker/includes/class-schema.php`

- [ ] **Step 1: 创建插件主文件**

```php
<?php
/**
 * Plugin Name: Kairui Mirror Tracker
 * Description: Tracks user behavior per mirror source for Kairui dashboard analytics.
 * Version: 1.0.0
 */

defined('ABSPATH') || exit;

define('KAIRUI_TRACKER_VERSION', '1.0.0');
define('KAIRUI_TRACKER_DIR', plugin_dir_path(__FILE__));

require_once KAIRUI_TRACKER_DIR . 'includes/class-schema.php';

register_activation_hook(__FILE__, ['Kairui_Schema', 'create_tables']);
register_deactivation_hook(__FILE__, ['Kairui_Schema', 'cleanup']);

// Generate API key on activation if not exists
register_activation_hook(__FILE__, function () {
    if (!get_option('kairui_api_key')) {
        update_option('kairui_api_key', wp_generate_password(32, false));
    }
});
```

- [ ] **Step 2: 创建建表/迁移类**

```php
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
```

- [ ] **Step 3: 测试激活**

在 WordPress 测试环境安装插件，验证三张表创建成功。

- [ ] **Step 4: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: kairui-tracker plugin skeleton with schema"
```

---

### Task 2: 来源识别与 Cookie

**Files:**
- Create: `kairui-tracker/includes/class-tracker.php`

- [ ] **Step 1: 创建 Tracker 类**

```php
<?php
class Kairui_Tracker {
    const COOKIE_SOURCE = 'kairui_src';
    const COOKIE_SESSION = 'kairui_sid';
    const SESSION_TTL = 1800; // 30 minutes

    public static function init() {
        add_action('init', [__CLASS__, 'set_source_cookie']);
    }

    public static function set_source_cookie() {
        // Read X-Forwarded-Host header (set by CF Worker)
        $source = '';
        if (!empty($_SERVER['HTTP_X_FORWARDED_HOST'])) {
            $source = sanitize_text_field($_SERVER['HTTP_X_FORWARDED_HOST']);
        }

        // Fallback to referrer host if no X-Forwarded-Host
        if (!$source && !empty($_SERVER['HTTP_REFERER'])) {
            $host = parse_url($_SERVER['HTTP_REFERER'], PHP_URL_HOST);
            if ($host && $host !== $_SERVER['HTTP_HOST']) {
                $source = $host;
            }
        }

        if ($source && empty($_COOKIE[self::COOKIE_SOURCE])) {
            setcookie(self::COOKIE_SOURCE, $source, time() + 7776000, '/', '', true, false); // 90 days
            $_COOKIE[self::COOKIE_SOURCE] = $source;
        }

        // Set or refresh session cookie
        if (empty($_COOKIE[self::COOKIE_SESSION])) {
            $sid = wp_generate_uuid4();
            setcookie(self::COOKIE_SESSION, $sid, time() + self::SESSION_TTL, '/', '', true, false);
            $_COOKIE[self::COOKIE_SESSION] = $sid;
        } else {
            // Extend session on activity
            setcookie(self::COOKIE_SESSION, $_COOKIE[self::COOKIE_SESSION], time() + self::SESSION_TTL, '/', '', true, false);
        }
    }

    public static function get_source() {
        return sanitize_text_field($_COOKIE[self::COOKIE_SOURCE] ?? 'direct');
    }

    public static function get_session_id() {
        return sanitize_text_field($_COOKIE[self::COOKIE_SESSION] ?? '');
    }
}
```

- [ ] **Step 2: 在主文件中初始化**

```php
require_once KAIRUI_TRACKER_DIR . 'includes/class-tracker.php';
Kairui_Tracker::init();
```

- [ ] **Step 3: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: source identification via X-Forwarded-Host + session cookie"
```

---

### Task 3: 服务端事件接收 API

**Files:**
- Modify: `kairui-tracker/includes/class-tracker.php`
- Create: `kairui-tracker/includes/class-api.php`

- [ ] **Step 1: 添加事件写入方法**

```php
// In class-tracker.php, add:
public static function record_event($event_type, $data = []) {
    global $wpdb;
    $source = self::get_source();
    $sid = self::get_session_id();

    $row = [
        'source'      => $source,
        'session_id'  => $sid,
        'event_type'  => $event_type,
        'page_url'    => sanitize_text_field($_SERVER['REQUEST_URI'] ?? ''),
        'product_id'  => $data['product_id'] ?? null,
        'product_price' => $data['product_price'] ?? null,
        'order_id'    => $data['order_id'] ?? null,
        'order_total' => $data['order_total'] ?? null,
        'extra_data'  => !empty($data['extra']) ? json_encode($data['extra']) : null,
        'client_ip'   => $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? '',
        'user_agent'  => sanitize_text_field($_SERVER['HTTP_USER_AGENT'] ?? ''),
    ];

    $wpdb->insert("{$wpdb->prefix}kairui_events", $row);
    return $wpdb->insert_id;
}
```

- [ ] **Step 2: 创建 REST API 基础类**

```php
<?php
class Kairui_API {
    public static function init() {
        add_action('rest_api_init', [__CLASS__, 'register_routes']);
    }

    public static function register_routes() {
        register_rest_route('kairui/v1', '/track', [
            'methods' => 'POST',
            'callback' => [__CLASS__, 'handle_track'],
            'permission_callback' => '__return_true',
        ]);
    }

    public static function check_api_key($request) {
        $key = $request->get_header('X-Kairui-Key');
        return $key && hash_equals(get_option('kairui_api_key', ''), $key);
    }

    public static function handle_track($request) {
        $events = $request->get_json_params();
        if (!is_array($events)) {
            return new WP_Error('invalid', 'Expected array of events', ['status' => 400]);
        }

        global $wpdb;
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
}
```

- [ ] **Step 3: 在主文件中注册**

```php
require_once KAIRUI_TRACKER_DIR . 'includes/class-api.php';
Kairui_API::init();
```

- [ ] **Step 4: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: batch event tracking REST endpoint"
```

---

### Task 4: JS Tracker 前端埋点

**Files:**
- Create: `kairui-tracker/assets/tracker.js`
- Create: `kairui-tracker/includes/class-js-injector.php`

- [ ] **Step 1: 创建前端追踪脚本**

```javascript
(function() {
    'use strict';

    const API = '/wp-json/kairui/v1/track';
    const BATCH_INTERVAL = 5000;  // 5 seconds
    const BATCH_MAX = 20;         // or 20 events
    const HEARTBEAT = 15000;      // 15 seconds

    let queue = [];
    let sessionId = getCookie('kairui_sid');
    let lastPage = location.pathname;
    let scrollMarks = {25: false, 50: false, 75: false, 100: false};

    function getCookie(name) {
        const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return m ? m[2] : '';
    }

    function enqueue(type, data) {
        queue.push({ type, ...data, ts: Date.now() });
        if (queue.length >= BATCH_MAX) flush();
    }

    function flush() {
        if (!queue.length) return;
        const batch = queue.splice(0);
        navigator.sendBeacon(API, JSON.stringify(batch));
    }

    // Page view on load
    enqueue('page_view', { url: lastPage });

    // Product view detection
    if (document.querySelector('.product') || document.querySelector('[data-product_id]') || document.body.classList.contains('single-product')) {
        const pid = document.querySelector('[data-product_id]')?.dataset?.product_id
                 || document.querySelector('input[name="add-to-cart"]')?.value;
        const price = document.querySelector('.price .amount')?.textContent?.replace(/[^0-9.]/g, '');
        if (pid) enqueue('product_view', { product_id: parseInt(pid), product_price: parseFloat(price) || 0 });
    }

    // Scroll depth via IntersectionObserver
    const sentinel = document.createElement('div');
    sentinel.style.cssText = 'position:absolute;top:0;left:0;width:1px;pointer-events:none;';
    document.body.appendChild(sentinel);
    ['25','50','75','100'].forEach(pct => {
        sentinel.style.top = (document.body.scrollHeight * pct / 100) + 'px';
        const obs = new IntersectionObserver(([e]) => {
            if (e.isIntersecting && !scrollMarks[pct]) {
                scrollMarks[pct] = true;
                enqueue('scroll_' + pct, { url: lastPage });
            }
        });
        obs.observe(sentinel);
    });

    // Heartbeat for session duration
    setInterval(() => enqueue('heartbeat', { url: location.pathname }), HEARTBEAT);

    // Batch flush interval
    setInterval(flush, BATCH_INTERVAL);

    // Flush on page unload
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flush();
    });

    // Exit intent
    document.addEventListener('mouseleave', function once(e) {
        if (e.clientY < 0) {
            enqueue('exit_intent', { url: location.pathname });
            document.removeEventListener('mouseleave', once);
        }
    });
})();
```

- [ ] **Step 2: 创建 JS 注入器**

```php
<?php
class Kairui_JS_Injector {
    public static function init() {
        add_action('wp_enqueue_scripts', [__CLASS__, 'enqueue_tracker']);
    }

    public static function enqueue_tracker() {
        wp_enqueue_script(
            'kairui-tracker',
            plugins_url('assets/tracker.js', dirname(__FILE__)),
            [],
            KAIRUI_TRACKER_VERSION,
            true  // in footer
        );
        // Add async attribute
        add_filter('script_loader_tag', function ($tag, $handle) {
            if ($handle === 'kairui-tracker') {
                return str_replace(' src', ' async src', $tag);
            }
            return $tag;
        }, 10, 2);
    }
}
```

- [ ] **Step 3: 注册注入器**

在主文件中添加:
```php
require_once KAIRUI_TRACKER_DIR . 'includes/class-js-injector.php';
Kairui_JS_Injector::init();
```

- [ ] **Step 4: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: JS tracker with page view, scroll, heartbeat, exit intent"
```

---

### Task 5: WooCommerce 服务端事件钩子

**Files:**
- Modify: `kairui-tracker/includes/class-tracker.php`

- [ ] **Step 1: 添加 WooCommerce 事件钩子**

在 `Kairui_Tracker::init()` 中添加:

```php
// Only if WooCommerce is active
if (class_exists('WooCommerce')) {
    add_action('woocommerce_add_to_cart', [__CLASS__, 'on_add_to_cart'], 10, 6);
    add_action('woocommerce_remove_cart_item', [__CLASS__, 'on_remove_from_cart'], 10, 2);
    add_action('woocommerce_checkout_order_processed', [__CLASS__, 'on_order_processed'], 10, 3);
    add_action('woocommerce_order_refunded', [__CLASS__, 'on_refund'], 10, 2);
}
```

- [ ] **Step 2: 实现事件回调**

```php
public static function on_add_to_cart($cart_item_key, $product_id, $quantity, $variation_id, $variation, $cart_item_data) {
    $product = wc_get_product($product_id);
    self::record_event('add_to_cart', [
        'product_id' => $product_id,
        'product_price' => $product ? floatval($product->get_price()) : 0,
        'extra' => ['quantity' => $quantity],
    ]);
}

public static function on_remove_from_cart($cart_item_key, $cart) {
    $item = $cart->removed_cart_contents[$cart_item_key] ?? null;
    if ($item) {
        self::record_event('remove_from_cart', [
            'product_id' => $item['product_id'] ?? 0,
            'extra' => ['reason' => 'manual'],
        ]);
    }
}

public static function on_order_processed($order_id, $posted_data, $order) {
    self::record_event('purchase', [
        'order_id' => $order_id,
        'order_total' => floatval($order->get_total()),
        'extra' => [
            'items' => count($order->get_items()),
            'payment_method' => $order->get_payment_method(),
            'coupons' => $order->get_coupon_codes(),
        ],
    ]);

    // Write attribution record
    global $wpdb;
    $wpdb->replace("{$wpdb->prefix}kairui_order_attribution", [
        'order_id'     => $order_id,
        'source'       => self::get_source(),
        'session_id'   => self::get_session_id(),
        'landing_page' => sanitize_text_field($_COOKIE['kairui_landing'] ?? ''),
    ]);
}

public static function on_refund($order_id, $refund_id) {
    $order = wc_get_order($order_id);
    self::record_event('refund', [
        'order_id' => $order_id,
        'order_total' => floatval(get_post_meta($refund_id, '_refund_amount', true)),
    ]);
}
```

- [ ] **Step 3: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: WooCommerce hooks for add_to_cart, remove, purchase, refund"
```

---

### Task 6: 分析 API 端点

**Files:**
- Modify: `kairui-tracker/includes/class-api.php`

- [ ] **Step 1: 注册所有分析端点**

在 `register_routes()` 中添加:

```php
// Protected endpoints (require API key)
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
```

- [ ] **Step 2: 实现 summary 端点**

```php
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

private static function period_to_date($period) {
    switch ($period) {
        case 'today': return date('Y-m-d 00:00:00');
        case 'yesterday': return date('Y-m-d 00:00:00', strtotime('-1 day'));
        case '7d': return date('Y-m-d H:00:00', strtotime('-7 days'));
        case '30d': return date('Y-m-d H:00:00', strtotime('-30 days'));
        default: return date('Y-m-d H:00:00', strtotime('-7 days'));
    }
}
```

- [ ] **Step 3: 实现 funnel 端点**

```php
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
```

- [ ] **Step 4: 实现 trend 端点**

```php
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
```

- [ ] **Step 5: 实现 sessions 和时间线端点**

```php
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
    $sql = $wpdb->prepare("SELECT event_type, page_url, product_id, product_price, order_id, order_total, extra_data, created_at FROM $tbl WHERE session_id=%s ORDER BY created_at", $sid);
    $events = $wpdb->get_results($sql, ARRAY_A);
    if (!$events) return new WP_Error('not_found', 'Session not found', ['status' => 404]);
    $timeline = array_map(function($e) {
        return [
            'time' => date('H:i:s', strtotime($e['created_at'])),
            'event' => $e['event_type'],
            'url' => $e['page_url'],
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
```

- [ ] **Step 6: 实现 realtime 端点**

```php
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
    // Active carts in last 30 min
    $cart_cutoff = date('Y-m-d H:i:s', strtotime('-30 minutes'));
    $cart_sql = $wpdb->prepare("SELECT COUNT(DISTINCT session_id) FROM $tbl WHERE event_type='add_to_cart' AND created_at >= %s" . ($source ? " AND source=%s" : ""), $source ? [$cart_cutoff, $source] : [$cart_cutoff]);
    $active_carts = (int) $wpdb->get_var($cart_sql);
    return rest_ensure_response(['online_now' => $online, 'active_carts' => $active_carts]);
}
```

- [ ] **Step 4: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: analytics REST API endpoints (summary, funnel, trend, visitors, sessions)"
```

---

### Task 7: WP-Cron 小时汇总

**Files:**
- Create: `kairui-tracker/includes/class-aggregator.php`

- [ ] **Step 1: 创建汇总类**

```php
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
```

- [ ] **Step 2: 在主文件中注册**

```php
require_once KAIRUI_TRACKER_DIR . 'includes/class-aggregator.php';
Kairui_Aggregator::init();
```

- [ ] **Step 3: Commit**

```bash
git add kairui-tracker/
git commit -m "feat: hourly aggregation via WP-Cron + 90-day event cleanup"
```

---

## Phase 2: 凯瑞投流仪表盘重构

*(计划在 Phase 1 完成后制定)*
