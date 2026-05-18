# 亚马逊选品 SOP 自动化工具

## 项目目标

将实战验证的选品 SOP 自动化为数据管道工具。关键词/类目 → 漏斗筛选 → 候选产品清单。

## 核心方法论

红蓝军对抗式选品：数据撞库 + 多维度漏斗筛选，找竞争友好、利润足够的产品机会。

## 选品 SOP

红蓝军对抗式漏斗筛选，完整流程见 `amazon-screening` Skill。

## 数据管道

- 外部数据源：
  - **Sorftime CLI** — 深度分析（接口详情见 Memory）
- 数据存储约定：
  - Sorftime 数据存 `keyword_cache` / `asin_cache` 表，**美分 ÷100 = USD**
- **数据库复合唯一约束**：`(keyword, domain)` 和 `(asin, domain)`
- **保留 `raw_response` TEXT 列**作为完整 JSON 备份

## 项目已有工具

1. ABA清洗工具/ — ABA 搜索词数据清洗
2. 数据聚合助手/ — 多维度数据聚合可视化
3. 现金流模拟器/ — FBA 现金流模拟
4. tools/keyword-root-filter.html — 关键词词根筛选器（独立HTML，交集筛选、KTV式高亮）
5. sourcing-tool/templates/explorer.html — 赛道探索器（上传KCR→拆词根→筛选→保留赛道）
6. sourcing-tool/merge_niches.py — 同标签小niche合并
7. sourcing-tool/dedup_niches.py — Jaccard跨标签ASIN去重
8. sourcing-tool/cluster_niche.py — 单赛道嵌入聚类+LLM命名
9. sourcing-tool/batch_cluster.py — 全量批量聚类流水线
10. sourcing-tool/dedup_sub_niches.py — 嵌入距离跨子赛道去重
11. sourcing-tool/expand_sub_niche.py — 双向扩容（ASIN拓词→词拓ASIN）
12. sourcing-tool/export_final.py — 导出最终子赛道Excel

## 技术架构

- 后端：**FastAPI** + SQLite（单进程，`python main.py` 启动）
- 前端：**Jinja2 + htmx + Alpine.js + Tailwind CSS + ECharts**
- 浏览器访问 `localhost:8000`，不打包桌面应用
- 项目技术栈决策由 AI 评估，用户不懂编程

## MCP 配置

- Playwright MCP：全局 `--no-sandbox`，自管 Chromium
