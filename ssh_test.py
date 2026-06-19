import paramiko, sys
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    c.connect('104.243.33.139', 22, 'root', 'A52290120a', timeout=10)
    _, o, _ = c.exec_command('uname -a')
    print('OK:', o.read().decode())
    c.close()
except Exception as e:
    print(f'FAIL: {e}')
    sys.exit(1)
