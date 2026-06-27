import paramiko, time

t = paramiko.Transport(('104.243.35.121', 22))
t.connect(username='root', password='9xXFXWk8oX2T0fK')

# 1. Get blog IDs
c = t.open_session()
c.exec_command('mysql -u wpuser -pwp_password_2024 -D wordpress -e "SELECT blog_id, domain FROM wp_blogs ORDER BY blog_id" -N -s')
time.sleep(2)
blogs = c.recv(8192).decode().strip().split('\n')
print("Blogs found:")
for b in blogs:
    if b.strip():
        parts = b.strip().split('\t')
        if len(parts) >= 2:
            print(f"  blog_id={parts[0]}, domain={parts[1]}")

# 2. Check Woodmart CSS directories
c2 = t.open_session()
c2.exec_command('for d in /var/www/html/wp-content/uploads/sites/*/woodmart; do echo "$d: $(ls $d 2>/dev/null | wc -l) files"; done')
time.sleep(1)
print("\nWoodmart CSS dirs:\n" + c2.recv(8192).decode())

# 3. Create woodmart directories for missing subsites
c3 = t.open_session()
c3.exec_command('''
# Find wayfelr.com blog_id (2) and copy its woodmart dir to other subsites
SRC=/var/www/html/wp-content/uploads/sites/2
if [ -d "$SRC/woodmart" ]; then
  for blog in 3 4 5 6 7 8 9; do
    DST=/var/www/html/wp-content/uploads/sites/$blog
    if [ -d "$DST" ]; then
      cp -r $SRC/woodmart $DST/ 2>/dev/null && echo "Copied woodmart to blog $blog" || echo "Blog $blog: failed"
    fi
  done
else
  echo "Source woodmart dir not found at $SRC/woodmart"
fi
''')
time.sleep(2)
print("\nCopy result:\n" + c3.recv(8192).decode())

# 4. Set correct permissions
c4 = t.open_session()
c4.exec_command('chown -R www-data:www-data /var/www/html/wp-content/uploads/sites/')
time.sleep(1)
c4.recv(4096)

c.close(); c2.close(); c3.close(); c4.close()
t.close()
print("\nDone")
