# 1Panel API → 直连 SSH 迁移设计

## 目标

移除所有 1Panel API 依赖，改用 paramiko SSH 直连 Debian 服务器，进行静态站文件管理。

## 不影响的部分

- 前端 UI（站点列表、创建向导、镜像向导、产品/Feed管理）完全不变
- 数据库结构（sites、panel_environments 等）基本不变
- Cloudflare DNS / Worker 操作不变
- 品牌套件、代理池、Google 账户管理不变
- 运营隔离逻辑不变

## 数据库改动

### `panel_environments` 表

新增列：
```sql
ALTER TABLE panel_environments ADD COLUMN ssh_password TEXT DEFAULT '';
ALTER TABLE panel_environments ADD COLUMN ssh_initialized INTEGER DEFAULT 0;
```

- `host` — Debian 服务器 IP
- `port` — SSH 端口（默认 22）
- `ssh_password` — SSH 密码
- `ssh_initialized` — 0=未初始化，1=已完成部署前准备
- `api_key` — 废弃（旧 1Panel 数据保留不删，兼容历史记录）

## 系统设置 UI 改动

### "1Panel 环境"标签 → "服务器环境"标签

列表每项显示：
- 环境名称
- 主机:端口
- SSH 状态指示器：🟢 已连接 / 🔴 无法连接 / ⏳ 未初始化
- CF 账户绑定（不变）
- 操作：编辑 / 初始化 / 测试连接 / 删除

### 新增/编辑环境表单

| 字段 | 类型 | 必填 |
|------|------|------|
| 名称 | 文本 | ✅ |
| 主机 IP | 文本 | ✅ |
| SSH 端口 | 数字(默认22) | ✅ |
| SSH 密码 | 密码 | ✅ |
| 关联 CF 账户 | 下拉 | 可选 |

### 初始化功能

点击"初始化"→ `POST /api/server/init` → SSH 连接 → 全自动部署：

```bash
# 1. 安装 OpenResty
apt update && apt install -y wget gnupg2 ca-certificates
wget -O - https://openresty.org/package/pubkey.gpg | apt-key add -
echo "deb http://openresty.org/package/debian $(lsb_release -sc) openresty" > /etc/apt/sources.list.d/openresty.list
apt update && apt install -y openresty
systemctl enable openresty && systemctl start openresty

# 2. 创建站点目录结构
mkdir -p /www/sites /www/conf.d /www/logs

# 3. 配置 openresty 主配置加载 conf.d
# 在 http {} 块末尾插入 include /www/conf.d/*.conf;
sed -i '/^http {/a \    include /www/conf.d/*.conf;' /usr/local/openresty/nginx/conf/nginx.conf

# 4. 验证配置并重载
openresty -t && systemctl reload openresty

# 5. 确认端口 80 监听
echo "OK: $(curl -s -o /dev/null -w '%{http_code}' http://localhost/)"
```

整个过程无人值守，3 分钟内完成。初始化成功 → `ssh_initialized = 1`，环境卡片显示 🟢 已就绪。

### 测试连接

点击"测试连接" → SSH 连接 → `echo ok` → 返回结果，更新状态指示器。

## 新增文件

### `backend/ssh_client.py`

```python
class SSHClient:
    def __init__(self, host, port=22, username='root', password=None)
    def connect(self)
    def mkdir_p(self, path)              # mkdir -p
    def write_file(self, path, content) # SFTP put
    def read_file(self, path)           # SFTP get
    def delete_file(self, path)         # rm -rf
    def file_exists(self, path)         # test -e
    def reload_nginx(self)              # systemctl reload openresty
    def close(self)
```

连接池：每个 SSHClient 实例按 `(host, port)` 缓存，复用 SFTP 连接。

## 替换清单：routes.py 内 1Panel 调用 → SSHClient

| 函数 | 替换的 1Panel 调用 | SSH 等效 |
|------|-------------------|---------|
| `_bg_deploy_static` | `create_static_website` | `mkdir_p(site_dir)` |
| | `create_file`/`save_file`/`delete_file` | `write_file`/`delete_file` |
| | `reload_openresty` | `reload_nginx` |
| `_regenerate_static_site_html` | `create_file`/`save_file`/`delete_file` | 同上 |
| | `reload_openresty` | `reload_nginx` |
| `_sync_feed_to_static_site` | `upload_static_site_files` | `write_file` |
| | `reload_openresty` | `reload_nginx` |
| `_clean_feed_from_static_site` | `upload_static_site_files`(空) | `write_file`(空feed) |
| `remove_site` (静态站) | `delete_website` + `delete_nginx_proxy_config` | `rm -rf site_dir` + `rm nginx_conf` |
| `_nginx_fix_body_size` | `read_file`/`save_file` | `read_file`/`write_file` |
| Meta 标签注入 | `read_file`/`delete_file`/`create_file`/`save_file` | 同上 |

## 删除的代码

### 前端
- `panelConnected` 状态变量 → 改为 `sshConnected[envId]`
- 顶部"1Panel 已连接"指示器 → "服务器: 名称 🟢"
- 站点列表"同步1Panel"按钮 → 删除
- 侧边栏"统计总览"菜单 → 删除
- 创建向导中"1Panel已连接"提示 → 删除
- 系统设置：`panel` 标签 → `server` 标签（UI 重写）

### 后端
- `panel_client.py` — 整个文件可删除
- `routes.py` 中所有 `_get_panel_client()` / `OnePanelClient(...)` 调用
- `routes.py` 中 WordPress 部署相关函数（`_bg_deploy_inner` 等）
- `/api/panel/*` 路由 → 替换为 `/api/server/init` + `/api/server/test`

## 服务器要求

Debian 纯净环境，只需 SSH 已开启（`systemctl start ssh`）。其余全部由初始化功能自动完成。

## 验证

1. 运营登录 → 批量创建站点 → 站点文件出现在目标服务器 `/www/sites/{domain}/index/`
2. 访问站点域名 → 显示商城页面
3. 网站产品同步 → 站点 HTML 重新生成
4. Feed 创建/清理 → feed.xml 更新
5. 镜像向导 → Worker 创建（不涉及 SSH）
6. 站点删除 → 服务器目录被清理
