<?php
/**
 * Serve feed XML files for mirror sites via the target WooCommerce site.
 * Kairui backend uploads feeds here; Worker proxies requests and the plugin
 * returns the correct feed for each mirror domain via X-Forwarded-Host header.
 */
class Kairui_Feed_Server {
    const OPTION_PREFIX = 'kairui_feed_';

    public static function init() {
        add_action('parse_request', [__CLASS__, 'intercept']);
        add_action('rest_api_init', [__CLASS__, 'register_api']);
    }

    public static function intercept($wp) {
        $uri = $_SERVER['REQUEST_URI'] ?? '';
        $path = parse_url($uri, PHP_URL_PATH);
        if (!$path || (!str_ends_with($path, '/feedstart.xml') && !str_ends_with($path, '/feed.xml'))) {
            return;
        }

        $domain = sanitize_text_field($_SERVER['HTTP_X_FORWARDED_HOST'] ?? $_SERVER['HTTP_HOST'] ?? '');
        if (!$domain) { status_header(400); die('Missing domain'); }

        $is_feed_xml = str_ends_with($path, '/feed.xml');
        $key = self::OPTION_PREFIX . ($is_feed_xml ? 'feed_' : '') . $domain;
        $content = get_option($key, '');

        if (!$content) { status_header(404); die('Feed not found: '.$domain); }

        status_header(200);
        header('Content-Type: application/xml; charset=utf-8');
        header('X-Robots-Tag: noindex');
        echo $content;
        exit;
    }

    public static function register_api() {
        register_rest_route('kairui/v1', '/feed/upload', [
            'methods' => 'POST',
            'callback' => [__CLASS__, 'handle_upload'],
            'permission_callback' => [__CLASS__, 'check_key'],
        ]);
    }

    public static function check_key($request) {
        $key = $request->get_header('X-Kairui-Key');
        return $key && hash_equals(get_option('kairui_api_key', ''), $key);
    }

    public static function handle_upload($request) {
        $domain = sanitize_text_field($request->get_param('domain') ?? '');
        $content = $request->get_param('content') ?? '';
        $type = sanitize_text_field($request->get_param('type') ?? 'feedstart');

        if (!$domain || !$content) {
            return new WP_Error('invalid', 'domain and content required', ['status' => 400]);
        }

        $key = self::OPTION_PREFIX . $domain;
        if ($type === 'feed') {
            $key = self::OPTION_PREFIX . 'feed_' . $domain;
        }

        update_option($key, $content);
        return rest_ensure_response(['ok' => true, 'domain' => $domain, 'size' => strlen($content)]);
    }
}
