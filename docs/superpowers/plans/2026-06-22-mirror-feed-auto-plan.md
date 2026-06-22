# 镜像站自动 GMC Feed 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建镜像时自动从目标 WooCommerce 拉取产品生成 Google Shopping Feed XML，部署到镜像域名。

**Architecture:** 系统设置配置 WC 源站（Key/Secret+运营绑定）→ 镜像创建时自动读凭据→拉取产品→生成 feed→ 上传 cnusel.com 插件存储。

**Tech Stack:** Python Flask, WooCommerce REST API, XML ElementTree, Cloudflare Workers, WordPress PHP.

---

### Task 1: WC 源站数据模型

**Files:**
- Modify: `backend/models.py`

- [ ] **Step 1: 添加 wc_sources 表和迁移**

```python
# 在 models.py 的 _migrate 函数中添加
if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wc_sources'").fetchone():
    conn.execute("""
        CREATE TABLE wc_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            consumer_key TEXT NOT NULL DEFAULT '',
            consumer_secret TEXT NOT NULL DEFAULT '',
            operator_id INTEGER DEFAULT NULL,
            created_at TEXT
        )
    """)

# CRUD 函数
def list_wc_sources() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM wc_sources ORDER BY id").fetchall()
    return [dict(r) for r in rows]

def create_wc_source(data: dict) -> dict:
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO wc_sources (name, url, consumer_key, consumer_secret, operator_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (data.get("name",""), data.get("url",""), data.get("consumer_key",""), data.get("consumer_secret",""), data.get("operator_id"), now)
    )
    conn.commit()
    return conn.execute("SELECT * FROM wc_sources WHERE id = last_insert_rowid()").fetchone()

def delete_wc_source(source_id: int):
    conn = get_db()
    conn.execute("DELETE FROM wc_sources WHERE id = ?", (source_id,))
    conn.commit()

def get_wc_source_for_operator(operator_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM wc_sources WHERE operator_id = ? LIMIT 1", (operator_id,)).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 2: Commit**

```bash
git add backend/models.py
git commit -m "feat: wc_sources 表+CRUD支持运营绑定WC凭据"
```

---

### Task 2: WC 源站管理 API

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: 添加 WC 源站 CRUD 路由**

```python
@app.route("/api/wc-sources", methods=["GET"])
@jwt_required()
def list_wc_sources_route():
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify({"code": 403}), 403
    return jsonify({"code": 200, "data": list_wc_sources()})

