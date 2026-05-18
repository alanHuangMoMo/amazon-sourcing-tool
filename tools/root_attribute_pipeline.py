"""
MVP: 关键词词根 → LLM属性分类 → 交叉组合 → 汇总报表
"""
import asyncio, aiohttp, json, sqlite3, csv
from collections import Counter, defaultdict
from pathlib import Path
import re, sys, math

DB_PATH = "d:/claude code/sourcing-tool/data/sourcing.db"
OUT_DIR = Path("d:/claude code/tools/output")
API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

STOP_WORDS = {
    'a','an','the','is','are','was','were','be','been','being',
    'have','has','had','having','do','does','did','doing',
    'will','would','shall','should','can','could','may','might','must',
    'i','me','my','we','our','us','you','your','he','she','it','its',
    'they','them','their','this','that','these','those',
    'in','on','at','to','for','of','with','by','from',
    'and','or','but','not','no','nor','if','so','as','than',
    'also','too','very','just','about','up','out','down','off','over','under',
    'again','all','each','every','both','few','more','most',
    'other','some','such','only','own','same','new','now',
    'then','here','there','when','where','why','how','which','who','what','whom',
    'one','two','three','first','last','get','got','go','going',
    'into','onto','after','before','during','without','within',
    'per','like','much','many','any','been','still','well',
    'back','also','even','already','yet',
    'custom','customized','personalized','personalised',
}

MIN_ROOT_LEN = 2
MIN_ROOT_COUNT = 2  # 词根至少出现2次才提交LLM

# ---- Step 1: 从 DB 读取关键词，拆词根 ----
def load_and_extract():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT seed_keyword, keyword, search_volume, cpc,
               search_conversion_rate_d90, click_of_90d, sales_volume_of_90d,
               product_count, share_click_rate, share_conversion_rate
        FROM keyword_extends
        WHERE keyword != ''
    """)
    rows = cur.fetchall()
    conn.close()

    keywords = []  # [(seed, kw, sv, cpc, ...)]
    root_counter = Counter()
    root_examples = defaultdict(set)  # root -> set of example keywords

    for row in rows:
        seed, kw = row[0], row[1]
        keywords.append(row)
        # 拆词根
        words = kw.lower().split()
        seen = set()
        for w in words:
            # 去掉非字母字符
            clean = re.sub(r'[^a-z]', '', w)
            if len(clean) >= MIN_ROOT_LEN and clean not in STOP_WORDS:
                root_counter[clean] += 1
                if clean not in seen and len(root_examples[clean]) < 3:
                    root_examples[clean].add(kw)
                seen.add(clean)

    # 过滤低频词根
    valid_roots = {r for r, c in root_counter.items() if c >= MIN_ROOT_COUNT}
    ranked_roots = sorted(
        [(r, root_counter[r]) for r in valid_roots],
        key=lambda x: -x[1]
    )

    print(f"  总关键词: {len(keywords)}")
    print(f"  去重词根: {len(valid_roots)} (出现≥{MIN_ROOT_COUNT}次)")
    print(f"  Top 20 词根: {', '.join(f'{r}({c})' for r,c in ranked_roots[:20])}")

    return keywords, ranked_roots, root_examples, root_counter

# ---- Step 2: 调用 DeepSeek 分类词根 ----
SYSTEM_PROMPT = """你是电商关键词词根分类专家。给你一个产品赛道的关键词词根列表（附带出现次数和示例关键词），请完成：

第一步：根据词根和示例判断这是什么产品赛道
第二步：识别该赛道适用的属性维度。维度完全由数据驱动——看到什么属性就列什么维度。常见维度包括但不限于：
  - 颜色/图案 (color, black, striped, floral, glitter, holographic...)
  - 材质 (cotton, leather, stainless, silicone, vinyl, acrylic...)
  - 尺寸/规格 (large, small, mini, xl, plus, tall, 2inch...)
  - 风格/设计 (vintage, modern, boho, minimalist, rustic, 3d...)
  - 功能/用途 (waterproof, rechargeable, adjustable, portable...)
  - 适用人群 (women, men, kids, baby, seniors, toddler...)
  - 场景/节日 (wedding, christmas, birthday, camping, office...)
  - 形式/类型 (sticker, decal, sign, bracelet, necklace, shirt...)
  - 数量/pack (pack, bulk, lot, dozen, wholesale...)
  - 技术参数 (bluetooth, wireless, 4k, heavy, lightweight...)
第三步：将每个词根归类到对应维度下。一个词根只能属于一个维度
第四步：标记"产品本体词"——这些是描述产品本身的通用词，不参与属性分析
第五步：无法归类的标为"其他"

