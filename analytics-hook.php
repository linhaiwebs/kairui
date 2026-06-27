<?php
/**
 * Plugin Name: Kairui Analytics Hook
 * Description: Forward payment/checkout data to kairui analytics when sending to wp.lhwebs.com
 */

// Intercept outgoing HTTP requests to wp.lhwebs.com
add_action('http_api_debug', function($response, $context, $class, $parsed_args, $url) {
    // Only intercept requests to the payment backend
    if (strpos($url, 'wp.lhwebs.com') === false) return;

    // Only forward successful responses
    if (is_wp_error($response)) return;

    $body = wp_remote_retrieve_body($response);
    $data = json_decode($body, true);
    if (!$data) return;

    // Forward to our analytics (non-blocking, async)
    $analytics_url = 'https://ads.lhwebs.com/api/analytics/payment-event';
    $forward = [
        'site_url' => home_url(),
        'event_type' => strpos($url, 'complete-order') !== false ? 'order_completed' : 'payment_update',
        'timestamp' => current_time('mysql'),
        'data' => $data,
        'order_id' => $data['order_id'] ?? ($data['data']['order_id'] ?? null),
        'amount' => $data['order_total'] ?? ($data['data']['order_total'] ?? $data['total'] ?? null),
    ];

    wp_remote_post($analytics_url, [
        'body' => json_encode($forward),
        'headers' => ['Content-Type' => 'application/json', 'X-Kairui-Event' => 'payment'],
        'timeout' => 3,
        'blocking' => false,  // async, don't slow down payment
    ]);
}, 10, 5);

// Also capture WooCommerce order completion
add_action('woocommerce_order_status_completed', function($order_id) {
    $order = wc_get_order($order_id);
    if (!$order) return;

    $forward = [
        'site_url' => home_url(),
        'event_type' => 'order_completed',
        'timestamp' => current_time('mysql'),
        'order_id' => $order_id,
        'amount' => $order->get_total(),
        'currency' => $order->get_currency(),
        'items_count' => count($order->get_items()),
        'payment_method' => $order->get_payment_method(),
        'customer_city' => $order->get_billing_city(),
        'customer_country' => $order->get_billing_country(),
    ];

    wp_remote_post('https://ads.lhwebs.com/api/analytics/payment-event', [
        'body' => json_encode($forward),
        'headers' => ['Content-Type' => 'application/json', 'X-Kairui-Event' => 'order'],
        'timeout' => 3,
        'blocking' => false,
    ]);
});
