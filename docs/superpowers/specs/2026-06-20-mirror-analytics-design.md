# 镜像站来源分析引擎 设计文档

## 概述

为镜像站群提供商业级来源追踪和分析能力。当多个静态镜像站点通过 Cloudflare Worker 代理到同一个目标 WooCommerce 站点时，追踪每个镜像来源的用户行为全链路（浏览→加购→下单），并通过 API 回传至凯瑞投流仪表盘统一展示。

## 架构

```
镜像站点A/B/C → CF Worker → X-Forwarded-Host 头
                    ↓
         目标 WooCommerce 站点
         ┌──────────────────────────────┐
         │  kairui-tracker 插件          │
         │  ┌──────────┐ ┌───────────┐  │
         │  │JS Tracker│ │PHP Server │  │
         │  │(前端埋点)│ │(事件接收) │  │
         │  └──────────┘ └───────────┘  │
         │         ↓           ↓        │
         │    ┌───────────────────┐     │
         │    │  kairui_events    │     │
         │    │  kairui_hourly    │     │
         │    │  kairui_orders    │     │
         │    └───────────────────┘     │
         │              ↓               │
         │    REST API (/kairui/v1/*)   │
         └──────────────┬───────────────┘
                        ↓
              凯瑞投流仪表盘
         ┌──────────────────────────────┐
         │  来源对比  │  转化漏斗       │
         │  访客分析  │  产品热力图     │
         │  ROI分析   │  实时在线       │
         └──────────────────────────────┘
```

## 组件

### 1. WordPress 插件 `kairui-tracker`

**结构：**
```
wp-content/plugins/kairui-tracker/
├── kairui-tracker.php
├── includes/
│   ├── class-schema.php      # 建表/迁移
│   ├── class-tracker.php     # 服务端事件接收
│   ├── class-api.php         # REST API
│   ├── class-aggregator.php  # WP-Cron 汇总
│   └── class-js-injector.php # JS 注入
└── assets/
    └── tracker.js            # 前端埋点脚本
```

**依赖：** 零外部依赖，纯 WordPress 原生 API。

### 2. JS Tracker (`tracker.js`)

- 大小：<5KB（压缩后）
- 加载：`wp_enqueue_scripts` + `async` 属性
- 来源识别：从 `document.cookie` 读取 `kairui_src`（由服务端首次访问时设置）
- 批量发送：每 5 秒或累积 20 条事件后，通过 `navigator.sendBeacon()` 发送到 `/wp-json/kairui/v1/track`
- 会话管理：生成 UUID session_id 存于 Cookie，30 分钟过期
- 停留时长：每 15 秒发送 heartbeat
- 滚动深度：IntersectionObserver 检测 25%/50%/75%/100%
- 退出意图：`mouseleave` 事件检测

### 3. PHP 服务端

- 来源 Cookie：首次访问时读取 `X-Forwarded-Host`，设置 `kairui_src` Cookie（90 天）
- 事件写入：批量写入 `kairui_events` 表，事务提交
- 订单归因：hook `woocommerce_checkout_order_processed`，读取 Cookie 中的 `kairui_src` 和 `kairui_sid`，写入 `kairui_order_attribution` 表

## 数据模型

### kairui_events

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT AUTO_INCREMENT | 主键 |
| source | VARCHAR(255) | 来源域名 |
| session_id | VARCHAR(64) | 会话 UUID |
| event_type | VARCHAR(50) | 事件类型 |
| page_url | VARCHAR(2048) | 当前页面 |
| product_id | BIGINT | WooCommerce 产品 ID |
| product_price | DECIMAL(10,2) | 产品价格 |
| order_id | BIGINT | 订单 ID |
| order_total | DECIMAL(10,2) | 订单总额 |
| extra_data | JSON | 扩展字段 |
| client_ip | VARCHAR(45) | 客户端 IP |
| user_agent | VARCHAR(512) | UA |
| created_at | DATETIME | 创建时间 |

索引：`(source, created_at)`, `(session_id)`, `(event_type)`

### kairui_hourly_stats

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT AUTO_INCREMENT | 主键 |
| source | VARCHAR(255) | 来源域名 |
| hour | DATETIME | 小时桶 |
| page_views | INT | 页面浏览 |
| product_views | INT | 产品浏览 |
| add_to_carts | INT | 加购 |
| checkouts | INT | 结账 |
| orders | INT | 下单 |
| revenue | DECIMAL(12,2) | 营收 |
| unique_visitors | INT | 独立访客 |
| bounce_count | INT | 跳出数 |

唯一键：`(source, hour)`

### kairui_order_attribution

| 列 | 类型 | 说明 |
|----|------|------|
| order_id | BIGINT PRIMARY KEY | WooCommerce 订单 ID |
| source | VARCHAR(255) | 来源域名 |
| session_id | VARCHAR(64) | 会话 ID |
| landing_page | VARCHAR(2048) | 落地页 |
| created_at | DATETIME | 归因时间 |

## 追踪事件

| 事件 | 触发方 | 触发时机 |
|------|-------|---------|
| page_view | JS | 页面加载 |
| heartbeat | JS | 每15秒（计算停留时长） |
| scroll_25/50/75/100 | JS | IntersectionObserver |
| exit_intent | JS | mouseleave |
| product_view | JS | 产品详情页 |
| product_impression | JS | 产品列表曝光 |
| search | JS | 站内搜索 |
| add_to_cart | JS+PHP | woocommerce_add_to_cart |
| remove_from_cart | PHP | woocommerce_remove_cart_item |
| begin_checkout | JS | 进入结账页 |
| add_shipping_info | JS | 配送表单完成 |
| add_payment_info | JS | 支付方式选择 |
| purchase | PHP | woocommerce_checkout_order_processed |
| refund | PHP | woocommerce_order_refunded |

## REST API

所有端点位于 `/wp-json/kairui/v1/`，需 API Key 鉴权（`X-Kairui-Key` 头，Key 在插件激活时自动生成，存储在 WordPress options 表中）。汇总任务由 WP-Cron 每小时执行一次。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/track` | POST | 批量事件接收 |
| `/analytics/summary` | GET | 来源汇总统计 |
| `/analytics/funnel` | GET | 转化漏斗 |
| `/analytics/trend` | GET | 时序趋势 |
| `/analytics/top-products` | GET | 产品热力图 |
| `/analytics/orders` | GET | 订单列表 |
| `/analytics/visitors` | GET | 访客分析 |
| `/analytics/behavior-flow` | GET | 行为流 |
| `/analytics/sessions` | GET | 会话明细 |
| `/analytics/realtime` | GET | 实时在线 |

## 凯瑞仪表盘变更

新增页面 `mirror-analytics`，侧边栏入口 `analytics` 图标。

- **来源对比**：表格 + 柱状图，各来源的访客/加购/下单/营收对比
- **转化漏斗**：选中来源后显示逐级漏斗（Bar Chart）
- **访客分析**：新老访客比、设备分布、地理分布、平均会话时长
- **时序趋势**：折线图，日/周/月维度的流量和营收趋势
- **产品热度**：每个来源下 TOP 产品排行
- **实时在线**：当前在线人数、活跃购物车

## 不影响现有功能

- 不修改 Worker 脚本
- 不修改镜像站点代码
- 不修改现有部署/删除/同步流程
- 插件仅安装在目标 WooCommerce 站点
- 仪表盘新增页面，不修改现有页面