输出纯JSON，不要markdown包裹，不要加任何解释文字：
{
  "category": "中文赛道名",
  "category_en": "English category name",
  "dimensions": {
    "维度中文名": {"en": "dimension_en", "roots": ["root1", "root2", ...]},
    ...
  },
  "core_terms": ["product", "generic", "word"],
  "unknown": ["rareword1", "rareword2"]
}"""

FEW_SHOT_EXAMPLE = """
示例1 — 服装类：
词根: [cotton(450), black(320), polyester(180), slim(150), men(140), vintage(120), oversized(110), striped(95), floral(88), breathable(75), wedding(60)]
赛道判断: T恤定制
输出: {"category":"定制T恤","category_en":"Custom T-Shirts","dimensions":{"颜色/图案":{"en":"color_pattern","roots":["black","striped","floral"]},"材质":{"en":"material","roots":["cotton","polyester"]},"版型/剪裁":{"en":"fit","roots":["slim","oversized"]},"风格":{"en":"style","roots":["vintage"]},"功能":{"en":"function","roots":["breathable"]},"人群":{"en":"audience","roots":["men"]},"场景":{"en":"occasion","roots":["wedding"]}},"core_terms":[],"unknown":[]}

示例2 — 厨房用具类：
词根: [stainless(300), insulated(250), large(200), handle(180), lid(160), camping(140), dishwasher(130), copper(110), teacher(95), nurse(80)]
赛道判断: 定制水杯/随行杯
输出: {"category":"定制随行杯","category_en":"Custom Tumblers","dimensions":{"材质":{"en":"material","roots":["stainless","copper"]},"功能":{"en":"function","roots":["insulated","dishwasher"]},"尺寸":{"en":"size","roots":["large"]},"场景":{"en":"occasion","roots":["camping"]},"人群":{"en":"audience","roots":["teacher","nurse"]},"配件/结构":{"en":"parts","roots":["handle","lid"]}},"core_terms":[],"unknown":[]}
"""

def build_user_prompt(ranked_roots, root_examples):
    """构建词根列表 + 示例关键词"""
    lines = []
    for root, count in ranked_roots:
        examples = list(root_examples.get(root, []))[:2]
        ex_str = f" (如: {', '.join(examples)})" if examples else ""
        lines.append(f"  {root}({count}){ex_str}")
    return "词根列表（含出现次数和上下文示例）:\n" + "\n".join(lines)

async def classify_roots(ranked_roots, root_examples) -> dict:
    """调用 DeepSeek 分类词根"""
    user_prompt = build_user_prompt(ranked_roots, root_examples)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": FEW_SHOT_EXAMPLE},
            {"role": "assistant", "content": "收到，我会严格按照JSON格式输出分类结果。"},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"}
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            result = await resp.json()
            content = result["choices"][0]["message"]["content"]
            # 清理可能的 markdown 包裹
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return json.loads(content)

# ---- Step 3: 属性交叉组合 + 汇总 ----
def cross_combine_and_aggregate(keywords, classification, root_map):
    """
    对每个维度，按属性值过滤关键词子集，汇总指标。
    root_map: {root: dimension_en_name}
    """
    dims = classification.get("dimensions", {})
    core_terms = set(classification.get("core_terms", []))

    # 构建 root -> dimension mapping
    root_to_dim = {}
    dim_roots = {}  # dim_en -> [roots]
    for dim_cn, dim_info in dims.items():
        dim_en = dim_info.get("en", dim_cn)
        roots = dim_info.get("roots", [])
        dim_roots[dim_en] = roots
        for r in roots:
            root_to_dim[r.lower()] = dim_en

    results = {}  # dim_en -> [(attr_value, aggregate_dict)]

    KW_METRICS = {
        "search_volume": 2, "cpc": 3,
        "search_conversion_rate_d90": 4, "click_of_90d": 5,
        "sales_volume_of_90d": 6, "product_count": 7,
        "share_click_rate": 8, "share_conversion_rate": 9,
    }

    for dim_en, roots in dim_roots.items():
        dim_result = []
        for root in roots:
            # 筛选包含该词根的关键词
            matched = []
            for row in keywords:
                kw = row[1].lower()
                words = set(re.sub(r'[^a-z]', ' ', kw).split())
                if root in words:
                    matched.append(row)

            if len(matched) < 2:
                continue

            # 汇总
            agg = {
                "attr": root,
                "count": len(matched),
                "total_search_volume": sum(r[2] or 0 for r in matched),
                "total_click_90d": sum(r[5] or 0 for r in matched),
                "total_sales_90d": sum(r[6] or 0 for r in matched),
                "avg_cpc": 0,
                "avg_conv_rate": 0,
                "avg_share_click": 0,
                "avg_product_count": 0,
            }

            # 计算平均值
            valid_cpc = [r[3] for r in matched if r[3] and r[3] > 0]
            valid_conv = [r[4] for r in matched if r[4] and r[4] > 0]
            valid_share = [r[8] for r in matched if r[8] and r[8] > 0]
            valid_prod = [r[7] for r in matched if r[7] and r[7] > 0]

            agg["avg_cpc"] = round(sum(valid_cpc) / len(valid_cpc), 2) if valid_cpc else 0
            agg["avg_conv_rate"] = round(sum(valid_conv) / len(valid_conv), 4) if valid_conv else 0
            agg["avg_share_click"] = round(sum(valid_share) / len(valid_share), 2) if valid_share else 0
            agg["avg_product_count"] = round(sum(valid_prod) / len(valid_prod)) if valid_prod else 0

            # 需求强度指数 (简易版): 搜索量 / 商品数 的比值，越高越有机会
            agg["demand_intensity"] = round(
                agg["total_search_volume"] / max(agg["avg_product_count"], 1), 1
            )

            dim_result.append(agg)

        # 按搜索量倒序
        dim_result.sort(key=lambda x: -x["total_search_volume"])
        if dim_result:
            results[dim_en] = dim_result

    return results

# ---- Step 4: 输出报表 ----
def generate_report(classification, results, keywords):
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    category = classification.get("category", "Unknown")
    category_en = classification.get("category_en", "unknown")

    # CSV: 每个维度一个 sheet（用多文件）
    report_lines = []
    report_lines.append(f"# 赛道拆解报告: {category} ({category_en})")
    report_lines.append(f"## 总关键词数: {len(keywords)}")
    report_lines.append(f"## 属性维度数: {len(results)}\n")

    for dim_en, dim_results in results.items():
        report_lines.append(f"\n### {dim_en}")
        report_lines.append("| 属性值 | 关键词数 | 月搜索量 | 月点击 | 月销量 | 均CPC | 均转化率 | 均商品数 | 需求强度 |")
        report_lines.append("|--------|---------|---------|--------|--------|-------|---------|----------|---------|")
        for r in dim_results:
            report_lines.append(
                f"| {r['attr']} | {r['count']} | {r['total_search_volume']:,} | "
                f"{r['total_click_90d']:,} | {r['total_sales_90d']:,} | "
                f"${r['avg_cpc']:.2f} | {r['avg_conv_rate']*100:.2f}% | "
                f"{r['avg_product_count']:,} | {r['demand_intensity']} |"
            )

    report_md = "\n".join(report_lines)
    md_path = OUT_DIR / f"{category_en}_report.md"
    md_path.write_text(report_md, encoding="utf-8")
    print(f"\n  报告: {md_path}")

    # JSON 详细数据
    json_path = OUT_DIR / f"{category_en}_data.json"
    json.dump({
        "classification": classification,
        "results": {k: v for k, v in results.items()}
    }, json_path, ensure_ascii=False, indent=2)
    print(f"  数据: {json_path}")

    # 打印摘要
    print(f"\n=== 赛道: {category} ===")
    for dim_en, dim_results in list(results.items())[:6]:
        print(f"\n  [{dim_en}]")
        for r in dim_results[:5]:
            print(f"    {r['attr']:20s} 搜索量:{r['total_search_volume']:>10,}  "
                  f"关键词:{r['count']:>4}  需求强度:{r['demand_intensity']:>8}")

# ---- Main ----
async def main():
    print("=== Step 1: 拆词根 ===")
    keywords, ranked_roots, root_examples, root_counter = load_and_extract()

    print(f"\n=== Step 2: LLM 分类 {len(ranked_roots)} 个词根 ===")
    classification = await classify_roots(ranked_roots, root_examples)
    print(f"  赛道: {classification.get('category')}")
    dims = classification.get("dimensions", {})
    for dim_cn, info in dims.items():
        print(f"  {dim_cn}: {len(info.get('roots',[]))} 个词根")
    print(f"  核心词: {classification.get('core_terms', [])}")
    print(f"  未分类: {classification.get('unknown', [])}")

    print(f"\n=== Step 3: 属性交叉汇总 ===")
    root_map = {}
    for dim_cn, info in dims.items():
        for r in info.get("roots", []):
            root_map[r] = dim_cn
    results = cross_combine_and_aggregate(keywords, classification, root_map)

    print(f"\n=== Step 4: 输出报表 ===")
    generate_report(classification, results, keywords)

    print("\n=== 完成 ===")

if __name__ == "__main__":
    asyncio.run(main())
