# 静态商城生成引擎 — 设计文档

> 日期: 2026-06-13 | 状态: 待审核

## 1. 目标

品牌套件 AI 生成时一次性产出完整商城设计，部署时组件引擎根据设计渲染独一无二的静态电商站点，用于 GMC 审核 + 完整电商展示。

## 2. 核心架构

```
品牌套件 AI 生成
  ├── colors / typography / logo       ← AI (已存在)
  ├── business_info / tax / shipping   ← AI (已存在)
  │
  └── 🆕 design_system (JSON)          ← AI 生成
       {
         "layout": { "hero": "...", "nav": "...", "footer": "..." },
         "product_card": { "style": "...", "cols": 4 },
         "product_detail": { "gallery": "...", "tabs": true },
         "cart": { "style": "drawer" },
         "checkout": { "steps": [...] },
         "components": [...],
         "css_vars": { "--radius": "...", "--shadow": "..." }
       }

部署 / 产品同步
  └── 🆕 static_store_engine.py        ← 组件引擎
       读取 design_system + 产品数据 + brand_kit
       → 渲染完整 HTML/CSS/JS 文件
       → 写入 /app/backend/static-sites/{domain}/

纯静态站点
  ├── index.html         (首页网格)
  ├── products/{id}.html (产品详情页)
  ├── cart.html          (购物车)
  ├── checkout.html      (结账)
  ├── order.html         (订单确认)
  ├── about.html         (品牌故事)
  ├── contact.html       (联系我们)
  ├── privacy.html       (隐私政策)
  ├── terms.html         (服务条款)
  ├── shipping.html      (配送政策)
  ├── returns.html       (退换政策)
  ├── feed.xml           (Google Shopping Feed)
  ├── assets/
  │   ├── style.css      (品牌配色 + 组件样式)
  │   ├── store.js       (购物车 localStorage + 交互)
  │   ├── logo.png
  │   └── favicon.ico
  └── robots.txt
```

## 3. 新增模块

### 3.1 数据库

**`brand_kits` 新增字段:**
- `design_system` TEXT DEFAULT '{}' — AI 生成的设计决策 JSON

**`static_site_products` 表不变** — 已包含所有字段（title/description/price/image/category/...）

### 3.2 后端新文件

**`backend/static_store_engine.py`** — 组件渲染引擎
- `render_site(domain, brand_kit, products)` → 生成所有页面文件
- `render_product_page(product, design, brand)` → 单个产品详情页
- `render_cart_page(design, brand)` → 购物车页
- `render_checkout_page(design, brand)` → 结账页
- `render_homepage(products, design, brand)` → 首页
- `render_policy_pages(design, brand)` → 政策页
- `build_css_vars(design)` → CSS 变量

### 3.3 AI 生成修改

**`routes.py` 品牌套件生成流程** — Step 2 之后增加:
- Step 2.5: AI 生成 `design_system`（DeepSeek 调用，约 500 tokens 输出）
- 存储到 `brand_kits.design_system`

### 3.4 部署/同步修改

- `_bg_deploy_static` → 调用 `render_site()` 替代 `_generate_brand_pages()`
- `_regenerate_static_site_html` → 调用 `render_site()`

## 4. `design_system` 结构定义

```json
{
  "version": 1,
  "layout": {
    "hero": {
      "type": "split|gradient|image|carousel|minimal",
      "headline": "AI generated headline",
      "subheadline": "AI generated subheadline"
    },
    "nav": {
      "style": "sticky_top|left_sidebar|transparent_top",
      "search": true,
      "account_icon": true
    },
    "footer": {
      "columns": 4,
      "newsletter": false
    }
  },
  "product_card": {
    "style": "shadow|border|flat|overlay",
    "hover_effect": "zoom|lift|glow|none",
    "columns_desktop": 4,
    "show_rating": true,
    "show_badge": true
  },
  "product_detail": {
    "gallery": "thumbnails_left|thumbnails_bottom|carousel|stacked",
    "tabs": true,
    "sticky_atc": true,
    "show_breadcrumb": true,
    "show_sku": true,
    "show_share": true
  },
  "cart": {
    "style": "drawer|page|popup",
    "position": "right|left",
    "show_related": true
  },
  "checkout": {
    "steps": ["shipping", "payment", "review"],
    "show_coupon": false,
    "show_order_summary": true
  },
  "components": [
    "badge", "breadcrumb", "faq", "related_products",
    "reviews", "trust_badges", "newsletter"
  ],
  "css_vars": {
    "--radius": "8px",
    "--shadow": "0 4px 24px rgba(0,0,0,0.08)",
    "--transition": "0.3s ease"
  },
  "typography_scale": 1.0,
  "animation_level": "subtle|none"
}
```

