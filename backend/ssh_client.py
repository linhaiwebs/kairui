"""SSH client for direct server file operations (replaces 1Panel API)."""
import paramiko
import logging

logger = logging.getLogger(__name__)

class SSHClient:
    def __init__(self, host, port=22, username='root', password=None, key_file=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_file = key_file or '/root/.ssh/id_ed25519'
        self._ssh = None
        self._sftp = None

    def connect(self):
        if self._ssh: return
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        import os
        if os.path.isfile(self.key_file):
            try:
                k = paramiko.Ed25519Key.from_private_key_file(self.key_file)
                self._ssh.connect(self.host, self.port, self.username, pkey=k, timeout=15, look_for_keys=False, allow_agent=False)
            except Exception:
                # Fallback: try RSA key
                try:
                    k = paramiko.RSAKey.from_private_key_file(self.key_file)
                    self._ssh.connect(self.host, self.port, self.username, pkey=k, timeout=15, look_for_keys=False, allow_agent=False)
                except Exception:
                    raise
        elif self.password:
            self._ssh.connect(self.host, self.port, self.username, self.password, timeout=15)
        else:
            # Try system SSH agent
            self._ssh.connect(self.host, self.port, self.username, timeout=15)
        self._sftp = self._ssh.open_sftp()

    def close(self):
        if self._sftp: self._sftp.close()
        if self._ssh: self._ssh.close()
        self._ssh = None; self._sftp = None

    def test_connection(self):
        self.connect()
        _, stdout, _ = self._ssh.exec_command("echo ok", timeout=10)
        return stdout.read().decode().strip()

    def server_init(self):
        self.connect()
        commands = [
            "apt update",
            "apt install -y wget gnupg2 ca-certificates",
            "wget -qO - https://openresty.org/package/pubkey.gpg | apt-key add -",
            'echo "deb http://openresty.org/package/debian $(lsb_release -sc) openresty" > /etc/apt/sources.list.d/openresty.list',
            "apt update",
            "apt install -y openresty",
            "systemctl enable openresty",
            "systemctl start openresty",
            "mkdir -p /www/sites /www/conf.d /www/logs",
            'grep -q "include /www/conf.d" /usr/local/openresty/nginx/conf/nginx.conf || sed -i "/^http {/a \\    include /www/conf.d/*.conf;" /usr/local/openresty/nginx/conf/nginx.conf',
            "openresty -t && systemctl reload openresty",
        ]
        results = []
        for cmd in commands:
            _, stdout, stderr = self._ssh.exec_command(cmd, timeout=60)
            err = stderr.read().decode().strip()
            status = "OK" if not err or "already" in err.lower() or "newest" in err.lower() else "ERR"
            results.append({"cmd": cmd[:60], "status": status, "error": err[:200] if status == "ERR" else ""})
        _, stdout, _ = self._ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost/", timeout=10)
        results.append({"cmd": "Verify port 80", "status": "OK" if stdout.read().decode().strip() else "WARN"})
        return results

    def reload_nginx(self):
        self.connect()
        self._ssh.exec_command("systemctl reload openresty", timeout=30)

    def mkdir_p(self, path):
        self.connect()
        self._ssh.exec_command(f"mkdir -p {path}", timeout=10)

    def write_file(self, path, content):
        self.connect()
        import os
        parent = os.path.dirname(path)
        if parent: self._ssh.exec_command(f"mkdir -p {parent}", timeout=10)
        with self._sftp.open(path, 'w') as f: f.write(content)

    def read_file(self, path):
        self.connect()
        try:
            with self._sftp.open(path, 'r') as f: return f.read().decode('utf-8')
        except FileNotFoundError: return None

    def delete_file(self, path):
        self.connect()
        self._ssh.exec_command(f"rm -rf {path}", timeout=10)


_ssh_pool = {}
def get_ssh_client(host, port=22, password=''):
    key = (host, port)
    if key not in _ssh_pool:
        _ssh_pool[key] = SSHClient(host, port, 'root', password)
    return _ssh_pool[key]
