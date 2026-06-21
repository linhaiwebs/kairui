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
