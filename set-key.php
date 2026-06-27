<?php
$db = new mysqli('localhost','wpuser','wp_password_2024','wordpress');
$key = '3p1AWlniZSKsUCkdtKc6itUGwwkHyBeX';
foreach([2,4,5,6,7] as $bid){
  $db->query("INSERT INTO wp_{$bid}_options (option_name, option_value, autoload) VALUES ('kairui_api_key','$key','yes') ON DUPLICATE KEY UPDATE option_value='$key'");
  echo "Blog $bid: key set\n";
}
foreach([2,4,5,6,7] as $bid){
  $r = $db->query("SELECT option_value FROM wp_{$bid}_options WHERE option_name='kairui_api_key'");
  $v = $r->fetch_row();
  echo "Blog $bid verify: " . ($v[0] ? substr($v[0],0,20).'...' : 'FAILED') . "\n";
}
