<?php
$db = new mysqli('localhost','wpuser','wp_password_2024','wordpress');

echo "=== wp_blogs ===\n";
$r = $db->query('SELECT blog_id, domain, path FROM wp_blogs ORDER BY blog_id');
while($row = $r->fetch_assoc()) echo $row['blog_id'].' '.$row['domain'].$row['path']."\n";

foreach ([2,4,5,6,7] as $bid) {
    echo "\n=== Blog $bid siteurl/home ===\n";
    $r = $db->query("SELECT option_name, option_value FROM wp_{$bid}_options WHERE option_name IN ('siteurl','home')");
    while($row = $r->fetch_assoc()) echo $row['option_name'].'='.$row['option_value']."\n";
}