@app.route("/api/wc-sources", methods=["POST"])
@jwt_required()
def create_wc_source_route():
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify({"code": 403}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip().replace("https://","").replace("http://","").strip("/")
    key = (data.get("consumer_key") or "").strip()
    secret = (data.get("consumer_secret") or "").strip()
    op_id = data.get("operator_id") or None
    if not all([name, url, key, secret, op_id]):
        return jsonify({"code": 400, "message": "缺少必填字段"}), 400
    src = create_wc_source({"name": name, "url": url, "consumer_key": key, "consumer_secret": secret, "operator_id": op_id})
    return jsonify({"code": 200, "data": src})

@app.route("/api/wc-sources/<int:sid>", methods=["DELETE"])
@jwt_required()
def delete_wc_source_route(sid):
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify({"code": 403}), 403
    delete_wc_source(sid)
    return jsonify({"code": 200})
```

- [ ] **Step 2: Commit**

```bash
git add backend/routes.py
git commit -m "feat: WC源站管理API(CRUD)"
```

---

### Task 3: 系统设置 WC 源站标签页

**Files:**
- Modify: `frontend/static/js/app.js`

- [ ] **Step 1: 添加 settingsTabs 条目**

```javascript
{ key: 'wc_source', label: 'WC源站' },
```

插入在分析配置和谷歌账户之间。

- [ ] **Step 2: 添加状态变量**

```javascript
const wcSources = ref([]);
const wcSourceForm = reactive({ name: '', url: '', consumer_key: '', consumer_secret: '', operator_id: null });
const wcSourceEditId = ref(null);
```

- [ ] **Step 3: 添加 CRUD 函数**

```javascript
async function loadWcSources() {
    const r = await API.request('GET', '/api/wc-sources');
    if (r.code === 200) wcSources.value = r.data;
}
function openWcSourceModal(src) {
    if (src) { wcSourceEditId.value = src.id; Object.assign(wcSourceForm, src); }
    else { wcSourceEditId.value = null; wcSourceForm = { name: '', url: '', consumer_key: '', consumer_secret: '', operator_id: null }; }
    showWcSourceModal.value = true;
}
async function saveWcSource() {
    const data = { ...wcSourceForm };
    const r = await API.request(wcSourceEditId.value ? 'PUT' : 'POST', '/api/wc-sources' + (wcSourceEditId.value ? '/' + wcSourceEditId.value : ''), data);
    if (r.code === 200) { showToast('已保存'); loadWcSources(); showWcSourceModal.value = false; }
    else showToast(r.message || '保存失败', 'error');
}
async function deleteWcSource(id) {
    if (!confirm('确定删除？')) return;
    await API.request('DELETE', '/api/wc-sources/' + id);
    loadWcSources();
}
```

- [ ] **Step 4: 添加模板**

```html
<div v-else-if="settingsActiveTab === 'wc_source'" @vue:mounted="loadWcSources()">
    <div class="flex items-center justify-between mb-4">
        <h4 class="text-sm font-semibold"><i class="fab fa-wordpress mr-2 text-primary"></i>WooCommerce 源站</h4>
        <button @click="openWcSourceModal(null)" class="btn-primary px-4 py-2 rounded text-sm"><i class="fas fa-plus mr-1"></i>添加源站</button>
    </div>
    <div v-if="wcSources.length" class="space-y-3">
        <div v-for="s in wcSources" class="bg-surface-container-low rounded-lg p-4 flex items-center justify-between">
            <div>
                <p class="font-medium">{{ s.name }} ({{ s.url }})</p>
                <p class="text-xs text-on-surface-variant">绑定运营: {{ s.operator_id }} | Key: {{ s.consumer_key.substring(0,12) }}...</p>
            </div>
            <div class="flex gap-1">
                <button @click="openWcSourceModal(s)" class="text-xs text-primary"><i class="fas fa-edit"></i></button>
                <button @click="deleteWcSource(s.id)" class="text-xs text-error"><i class="fas fa-trash"></i></button>
            </div>
        </div>
    </div>
    <p v-else class="text-sm text-on-surface-variant py-8 text-center">暂无WC源站配置</p>
</div>
```

- [ ] **Step 5: 添加模态框**

```html
<div v-if="showWcSourceModal" class="modal-overlay" @click.self="showWcSourceModal=false">
    <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-md mx-4 p-6 fade-in">
        <h3 class="text-lg font-bold mb-4">{{ wcSourceEditId ? '编辑' : '添加' }} WC 源站</h3>
        <div class="space-y-3">
            <div><label class="block text-xs mb-1">源站名称</label><input v-model="wcSourceForm.name" class="w-full px-3 py-2 border rounded text-sm"></div>
            <div><label class="block text-xs mb-1">站点 URL</label><input v-model="wcSourceForm.url" placeholder="cnusel.com" class="w-full px-3 py-2 border rounded text-sm"></div>
            <div><label class="block text-xs mb-1">Consumer Key</label><input v-model="wcSourceForm.consumer_key" class="w-full px-3 py-2 border rounded text-sm"></div>
            <div><label class="block text-xs mb-1">Consumer Secret</label><input v-model="wcSourceForm.consumer_secret" type="password" class="w-full px-3 py-2 border rounded text-sm"></div>
            <div><label class="block text-xs mb-1">绑定运营</label>
                <select v-model="wcSourceForm.operator_id" class="w-full px-3 py-2 border rounded text-sm">
                    <option :value="null">选择运营</option>
                    <option v-for="u in users" :key="u.id" :value="u.id" v-if="u.role==='operator'">{{ u.username }}</option>
                </select>
            </div>
        </div>
        <div class="flex justify-end gap-2 mt-6">
            <button @click="showWcSourceModal=false" class="px-4 py-2 border rounded text-sm">取消</button>
            <button @click="saveWcSource" class="btn-primary px-6 py-2 rounded text-sm">保存</button>
        </div>
    </div>
</div>
```

- [ ] **Step 6: 注册到 return 和 commit**

---

### Task 4: Feed 自动生成 + 镜像集成

**Files:**
- Modify: `backend/routes.py`

- [ ] **Step 1: 创建 Feed 生成函数**

```python
def _generate_mirror_feed(domain, wc_source):
    """Pull products from WooCommerce API and generate GMC feed XML."""
    import xml.etree.ElementTree as ET
    import requests as http_requests
    from requests.auth import HTTPBasicAuth

    url = wc_source["url"]
    auth = HTTPBasicAuth(wc_source["consumer_key"], wc_source["consumer_secret"])
    base = f"https://{url}/wp-json/wc/v3/products"

    ns_g = "http://base.google.com/ns/1.0"
    rss = ET.Element("rss", {"version": "2.0", "xmlns:g": ns_g})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = domain
    ET.SubElement(channel, "link").text = f"https://{domain}"
    ET.SubElement(channel, "description").text = "Google Shopping Product Feed"

    page, total = 1, 0
    while True:
        r = http_requests.get(f"{base}?per_page=100&page={page}", auth=auth, timeout=30)
        if r.status_code != 200: break
        products = r.json()
        if not products: break
        total += len(products)

        for p in products:
            ptype = p.get("type", "simple")
            # Skip parent variable products
            if ptype == "variable":
                continue

            # Use variation-specific data if available
            name = p.get("name", "")
            price = p.get("sale_price") or p.get("regular_price") or p.get("price") or ""
            permalink = p.get("permalink", f"https://{domain}")
            # Replace target domain with mirror domain in permalink
            permalink = permalink.replace(f"https://{url}", f"https://{domain}")

            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "g:id").text = p.get("sku") or str(p.get("id"))
            ET.SubElement(item, "g:title").text = name[:150]
            ET.SubElement(item, "g:description").text = (p.get("short_description") or p.get("description") or "")[:5000]
            ET.SubElement(item, "g:link").text = permalink

            images = p.get("images", [])
            if images:
                ET.SubElement(item, "g:image_link").text = images[0].get("src", "")
                for img in images[1:11]:
                    ET.SubElement(item, "g:additional_image_link").text = img.get("src", "")

            if price:
                ET.SubElement(item, "g:price").text = f"{price} USD"
            ET.SubElement(item, "g:availability").text = "in_stock" if p.get("stock_status") != "outofstock" else "out_of_stock"
            ET.SubElement(item, "g:condition").text = "new"

            brand = ""
            for attr in p.get("attributes", []):
                if attr.get("name", "").lower() == "brand":
                    brand = (attr.get("options") or [""])[0]
                    break
            if brand:
                ET.SubElement(item, "g:brand").text = brand[:70]
            if p.get("sku"):
                ET.SubElement(item, "g:mpn").text = str(p.get("sku"))[:70]

            # item_group_id for variations
            if ptype == "variation" and p.get("parent_id"):
                ET.SubElement(item, "g:item_group_id").text = str(p.get("parent_id"))

        page += 1
        logger.info(f"[MirrorFeed] {domain}: page={page-1}, total={total}")

    return ET.tostring(rss, encoding="unicode")
