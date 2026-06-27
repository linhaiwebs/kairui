#!/bin/bash
for blog in 4 5 6 7; do
  echo "Blog $blog:"
  mysql -u wpuser -pwp_password_2024 wordpress -N -s -e "INSERT IGNORE INTO wp_${blog}_options (option_name, option_value, autoload) SELECT option_name, option_value, autoload FROM wp_2_options WHERE option_name LIKE '%woodmart%'"
  mysql -u wpuser -pwp_password_2024 wordpress -N -s -e "INSERT IGNORE INTO wp_${blog}_options (option_name, option_value, autoload) SELECT 'theme_mods_woodmart', option_value, autoload FROM wp_2_options WHERE option_name='theme_mods_woodmart'"
  COUNT=$(mysql -u wpuser -pwp_password_2024 wordpress -N -s -e "SELECT COUNT(*) FROM wp_${blog}_options WHERE option_name LIKE '%woodmart%'")
  echo "  Woodmart options: $COUNT"
done
