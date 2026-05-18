"""
定制市场结构全景报告：
Phase 1: 共现图社区检测（已完成）
Phase 2: LLM 合并相似簇 → 层级化市场结构
Phase 3: 每个节点程序化汇总指标
"""
import asyncio, aiohttp, json, sqlite3, re
from collections import defaultdict, Counter
from pathlib import Path

DB = "d:/claude code/sourcing-tool/data/sourcing.db"
COOC = Path("d:/claude code/tools/output/cooccurrence_communities.json")
OUT = Path("d:/claude code/tools/output")
API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# ── Load data ──
def load_all():
    cooc = json.loads(COOC.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""SELECT keyword, search_volume, cpc, search_conversion_rate_d90,
        click_of_90d, sales_volume_of_90d, product_count, share_click_rate,
        share_conversion_rate FROM keyword_extends WHERE keyword != ''""")
    rows = cur.fetchall()
    conn.close()
    return cooc["communities"], rows

# ── Phase 2: LLM 合并 + 层级化 ──
SYSTEM = """你是电商市场结构分析师。给你一组通过共现算法自动拆出的关键词簇，请将它们合并整理成层级化市场结构树。

规则：
1. 同产品类型的簇合并为一个segment（如3个T恤簇 → 1个服装segment）
2. 每个segment下拆解属性维度（产品形态/定制方式/材质/人群/场景等，由数据驱动）
3. 每个属性维度下列出该segment下的属性值——★必须是原始英文/西班牙文词根★，不要翻译成中文！取出现最多的8-15个
4. 西班牙语等非英语市场单独列出
5. 太大的segment可以拆子segment

输出纯JSON：
{
  "market": "市场总名",
  "segments": [
    {
      "name": "中文段名",
      "name_en": "english",
      "merged_from": ["comm_xx", "comm_yy"],
      "sub_dimensions": {
        "维度中文名": {"en": "dim_en", "values": ["root1", "root2", ...]}
      },
      "core_terms": ["产品本体词根"],
      "non_english": ["外语词根"],
      "note": "一句话商业判断"
    }
  ]
}

★ 重要：values 里填的是英文词根（如 mug, photo, men, mothers, birthday），不是中文翻译（不要写"马克杯"、"女性"、"母亲节"）！"""

async def build_tree(communities, all_keywords):
    # Prepare input: each cluster with top keywords and search volume
    cluster_desc = []
    for cid, cdata in communities.items():
        roots = cdata["roots"]
        root_set = set(roots)
        # Find matched keywords
        matched = []
        for row in all_keywords:
            kw = row[0].lower()
            words = set(re.sub(r'[^a-z]', ' ', kw).split())
            if words & root_set:
                matched.append((row[0], row[1] or 0))
        total_sv = cdata["total_search_volume"]
        top_kws = sorted(matched, key=lambda x: -x[1])[:20]  # more samples
        cluster_desc.append({
            "id": cid,
            "roots": roots,
            "total_sv": total_sv,
            "n_keywords": len(matched),
            "keyword_sample": [kw for kw, sv in top_kws]  # just keywords, no SV numbers
        })
    cluster_desc.sort(key=lambda x: -x["total_sv"])

    user = json.dumps(cluster_desc, ensure_ascii=False, indent=2)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"请合并整理以下关键词簇：\n\n{user}"}
        ],
        "temperature": 0.2, "max_tokens": 4000,
        "response_format": {"type": "json_object"}
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            r = await resp.json()
            content = r["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return json.loads(content)

# ── Phase 3: 指标汇总 ──
def aggregate(kw_rows, target_roots):
    """对给定词根集合，汇总匹配关键词的指标。
    target_roots 可以是单字根("mug")或多字根列表(["screen print"])。"""
    root_set = set(r.lower() for r in target_roots)
    matched = []
    for row in kw_rows:
        kw = row[0].lower()
        # 单字匹配 + 多字短语匹配
        kw_words = set(re.sub(r'[^a-z]', ' ', kw).split())
        # Check if any target root (single or multi-word) appears in the keyword
        hit = False
        for root in root_set:
            if ' ' in root:
                if root in kw:
                    hit = True
                    break
            else:
                if root in kw_words:
                    hit = True
                    break
        if hit:
            matched.append(row)
    if not matched:
        return None
    total_sv = sum(r[1] or 0 for r in matched)
    total_click = sum(r[4] or 0 for r in matched)
    total_sales = sum(r[5] or 0 for r in matched)
    cpcs = [r[2] for r in matched if r[2] and r[2] > 0]
    convs = [r[3] for r in matched if r[3] and r[3] > 0]
    shares = [r[7] for r in matched if r[7] and r[7] > 0]
    prods = [r[6] for r in matched if r[6] and r[6] > 0]
    return {
        "n": len(matched),
        "total_sv": total_sv,
        "total_click": total_click,
        "total_sales": total_sales,
        "avg_cpc": round(sum(cpcs) / len(cpcs), 2) if cpcs else 0,
        "avg_conv": round(sum(convs) / len(convs), 4) if convs else 0,
        "avg_share": round(sum(shares) / len(shares), 2) if shares else 0,
        "avg_products": round(sum(prods) / len(prods)) if prods else 0,
        "intensity": round(total_sv / max(sum(prods) / max(len(prods), 1), 1), 1) if prods else 0,
    }

# ── Output ──
def build_report(tree, kw_rows, communities):
    lines = [f"# {tree.get('market', '定制市场全景')}", ""]
    lines.append(f"总关键词: {len(kw_rows)} | 探索种子: custom / customized / personalized | US站")
    lines.append("")

    grand_total_sv = 0
    for seg in tree.get("segments", []):
        seg_name = seg["name"]
        seg_en = seg.get("name_en", "")
        merged = seg.get("merged_from", [])

        # Aggregate segment-level metrics from all merged clusters
        all_seg_roots = set()
        for cid in merged:
            if cid in communities:
                all_seg_roots.update(communities[cid]["roots"])
        seg_agg = aggregate(kw_rows, all_seg_roots)
        seg_sv = seg_agg["total_sv"] if seg_agg else 0
        grand_total_sv += seg_sv

        lines.append(f"## {seg_name} ({seg_en})")
        lines.append(f"月搜索量: {seg_sv:,} | 关键词: {seg_agg['n'] if seg_agg else 0}")
        if seg.get("note"):
            lines.append(f"> {seg['note']}")
        lines.append("")

        # Sub-dimensions with per-value aggregation
        sub_dims = seg.get("sub_dimensions", {})
        for dim_cn, dim_info in sub_dims.items():
            dim_en = dim_info.get("en", dim_cn)
            values = dim_info.get("values", [])
            lines.append(f"### {dim_cn} ({dim_en})")
            lines.append(f"| 属性值 | 关键词数 | 月搜索量 | 月点击 | 月销量 | CPC | 转化率 | 商品数 | 需求强度 |")
            lines.append(f"|--------|---------|---------|--------|--------|-----|--------|--------|---------|")

            for val in values:
                agg = aggregate(kw_rows, [val])
                if not agg or agg["n"] < 2:
                    continue
                lines.append(
                    f"| {val} | {agg['n']} | {agg['total_sv']:,} | "
                    f"{agg['total_click']:,} | {agg['total_sales']:,} | "
                    f"${agg['avg_cpc']:.2f} | {agg['avg_conv']*100:.1f}% | "
                    f"{agg['avg_products']:,} | {agg['intensity']} |"
                )
            lines.append("")

        # Core terms
        if seg.get("core_terms"):
            lines.append(f"核心词: {', '.join(seg['core_terms'])}")
        if seg.get("non_english"):
            lines.append(f"非英语: {', '.join(seg['non_english'])}")
        lines.append("")

    lines.insert(2, f"总市场搜索量: {grand_total_sv:,}\n")

    return "\n".join(lines)

async def main():
    print("Loading data...")
    communities, kw_rows = load_all()
    print(f"  {len(communities)} clusters, {len(kw_rows)} keywords")

    print("\nPhase 2: LLM merging & structuring...")
    tree = await build_tree(communities, kw_rows)
    print(f"  Market: {tree.get('market')}")
    for seg in tree.get("segments", []):
        dims = len(seg.get("sub_dimensions", {}))
        print(f"  {seg['name']}: {len(seg.get('merged_from',[]))} 簇合并 → {dims} 维度")

    print("\nPhase 3: Aggregating metrics...")
    report = build_report(tree, kw_rows, communities)

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "market_structure_report.md"
    out_path.write_text(report, encoding="utf-8")
    json_path = OUT / "market_tree.json"
    json_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone: {out_path}")
    print(f"Tree: {json_path}")

if __name__ == "__main__":
    asyncio.run(main())
