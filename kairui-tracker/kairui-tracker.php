<?php
/**
 * Plugin Name: Kairui Mirror Tracker
 * Description: Tracks user behavior per mirror source for Kairui dashboard analytics.
 * Version: 1.0.0
 */

defined('ABSPATH') || exit;

define('KAIRUI_TRACKER_VERSION', '1.0.1');
define('KAIRUI_TRACKER_DIR', plugin_dir_path(__FILE__));

require_once KAIRUI_TRACKER_DIR . 'includes/class-schema.php';
require_once KAIRUI_TRACKER_DIR . 'includes/class-tracker.php';
require_once KAIRUI_TRACKER_DIR . 'includes/class-api.php';
require_once KAIRUI_TRACKER_DIR . 'includes/class-js-injector.php';
require_once KAIRUI_TRACKER_DIR . 'includes/class-aggregator.php';
Kairui_Tracker::init();
Kairui_API::init();
Kairui_JS_Injector::init();
Kairui_Aggregator::init();

register_activation_hook(__FILE__, ['Kairui_Schema', 'create_tables']);
register_deactivation_hook(__FILE__, ['Kairui_Schema', 'cleanup']);

// Generate API key on activation if not exists
register_activation_hook(__FILE__, function () {
    if (!get_option('kairui_api_key')) {
        update_option('kairui_api_key', wp_generate_password(32, false));
    }
});
