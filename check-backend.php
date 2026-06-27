<?php
$db = new mysqli('localhost','wpuser','wp_password_2024','wordpress');
foreach([2,4,5,6,7] as $bid){
  $r = $db->query("SELECT option_value FROM wp_{$bid}_options WHERE option_name='woocommerce_wc_card_gateway_settings'");
  $v = $r->fetch_assoc();
  if($v && $v['option_value']){
    $s = unserialize($v['option_value']);
    $url = $s['backend_url'] ?? 'NOT_SET';
    echo "Blog $bid backend_url: $url\n";
  } else {
    echo "Blog $bid: no settings\n";
  }
}
