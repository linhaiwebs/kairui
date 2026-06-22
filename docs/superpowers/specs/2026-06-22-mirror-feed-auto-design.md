# 镜像站自动创建 GMC Feed 设计文档

## 概述

扩展镜像向导：创建镜像时自动从目标 WooCommerce 拉取全量产品（含子变体），生成 Google Shopping Feed XML，部署到镜像域名下供 GMC 使用。

## 架构

```
创建镜像 a.shop → cnusel.com
         │
         ├─ ① CF Worker 部署
         ├─ ② WooCommerce API 拉取全量产品
         ├─ ③ 生成 feed-a.shop.xml（Google Shopping 格式）
         ├─ ④ 上传到 cnusel.com 插件存储
         └─ ⑤ 访问 https://a.shop/feed-a.shop.xml
                    → Worker 代理 → cnusel.com
                    → 插件识别 X-Forwarded-Host: a.shop
                    → 返回对应 feed
```

## 组件

### 1. 系统设置 → WooCommerce 源站（管理员配置）

| 字段 | 说明 |
|------|------|
| 源站名称 | 显示用（如 "cnusel主站"） |
| 站点 URL | cnusel.com |
| Consumer Key | ck_xxx |
| Consumer Secret | cs_xxx |
| 绑定运营 | 下拉选择运营角色 |

后端存储：`global_config` 表，key 格式 `wc_source_{id}`。

### 2. WooCommerce 产品同步

创建镜像时：
1. 读取当前运营绑定的 WC 源站凭据
2. 分页调用 `wc/v3/products?per_page=100&page=N`
3. **包含子变体**（variation），每个变体有独立价格/SKU/图片
4. 生成 `feed-{域名}.xml`

### 3. Feed XML 格式

```xml
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
<channel>
<title>a.shop</title>
<link>https://a.shop</link>
<description>Google Shopping Product Feed</description>
<item>
  <g:id>sku-123</g:id>
  <g:title>产品名 - 红色/XL</g:title>
  <g:description>描述</g:description>
  <g:link>https://a.shop/product/parent/?attribute_color=red&size=xl</g:link>
  <g:image_link>https://...</g:image_link>
  <g:price>29.99 USD</g:price>
  <g:availability>in_stock</g:availability>
  <g:condition>new</g:condition>
  <g:brand>品牌名</g:brand>
  <g:mpn>sku-123</g:mpn>
  <g:item_group_id>parent-456</g:item_group_id>  <!-- 仅变体 -->
</item>
</channel>
</rss>
```

### 4. Feed 存储与访问

使用已有的 `class-feed-server.php` 插件（cnusel.com 上）：
- 上传：`POST /wp-json/kairui/v1/feed/upload` `{domain: "a.shop", content: "<xml>..."}`
- 访问：`GET https://a.shop/feed-a.shop.xml` → Worker 代理 → 插件匹配域名返回

### 5. 镜像向导 UI 变更

- 创建镜像时，新增选项：`[√] 自动生成 GMC Feed`
- 默认勾选
- 创建完成后显示 Feed URL

## 数据流

```
系统设置（WC凭据）
    ↓ 管理员配置
镜像向导（运营创建）
    ↓ 读取绑定的 WC 源站
WooCommerce REST API
    ↓ 分页拉取产品
Feed XML 生成
    ↓ 产品链接使用镜像域名
cnusel.com 插件存储
    ↓ key={镜像域名}
CF Worker 代理 → 最终访问
```

## 产品同步规则

- 父产品（variable）：不单独生成条目
- 子变体（variation）：每个生成一条 Feed 条目，含独立价格/SKU/图片
- 简单产品（simple）：每条生成一个 Feed 条目
- 价格使用 `regular_price`，有 `sale_price` 时用 `sale_price`
- 无库存标记为 `out_of_stock`

## 不影响现有功能

- 镜像/create Worker/URL 重写逻辑不变
- 已有 feed-server 插件复用
- 仅扩展镜像创建流程
