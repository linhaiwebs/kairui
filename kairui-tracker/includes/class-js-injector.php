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
            true
        );
        add_filter('script_loader_tag', function ($tag, $handle) {
            if ($handle === 'kairui-tracker') {
                return str_replace(' src', ' async src', $tag);
            }
            return $tag;
        }, 10, 2);
    }
}
