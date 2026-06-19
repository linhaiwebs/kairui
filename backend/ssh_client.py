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
        self._web_service = None

    def connect(self):
        if self._ssh: return
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        import os
        if os.path.isfile(self.key_file) and os.path.getsize(self.key_file) > 0:
            try:
                self._ssh.connect(self.host, self.port, self.username, key_filename=self.key_file, timeout=15, look_for_keys=False, allow_agent=False)
            except Exception:
                if self.password:
                    self._ssh.connect(self.host, self.port, self.username, self.password, timeout=15)
                else:
                    raise
        elif self.password:
            self._ssh.connect(self.host, self.port, self.username, self.password, timeout=15)
        else:
            self._ssh.connect(self.host, self.port, self.username, timeout=15, look_for_keys=True)
        self._sftp = self._ssh.open_sftp()

    def close(self):
        if self._sftp: self._sftp.close()
        if self._ssh: self._ssh.close()
        self._ssh = None; self._sftp = None

    def test_connection(self):
        self.connect()
        _, stdout, _ = self._ssh.exec_command("echo ok", timeout=10)
        return stdout.read().decode().strip()

    def _detect_web_service(self):
        """Detect which web server is installed (openresty or nginx)."""
        if self._web_service:
            return self._web_service
        _, stdout, _ = self._ssh.exec_command("systemctl is-active openresty 2>/dev/null || echo ''", timeout=10)
        if stdout.read().decode().strip() == "active":
            self._web_service = "openresty"
            return self._web_service
        _, stdout, _ = self._ssh.exec_command("systemctl is-active nginx 2>/dev/null || echo ''", timeout=10)
        if stdout.read().decode().strip() == "active":
            self._web_service = "nginx"
            return self._web_service
        # Check which is installed
        _, stdout, _ = self._ssh.exec_command("which openresty 2>/dev/null && echo 'found' || echo ''", timeout=10)
        if "found" in stdout.read().decode():
            self._web_service = "openresty"
        else:
            self._web_service = "nginx"
        return self._web_service

    def server_init(self):
        self.connect()
        results = []

        # Install prerequisites
        for cmd in [
            "apt update",
            "apt install -y wget gnupg2 ca-certificates curl",
        ]:
            _, stdout, stderr = self._ssh.exec_command(cmd, timeout=120)
            err = stderr.read().decode().strip()
            status = "OK" if not err or "already" in err.lower() or "newest" in err.lower() else "ERR"
            results.append({"cmd": cmd[:60], "status": status, "error": err[:200] if err else ""})

        # Try OpenResty first, fall back to nginx (Debian 13+)
        web_installed = False
        try:
            # Build OpenResty install command carefully
            install_cmd = (
                "wget -qO - https://openresty.org/package/pubkey.gpg | apt-key add - && "
                "echo 'deb http://openresty.org/package/debian '$(lsb_release -sc)' openresty' "
                "> /etc/apt/sources.list.d/openresty.list && "
                "apt update && apt install -y openresty 2>&1"
            )
            _, stdout, stderr = self._ssh.exec_command(install_cmd, timeout=120)
            err = stderr.read().decode().strip()
            out = stdout.read().decode().strip()
            if "Unable to locate package" in (err + out) or "E:" in err:
                raise Exception("OpenResty not available")
            web_installed = True
            self._web_service = "openresty"
            results.append({"cmd": "Install OpenResty", "status": "OK"})
        except Exception as e:
            # Fallback to nginx
            _, stdout, stderr = self._ssh.exec_command("apt install -y nginx 2>&1", timeout=120)
            err = stderr.read().decode().strip()
            out = stdout.read().decode().strip()
            if "E:" in err and "Unable to locate" in err:
                results.append({"cmd": "Install nginx", "status": "ERR", "error": err[:200]})
            else:
                web_installed = True
                self._web_service = "nginx"
                results.append({"cmd": "Install nginx (fallback)", "status": "OK"})

        if not web_installed:
            results.append({"cmd": "Web server", "status": "ERR", "error": "Neither OpenResty nor nginx could be installed"})
            return results

        # Create directory structure
        for cmd in [
            "mkdir -p /www/sites /www/conf.d /www/logs",
        ]:
            _, stdout, stderr = self._ssh.exec_command(cmd, timeout=10)
            err = stderr.read().decode().strip()
            results.append({"cmd": cmd[:60], "status": "OK" if not err else "ERR"})

        # Configure nginx to include /www/conf.d
        if self._web_service == "openresty":
            nginx_conf = "/usr/local/openresty/nginx/conf/nginx.conf"
        else:
            nginx_conf = "/etc/nginx/nginx.conf"

        add_include = (
            "grep -q '/www/conf.d' " + nginx_conf + " || "
            "sed -i 's|^http {|http {\\n    include /www/conf.d/*.conf;|' " + nginx_conf
        )
        _, stdout, stderr = self._ssh.exec_command(add_include, timeout=10)
        err = stderr.read().decode().strip()
        results.append({"cmd": "Configure nginx include", "status": "OK" if not err else "ERR", "error": err[:100] if err else ""})

        # Enable and start service
        svc = self._web_service
        _, stdout, stderr = self._ssh.exec_command(
            "systemctl enable " + svc + " && systemctl start " + svc + " 2>&1", timeout=30)
        err = stderr.read().decode().strip()
        results.append({"cmd": "Start " + svc, "status": "OK" if not err or "already" in err.lower() else "ERR"})

        # Test config
        test_cmd = "openresty -t 2>&1" if svc == "openresty" else "nginx -t 2>&1"
        _, stdout, stderr = self._ssh.exec_command(test_cmd, timeout=10)
        out = (stderr.read().decode() + stdout.read().decode()).strip()
        if "ok" in out.lower() or "successful" in out.lower():
            self._ssh.exec_command("systemctl reload " + svc, timeout=10)
            results.append({"cmd": "Config test", "status": "OK"})

        # Verify port 80
        _, stdout, _ = self._ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost/", timeout=10)
        code = stdout.read().decode().strip()
        results.append({"cmd": "Verify port 80", "status": "OK" if code else "WARN", "error": "HTTP " + str(code) if code else ""})

        return results

    def reload_nginx(self):
        self.connect()
        svc = self._detect_web_service()
        self._ssh.exec_command("systemctl reload " + svc, timeout=30)

    def mkdir_p(self, path):
        self.connect()
        self._ssh.exec_command("mkdir -p " + path, timeout=10)

    def write_file(self, path, content):
        self.connect()
        import os
        parent = os.path.dirname(path)
        if parent: self._ssh.exec_command("mkdir -p " + parent, timeout=10)
        with self._sftp.open(path, 'w') as f: f.write(content)

    def read_file(self, path):
        self.connect()
        try:
            with self._sftp.open(path, 'r') as f: return f.read().decode('utf-8')
        except FileNotFoundError: return None

    def delete_file(self, path):
        self.connect()
        self._ssh.exec_command("rm -rf " + path, timeout=10)


_ssh_pool = {}
def get_ssh_client(host, port=22, password=''):
    key = (host, port)
    if key not in _ssh_pool:
        _ssh_pool[key] = SSHClient(host, port, 'root', password)
    return _ssh_pool[key]
