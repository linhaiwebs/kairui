<?php
class Kairui_Tracker {
    const COOKIE_SOURCE = 'kairui_src';
    const COOKIE_SESSION = 'kairui_sid';
    const SESSION_TTL = 1800; // 30 minutes

    public static function init() {
        add_action('init', [__CLASS__, 'set_source_cookie']);

        // WooCommerce hooks (only if WC is active)
        if (class_exists('WooCommerce')) {
            add_action('woocommerce_add_to_cart', [__CLASS__, 'on_add_to_cart'], 10, 6);
            add_action('woocommerce_remove_cart_item', [__CLASS__, 'on_remove_from_cart'], 10, 2);
            add_action('woocommerce_checkout_order_processed', [__CLASS__, 'on_order_processed'], 10, 3);
            add_action('woocommerce_order_refunded', [__CLASS__, 'on_refund'], 10, 2);
        }
    }

    public static function set_source_cookie() {
        $source = '';
        if (!empty($_SERVER['HTTP_X_FORWARDED_HOST'])) {
            $source = sanitize_text_field($_SERVER['HTTP_X_FORWARDED_HOST']);
        }

        if (!$source && !empty($_SERVER['HTTP_REFERER'])) {
            $host = parse_url($_SERVER['HTTP_REFERER'], PHP_URL_HOST);
            if ($host && $host !== $_SERVER['HTTP_HOST']) {
                $source = $host;
            }
        }

        if ($source && empty($_COOKIE[self::COOKIE_SOURCE])) {
            setcookie(self::COOKIE_SOURCE, $source, time() + 7776000, '/', '', true, false);
            $_COOKIE[self::COOKIE_SOURCE] = $source;
        }

        if (empty($_COOKIE[self::COOKIE_SESSION])) {
            $sid = wp_generate_uuid4();
            setcookie(self::COOKIE_SESSION, $sid, time() + self::SESSION_TTL, '/', '', true, false);
            $_COOKIE[self::COOKIE_SESSION] = $sid;
        } else {
            setcookie(self::COOKIE_SESSION, $_COOKIE[self::COOKIE_SESSION], time() + self::SESSION_TTL, '/', '', true, false);
        }
    }

    public static function get_source() {
        return sanitize_text_field($_COOKIE[self::COOKIE_SOURCE] ?? 'direct');
    }

    public static function get_session_id() {
        return sanitize_text_field($_COOKIE[self::COOKIE_SESSION] ?? '');
    }

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

    // WooCommerce hooks
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

        global $wpdb;
        $wpdb->replace("{$wpdb->prefix}kairui_order_attribution", [
            'order_id'     => $order_id,
            'source'       => self::get_source(),
            'session_id'   => self::get_session_id(),
            'landing_page' => sanitize_text_field($_COOKIE['kairui_landing'] ?? ''),
        ]);
    }

    public static function on_refund($order_id, $refund_id) {
        self::record_event('refund', [
            'order_id' => $order_id,
            'order_total' => floatval(get_post_meta($refund_id, '_refund_amount', true)),
        ]);
    }
}
