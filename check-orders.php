<?php
$db = new mysqli('localhost','wpuser','wp_password_2024','wordpress');

echo "=== Failed/Cancelled Orders ===\n";
foreach ([2=>'wayfelr',4=>'wayfallr',5=>'wayfellr',6=>'wayfoilr',7=>'wayfcir'] as $bid => $name) {
    $r = $db->query("SELECT COUNT(*) FROM wp_{$bid}_posts WHERE post_type='shop_order' AND post_status IN ('wc-pending','wc-failed','wc-cancelled')");
    echo "  $name: " . $r->fetch_row()[0] . " failed\n";
}

echo "\n=== All Order Statuses ===\n";
foreach ([2=>'wayfelr',4=>'wayfallr',5=>'wayfellr',6=>'wayfoilr',7=>'wayfcir'] as $bid => $name) {
    $r = $db->query("SELECT post_status, COUNT(*) c FROM wp_{$bid}_posts WHERE post_type='shop_order' GROUP BY post_status");
    $statuses = [];
    while ($row = $r->fetch_assoc()) $statuses[] = $row['post_status'].':'.$row['c'];
    echo "  $name: " . ($statuses ? implode(', ', $statuses) : '0 orders') . "\n";
}

echo "\n=== Checkout Attempts (kairui tracker) ===\n";
foreach ([2=>'wayfelr',4=>'wayfallr',5=>'wayfellr',6=>'wayfoilr',7=>'wayfcir'] as $bid => $name) {
    $t = "wp_{$bid}_kairui_events";
    $exists = $db->query("SELECT 1 FROM information_schema.tables WHERE table_name='$t'")->num_rows;
    if ($exists) {
        $r = $db->query("SELECT COUNT(*) FROM $t WHERE event_type='checkout'");
        echo "  $name: " . $r->fetch_row()[0] . " checkouts tracked\n";
    } else {
        echo "  $name: no tracker table\n";
    }
}
