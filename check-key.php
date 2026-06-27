<?php
$db = new mysqli('localhost','wpuser','wp_password_2024','wordpress');
foreach([2,4,5,6,7] as $bid){
  $r = $db->query("SELECT option_value FROM wp_{$bid}_options WHERE option_name='kairui_api_key'");
  $v = $r->fetch_row();
  echo 'Blog '.$bid.': '.($v[0] ? substr($v[0],0,30).'...' : 'NOT SET').PHP_EOL;
}
