import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, w=3):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(w)
    out = c.recv(4096).decode(errors='replace')
    c.close()
    return out.strip()

# Add post_name index for all 5 subsites
for bid in [2, 4, 5, 6, 7]:
    sql = 'mysql -u wpuser -pwp_password_2024 wordpress -e "CREATE INDEX idx_post_name ON wp_' + str(bid) + '_posts(post_name(191))" 2>&1'
    r = run(sql, 3)
    print('Blog ' + str(bid) + ': ' + r[:80])

# Test
print()
print('=== Test product page ===')
test_cmd = 'curl -s -o /dev/null -w "%{http_code}:%{time_total}s" --max-time 10 "http://localhost/product/pura-hypoallergenic-diapers-size-1-4-11-lbs-newborn-totally-chlorine-free-wetness-indicator-suitable-for-sensitive-skin-soft-organic-cotton-comfort-overnight-1-pack-of-32-baby-diapers/" -H "Host: wayfelr.com"'
print(run(test_cmd, 10))

t.close()
