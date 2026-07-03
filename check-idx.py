import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

def run(cmd, w=2):
    c = t.open_session()
    c.exec_command(cmd)
    time.sleep(w)
    out = c.recv(4096).decode(errors='replace')
    c.close()
    return out.strip()

# Check blogs
r = run('mysql -u wpuser -pwp_password_2024 wordpress -N -s -e "SELECT blog_id,domain FROM wp_blogs ORDER BY blog_id" 2>/dev/null', 2)
print("Blogs:\n" + r)

# Check post_name index for each blog
print("\nPost_name indexes:")
for bid in [2, 4, 5, 6, 7]:
    r = run('mysql -u wpuser -pwp_password_2024 wordpress -N -s -e "SHOW INDEX FROM wp_' + str(bid) + '_posts WHERE Key_name LIKE \'%post_name%\'" 2>/dev/null', 2)
    print("Blog " + str(bid) + ": " + ('HAS INDEX' if r else 'NO INDEX'))

t.close()
