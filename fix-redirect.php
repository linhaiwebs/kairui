<?php
/**
 * Plugin Name: Fix Multisite Redirect Loop
 * Description: Disable canonical redirects for domain-mapped multisite subsites
 */

// Don't canonical redirect - let multisite handle it
remove_filter('template_redirect', 'redirect_canonical');

// Also ensure multisite uses the correct domain from the request
add_filter('pre_option_home', function($value) {
    if (is_multisite() && !is_main_site()) {
        return 'https://' . $_SERVER['HTTP_HOST'];
    }
    return $value;
}, 99);

add_filter('pre_option_siteurl', function($value) {
    if (is_multisite() && !is_main_site()) {
        return 'https://' . $_SERVER['HTTP_HOST'];
    }
    return $value;
}, 99);
