# 产品谷歌热度评分

## 目标

爆品导入表格加"Google热度"列，通过 DataForSEO Keywords Data API 查询每个产品名称的谷歌月搜索量，计算综合热度分(0-100)，帮助筛选最热门产品投放 GMC。

## DataForSEO API

端点：`POST https://api.dataforseo.com/v3/keywords_data/google/search_volume/live`

请求体：
```json
{
  "keywords": ["product name 1", "product name 2", ...],
  "location_code": 2840,
  "language_code": "en"
}
```

响应字段：
```json
{
  "tasks": [{
    "result": [{
      "keyword": "...",
      "search_volume": 12100,
      "competition": 0.75,
      "cpc": 1.25,
      "monthly_searches": [{"month":"2026-05","search_volume":12100}, ...]
    }]
  }]
}
```

## 热度分算法

```
热度分 = min(搜索量分 + 竞争度分 + CPC分, 100)

搜索量分 = min(月搜索量 / 1000 * 3, 40)     # 0→40
竞争度分 = competition * 30                  # 0→30
CPC分 = min(cpc * 10, 30)                    # 0→30
```

热度标签：🔥 ≥70 | ⭐ ≥40 | — <40

## 数据库

`amazon_search_results` 表新增列：
```sql
ALTER TABLE amazon_search_results ADD COLUMN search_volume INTEGER DEFAULT NULL;
ALTER TABLE amazon_search_results ADD COLUMN competition REAL DEFAULT NULL;
ALTER TABLE amazon_search_results ADD COLUMN cpc REAL DEFAULT NULL;
ALTER TABLE amazon_search_results ADD COLUMN hotness_score INTEGER DEFAULT NULL;
```

## 后端

### `backend/dataforseo_client.py`（新增）

```python
class DataForSEOClient:
    BASE = "https://api.dataforseo.com/v3"
    def __init__(self, login, password)
    def search_volume(self, keywords: list[str]) -> dict  # 返回 {keyword: {volume, competition, cpc}}
```

### `backend/routes.py`（新增端点）

`POST /api/products/google-volume`
```json
{"product_ids": [1, 5, 10]}
→ 批量查询 → 更新 DB → 返回结果
```

## 前端

### 爆品导入表格

新增表头列："Google热度"

每行显示：
- 热度分数字 + 🔥/⭐/— 标签
- 搜索量（鼠标悬停显示详细信息：竞争度/CPC）
- "查询热度"按钮（表格顶部工具栏，批量查询已选中产品）

### 筛选增强

按热度分排序按钮（默认🔥降序）

## 费用估算

DataForSEO 按关键词计费，100 次查询约 $0.01-0.02。1000 个产品约 $0.10-0.20。

## 验证

1. 系统设置配置 DataForSEO API 凭据
2. 爆品导入搜索产品 → 选中产品 → 点击"查询热度"
3. 表格显示🔥/⭐/— 标签和分数
4. 点击"热度排序"按热度降序排列