```

- [ ] **Step 2: 在镜像创建流程中集成**

在 `sites_mirror()` 函数的 `for sid in site_ids` 循环中，Worker 部署成功后添加：

```python
# Generate feed if requested
if data.get("generate_feed"):
    wc_src = get_wc_source_for_operator(site.get("created_by"))
    if wc_src:
        try:
            feed_xml = _generate_mirror_feed(domain, wc_src)
            # Upload to cnusel.com plugin
            cfg = get_global_config()
            api_key = cfg.get(f"kairui_key_{target_host}", "")
            if api_key:
                http_requests.post(
                    f"https://{target_host}/wp-json/kairui/v1/feed/upload",
                    json={"domain": domain, "content": feed_xml},
                    headers={"X-Kairui-Key": api_key},
                    timeout=30
                )
                results[-1]["feed_url"] = f"https://{domain}/feed-{domain}.xml"
                logger.info(f"[MirrorFeed] {domain}: feed uploaded ({len(feed_xml)} bytes)")
        except Exception as fe:
            logger.warning(f"[MirrorFeed] {domain}: feed generation failed: {fe}")
```

- [ ] **Step 3: 前端镜像向导加复选框**

```html
<div class="flex items-center gap-2 mt-2">
    <input type="checkbox" v-model="mirrorGenerateFeed" id="genfeed" class="accent-primary">
    <label for="genfeed" class="text-sm">自动生成 GMC Feed</label>
</div>
```

添加状态 `const mirrorGenerateFeed = ref(true)`，在 `startMirror()` 中发送 `generate_feed: mirrorGenerateFeed.value`。

- [ ] **Step 4: 验证**

```bash
cd "C:\Users\Administrator\Desktop\kairui"
python -c "import py_compile; py_compile.compile('backend/routes.py', doraise=True); py_compile.compile('backend/models.py', doraise=True); print('OK')"
git add backend/routes.py frontend/static/js/app.js
git commit -m "feat: 镜像创建自动生成GMC Feed"
```

---
