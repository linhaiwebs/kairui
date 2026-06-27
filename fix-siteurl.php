<?php
$db = new mysqli('localhost', 'wpuser', 'wp_password_2024', 'wordpress');

$sites = [
    ['blog_id' => 2, 'domain' => 'wayfelr.com'],
    ['blog_id' => 4, 'domain' => 'wayfallr.com'],
    ['blog_id' => 5, 'domain' => 'wayfellr.com'],
    ['blog_id' => 6, 'domain' => 'wayfoilr.com'],
    ['blog_id' => 7, 'domain' => 'wayfcir.com'],
];

foreach ($sites as $site) {
    $blog_id = $site['blog_id'];
    $domain = $site['domain'];
    $url = 'https://' . $domain;

    // Update wp_{blog_id}_options
    $db->query("UPDATE wp_{$blog_id}_options SET option_value='$url' WHERE option_name='siteurl'");
    $db->query("UPDATE wp_{$blog_id}_options SET option_value='$url' WHERE option_name='home'");

    echo "Blog $blog_id ($domain): siteurl/home set to $url\n";
}

echo "Done\n";