## 5. 购物车/结账技术方案

纯前端，单文件 `store.js`，使用 localStorage。

### 购物车
- 数据结构: `{ items: [{product_id, qty, variant}], updated_at }`
- 增删改: JS 函数操作 localStorage
- UI: 顶部图标 + 数量徽章 + 滑出抽屉/独立页
- 跨页面同步: 每个页面加载时从 localStorage 读取

### 结账
- 3 步骤: 配送信息 → 支付信息(模拟) → 审核提交
- 表单校验: HTML5 + JS 二次校验
- 提交: 模拟成功，跳转确认页
- 确认页: 显示订单摘要 + 清空购物车

### 产品详情
- schema.org Product JSON-LD 结构化数据（GMC 关键）
- 变体选择: 颜色/尺寸下拉，JS 切换价格/图片
- 图片轮播: 纯 CSS + 少量 JS
- 相关产品: 随机选取同分类产品

## 6. AI Prompt 设计

品牌套件生成流程中新增一次 DeepSeek 调用：

```
System: You are an e-commerce design expert. Output strict JSON only.

User: Design a unique e-commerce storefront for brand "{brand_name}" 
in the "{industry}" industry.

Colors: {colors}  Typography: {typography}
Business: {business_info}

Output this JSON structure (fill in all creative decisions):
{design_system schema above}

Make the design UNIQUE — different hero type, different product card style, 
different layout choices from the previous designs. The store should feel 
like a real premium e-commerce site.
```

## 7. 实施步骤

| # | 步骤 | 文件 | 工作量 |
|---|------|------|--------|
| 1 | `brand_kits` 加 `design_system` 字段 | models.py | 5 行 |
| 2 | 实现 `static_store_engine.py` | 新文件 | ~400 行 |
| 3 | AI 生成 design_system（Step 2.5） | routes.py | ~80 行 |
| 4 | 改 `_bg_deploy_static` + `_regenerate` | routes.py | ~30 行 |
| 5 | 改 `delete_site` 清理 | routes.py | 已有 |
| 6 | Docker 构建 + 部署 | - | - |

## 8. 纯静态保证

- 生成站点 **不含任何后端逻辑**：每个域名目录下只有 HTML/CSS/JS/图片文件
- nginx 直接 serve 静态文件，不需要 PHP/Python/Node.js 运行时
- 购物车/结账/搜索 **全部前端 JS 实现**（localStorage + DOM 操作）
- 产品数据在生成时注入 HTML，**不依赖数据库查询**
- 站点可以部署到任何静态托管（CDN/S3/GitHub Pages），不绑定 puhuo

## 9. 网站数量与资源

| | WordPress (旧) | 静态站点 (新) |
|------|--------------|-------------|
| 单个站点大小 | ~150MB | **~50-200KB**（不含图片） |
| 所需运行时 | PHP + MariaDB | **无需**（纯文件） |
| 服务器可承载 | 5-10 个 | **数千个** |
| 部署时间 | 3-5 分钟 | **< 1 秒** |
| nginx 配置 | 每个站点单独配置 | **通配配置，零配置新增** |

引擎每生成一个站点只写入一次文件，站点上线后不再消耗任何 CPU/内存资源。

## 10. 边界与约束

- **不实现**: 真实支付（不需要后端）、真实用户认证、后台管理
- **技术栈**: 纯 HTML/CSS/JS，无框架依赖，store.js < 20KB
- **兼容**: 现代浏览器（Chrome/Firefox/Safari/Edge 近2年版本）
- **性能**: 首次加载 < 2s（含图片），lighthouse score > 80
- **GMC**: schema.org Product JSON-LD 完整标记，feed.xml 规范

## 11. 回退

如果 AI design_system 生成失败，引擎使用内置默认设计（等同于当前 `_generate_brand_pages` 的效果），不影响部署流程。
