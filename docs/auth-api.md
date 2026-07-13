# 凯瑞投流 - 外部登录对接 API 文档

## 概述

外部项目可通过调用本系统的登录接口，使用凯瑞投流的账号密码体系进行身份验证。支持 JWT Token 鉴权，兼容任何编程语言。

---

## 基础信息

| 项目 | 值 |
|---|---|
| 生产地址 | `https://ads.lhwebs.com` |
| 认证方式 | JWT Bearer Token |
| 请求格式 | `application/json` |
| 响应格式 | `application/json` |

---

## 接口列表

### 1. 登录验证 `POST /api/auth/login`

验证账号密码，返回 JWT Token 和用户信息。

**请求示例：**

```http
POST /api/auth/login
Content-Type: application/json

{
    "username": "kairui-pang",
    "password": "Mm123567.."
}
```

**成功响应 (200)：**

```json
{
    "code": 200,
    "message": "Login successful",
    "data": {
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "username": "kairui-pang",
        "role": "operator",
        "user_id": 6,
        "panel_environment_id": 5,
        "panel_environment": {
            "name": "美国-胖子-1",
            "host": "104.131.125.255"
        }
    }
}
```

**失败响应 (401)：**

```json
{
    "code": 401,
    "message": "Invalid credentials",
    "data": null
}
```

**返回字段说明：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `token` | string | JWT Token，后续请求需携带 |
| `username` | string | 登录用户名 |
| `role` | string | `admin` 管理员 / `operator` 运营 |
| `user_id` | int | 用户唯一 ID |
| `panel_environment_id` | int | 运营环境 ID |
| `panel_environment.name` | string | 运营环境名称 |
| `panel_environment.host` | string | 运营服务器 IP |

---

### 2. 校验登录态 `GET /api/auth/check`

验证 Token 是否有效，返回当前用户信息。

**请求示例：**

```http
GET /api/auth/check
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

**成功响应 (200)：**

```json
{
    "code": 200,
    "message": "OK",
    "data": {
        "username": "kairui-pang",
        "role": "operator",
        "user_id": 6,
        "panel_environment_id": 5,
        "panel_environment": {
            "name": "美国-胖子-1",
            "host": "104.131.125.255"
        }
    }
}
```

**失败响应 (401)：**

```json
{
    "msg": "Token has expired"
}
```

---

## 对接流程

```
外部项目                             凯瑞投流 API
   │                                      │
   │  1. POST /api/auth/login             │
   │     { username, password }           │
   │ ──────────────────────────────────→  │
   │                                      │  验证密码哈希
   │  2. 返回 { token, user_id, role }    │
   │ ←──────────────────────────────────  │
   │                                      │
   │  3. 存储 token 作为用户登录凭证       │
   │                                      │
   │  4. 可选: GET /api/auth/check        │
   │     Authorization: Bearer {token}    │
   │ ──────────────────────────────────→  │
   │                                      │  校验 JWT 签名与有效期
   │  5. 返回 200 或 401                  │
   │ ←──────────────────────────────────  │
```

---

## 各语言对接示例

### Python

```python
import requests

BASE = "https://ads.lhwebs.com"

class KairuiAuth:
    def __init__(self):
        self.token = None
        self.user = None

    def login(self, username: str, password: str) -> dict | None:
        """登录并返回用户信息"""
        resp = requests.post(
            f"{BASE}/api/auth/login",
            json={"username": username, "password": password},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200:
                self.user = data["data"]
                self.token = self.user["token"]
                return self.user
        return None

    def check(self) -> bool:
        """校验 token 是否有效"""
        if not self.token:
            return False
        resp = requests.get(
            f"{BASE}/api/auth/check",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10
        )
        return resp.status_code == 200

# 使用
auth = KairuiAuth()
user = auth.login("kairui-pang", "Mm123567..")
if user:
    print(f"登录成功: {user['username']} (ID: {user['user_id']})")
```

### JavaScript / Node.js

```javascript
const BASE = "https://ads.lhwebs.com";

async function kairuiLogin(username, password) {
  const resp = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json();
  if (data.code === 200) return data.data;
  throw new Error(data.message);
}

async function kairuiCheckToken(token) {
  const resp = await fetch(`${BASE}/api/auth/check`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return resp.ok;
}

// 使用
const user = await kairuiLogin("kairui-pang", "Mm123567..");
console.log(`登录成功: ${user.username}, Token: ${user.token.substring(0, 20)}...`);
```

### PHP

```php
<?php
define('BASE', 'https://ads.lhwebs.com');

function kairui_login($username, $password) {
    $ch = curl_init(BASE . '/api/auth/login');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode(['username' => $username, 'password' => $password]),
        CURLOPT_TIMEOUT => 10,
    ]);
    $resp = json_decode(curl_exec($ch), true);
    curl_close($ch);
    return $resp['code'] === 200 ? $resp['data'] : null;
}

$user = kairui_login('kairui-pang', 'Mm123567..');
if ($user) echo "登录成功: {$user['username']}, ID: {$user['user_id']}\n";
```

### cURL

```bash
# 登录
curl -X POST https://ads.lhwebs.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"kairui-pang","password":"Mm123567.."}'

# 校验 Token
curl https://ads.lhwebs.com/api/auth/check \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

## 注意事项

1. **不开放注册**：仅验证凯瑞投流中已有的账号，无法通过此接口创建新用户
2. **Token 过期**：JWT Token 有效期约 24 小时，过期后需重新调用 `/api/auth/login`
3. **外部项目的用户体系**：建议外部项目自行维护用户表，将凯瑞的 `user_id` 作为关联字段，不要直接存储用户密码
4. **CORS**：如需浏览器端调用，可能需要在服务端配置 CORS 白名单

---

## 当前状态

| 环境 | 地址 | 状态 |
|---|---|---|
| 生产 | `https://ads.lhwebs.com` | 需确认服务器是否恢复 |
