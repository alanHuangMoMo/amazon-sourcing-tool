"""
Phase 1: 共现图社区检测（已算完，直接读结果）
Phase 2: LLM 对每个簇做三件事 → 命名 / 判维度或产品段 / 递归拆子属性
"""
import asyncio, aiohttp, json, sqlite3, re
from collections import defaultdict
from pathlib import Path

DB_PATH = "d:/claude code/sourcing-tool/data/sourcing.db"
COOC_PATH = Path("d:/claude code/tools/output/cooccurrence_communities.json")
OUT_DIR = Path("d:/claude code/tools/output")
API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

SYSTEM = """你是一个电商产品属性分析专家。我会给你一组关键词（完整关键词+搜索量），这些关键词通过共现算法被聚成一个簇。

请对这个簇做三件事：

1. **命名**：用中文给这个簇一个2-6字的名字
2. **判断类型**：这个簇是以下哪种？
   - "attribute_dimension": 簇内词根是同一种属性的不同取值（如所有颜色词聚在一起：black/white/red）
   - "product_segment": 簇内词根定义了一个产品子市场（如杯子类：mug/coffee/cup，或男装类：shirts/men）
   - "mixed": 兼有两种特征
3. **拆解**：
   - 如果是 attribute_dimension：该属性的中文名是什么？（如"颜色"、"材质"）
   - 如果是 product_segment 或 mixed：在这个产品段内部，还能拆出哪些属性维度？列出每个维度及其候选属性值。

另外：
- 标记出"非英语词根"（如西班牙语等）
- 标记出"本体词根"（纯粹描述产品而非属性的词）

输出纯JSON，不要markdown，不要解释：
{
  "name": "簇中文名",
  "name_en": "cluster english name",
  "type": "attribute_dimension|product_segment|mixed",
  "attribute_name": "属性名（仅attribute_dimension时填写）",
  "attribute_name_en": "attribute en name",
  "sub_dimensions": {
    "维度中文名": {"en": "dim_en", "values": ["val1", "val2"]}
  },
  "non_english": ["root1", "root2"],
  "core_terms": ["root1", "root2"],
  "summary": "一句话总结这个簇的商业含义"
}
"""

async def call_llm(community_name, roots, keywords_sample):
    """调用DeepSeek分析一个簇"""
    user = f"""簇的top词根: {', '.join(roots[:10])}

该簇下的关键词样本（前30个）:
{chr(10).join(f'  - {kw} (搜索量:{sv:,})' for kw, sv in keywords_sample[:30])}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user}
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"}
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            result = await resp.json()
            content = result["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return json.loads(content)

async def main():
    # Load co-occurrence results
    if not COOC_PATH.exists():
        print("错误: 先跑 cooccurrence_cluster.py")
        return
    cooc = json.loads(COOC_PATH.read_text(encoding="utf-8"))
    communities = cooc["communities"]

    # Load all keywords from DB
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT keyword, search_volume FROM keyword_extends WHERE keyword != ''")
    all_kws = [(r[0], r[1] or 0) for r in cur.fetchall()]
    conn.close()

    print(f"=== Phase 2: LLM分析 {len(communities)} 个簇 ===\n")

    results = {}
    for i, (comm_key, comm_data) in enumerate(communities.items()):
        roots = comm_data["roots"]
        # Find keywords that contain at least one of these roots
        root_set = set(roots)
        matched = []
        for kw, sv in all_kws:
            kw_words = set(re.sub(r'[^a-z]', ' ', kw.lower()).split())
            if kw_words & root_set:
                matched.append((kw, sv))

        print(f"  [{i+1}/{len(communities)}] {comm_key}: {roots[:8]}... ({len(matched)} keywords)")

        try:
            analysis = await call_llm(comm_key, roots, matched)
            analysis["_n_keywords"] = len(matched)
            analysis["_roots"] = roots
            analysis["_total_search_volume"] = comm_data["total_search_volume"]
            results[comm_key] = analysis
            print(f"    → {analysis['name']} [{analysis['type']}]")
            if analysis.get("sub_dimensions"):
                for dim_cn, dim_info in analysis["sub_dimensions"].items():
                    print(f"       └ {dim_cn}: {dim_info['values'][:6]}")
        except Exception as e:
            print(f"    → 失败: {e}")

    # ── 汇总报告 ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save raw results
    out_json = OUT_DIR / "two_phase_results.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Generate summary
    lines = ["# 赛道结构分析报告\n"]
    dims = []
    segments = []

    for cid, r in results.items():
        if r["type"] == "attribute_dimension":
            dims.append(r)
        else:
            segments.append(r)

    lines.append(f"## 全局属性维度 ({len(dims)} 个)\n")
    lines.append("| 维度 | 属性值 | 关键词数 | 月搜索量 |")
    lines.append("|------|--------|---------|----------|")
    for r in dims:
        roots_str = ", ".join(r["_roots"][:8])
        lines.append(f"| {r['attribute_name']} | {roots_str} | {r['_n_keywords']} | {r['_total_search_volume']:,} |")

    lines.append(f"\n## 产品子市场 ({len(segments)} 个)\n")
    for r in sorted(segments, key=lambda x: -x["_total_search_volume"]):
        lines.append(f"### {r['name']} ({r['name_en']})")
        lines.append(f"- 关键词数: {r['_n_keywords']} | 月搜索量: {r['_total_search_volume']:,}")
        lines.append(f"- {r.get('summary', '')}")
        if r.get("sub_dimensions"):
            lines.append(f"- 内部分解维度:")
            for dim_cn, dim_info in r["sub_dimensions"].items():
                vals = ", ".join(dim_info["values"][:10])
                lines.append(f"  - {dim_cn}: {vals}")
        if r.get("core_terms"):
            lines.append(f"- 核心词: {', '.join(r['core_terms'])}")
        if r.get("non_english"):
            lines.append(f"- 非英语: {', '.join(r['non_english'])}")
        lines.append("")

    out_md = OUT_DIR / "two_phase_report.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {out_md}")
    print(f"数据: {out_json}")

if __name__ == "__main__":
    asyncio.run(main())
