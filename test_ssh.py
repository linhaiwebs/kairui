import paramiko
k = paramiko.SSHClient()
k.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    k.connect("134.209.166.127", 22, "root", key_filename="/root/.ssh/id_ed25519", timeout=15, look_for_keys=False, allow_agent=False)
    print("OK")
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:100])
k.close()
