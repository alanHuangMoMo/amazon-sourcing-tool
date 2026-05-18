"""
BOM 成本估算器：取 ASIN 标题 + 零售价 → DeepSeek 拆解 BOM → 推算 B2B 出厂价
每个产品独立调用一次 LLM。
"""
import sqlite3, json, time, re
from pathlib import Path
import requests

DB_PATH = Path(__file__).parent / "data" / "sourcing.db"
API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = """你是中国工厂的资深成本核算师。给你一个亚马逊白牌产品的信息（标题、类目），请从 BOM 逻辑出发估算该产品在中国工厂的 B2B 供货价（FOB 人民币价格）。

规则：
1. 所有产品按白牌/贴牌处理，无品牌溢价。你估算的是工厂卖给跨境卖家的供货价
2. 不要考虑品牌授权费、设计费、版权费。标准品直接给白牌市场行情
3. 金额全部为整数人民币（元），不要小数
4. 不要参考任何零售价——只从材料、工艺、包装角度推算

分析维度：
1. 原材料：推断主材及用量 × 市场单价
2. 加工：注塑/缝制/组装/灌装/表面处理等工序费
3. 包装：内包装+外箱，是否彩盒
4. 模具：有定制开模则按首单摊销（无则填0）

行情锚定参考（白牌 FOB 价）：
- 304不锈钢保温杯 500ml：¥18-25
- 纯棉 T恤 180g：¥12-18
- 硅胶厨具套装 5件：¥15-22
- 无纺布收纳盒 6件：¥8-12
- 瑜伽垫 6mm TPE：¥22-28

输出纯 JSON（不要 markdown 包裹，不要注释，字符串内不要换行）：
{"bom":{"material":{"detail":"主材+用量","cost_rmb":0},"labor":{"detail":"工序","cost_rmb":0},"packaging":{"detail":"方式","cost_rmb":0},"mold":{"detail":"说明","cost_rmb":0}},"factory_price_rmb":{"min":0,"max":0},"confidence":"high/medium/low","basis":""}"""


def sample_asins(n: int = 15) -> list[dict]:
    """从数据库抽取不同类目、有标题和价格的 ASIN 样本"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 取类目分布
    cur.execute("""
        SELECT main_category, COUNT(*) as cnt
        FROM sellersprite_product
        WHERE title IS NOT NULL AND price > 0 AND main_category IS NOT NULL AND main_category != ''
        GROUP BY main_category
        ORDER BY cnt DESC
        LIMIT 10
    """)
    cats = cur.fetchall()
    print(f"Top categories: {', '.join(f'{c[0]}({c[1]})' for c in cats[:6])}")

    samples = []
    for cat, _ in cats[:8]:
        cur.execute("""
            SELECT asin, title, price, main_category, monthly_sales, brand
            FROM sellersprite_product
            WHERE main_category = ? AND title IS NOT NULL AND price > 0
            ORDER BY monthly_sales DESC
            LIMIT 3
        """, (cat,))
        for r in cur.fetchall():
            samples.append({
                "asin": r[0], "title": r[1], "price": r[2],
                "category": r[3], "sales": r[4], "brand": r[5],
            })
        if len(samples) >= n:
            break

    conn.close()
    # 去重 + 截断
    seen = set()
    unique = []
    for s in samples:
        if s["asin"] not in seen:
            seen.add(s["asin"])
            unique.append(s)
    return unique[:n]


def estimate_bom(product: dict) -> dict:
    """调用 DeepSeek 估算单个产品的 BOM 出厂价"""
    user_msg = json.dumps({
        "product": product["title"],
        "category": product["category"],
    }, ensure_ascii=False)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}"}
            data = resp.json()
            if "error" in data:
                return {"error": str(data["error"])}
            content = data["choices"][0]["message"]["content"].strip()
            # Clean markdown wrapping
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            # Try to fix truncated JSON: find last valid closing brace
            if not content.endswith('}'):
                last_brace = content.rfind('}')
                if last_brace > 0:
                    content = content[:last_brace+1]
            return json.loads(content)
        except json.JSONDecodeError as e:
            last_error = e
            # Retry with higher temperature to get different output
            payload["temperature"] = 0.3 + attempt * 0.2
            time.sleep(0.5)
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"JSON parse failed after 3 retries: {last_error}"}


def main():
    print("=== BOM 成本估算器 ===\n")

    products = sample_asins(15)
    print(f"Sampled {len(products)} ASINs across categories\n")

    results = []
    for i, p in enumerate(products):
        print(f"[{i+1}/{len(products)}] {p['title'][:70]}... (${p['price']:.2f})")
        r = estimate_bom(p)
        if "error" in r:
            print(f"  ERROR: {r['error']}")
        elif "factory_price_rmb" in r:
            fr = r["factory_price_rmb"]
            # Calculate cost_ratio in code (not from LLM): B2B max / (retail USD * 7.2)
            retail_rmb = p["price"] * 7.2
            cost_ratio = round(fr["max"] / retail_rmb, 2) if retail_rmb > 0 else 0
            r["cost_ratio"] = cost_ratio
            conf = r.get("confidence", "?")
            print(f"  B2B: {fr['min']:.0f}-{fr['max']:.0f} RMB | ratio: {cost_ratio:.2f} | conf: {conf}")
        else:
            print(f"  UNEXPECTED: {json.dumps(r, ensure_ascii=False)[:200]}")
        results.append({"product": p, "estimation": r})
        time.sleep(0.5)  # 避免限速

    # 输出汇总
    print(f"\n{'='*100}")
    print(f"{'Product':<40s} {'Retail':>8s} {'Factory(RMB)':>15s} {'Ratio':>8s} {'Conf':>6s}")
    print(f"{'-'*100}")
    for r in results:
        p = r["product"]
        e = r.get("estimation", {})
        fr = e.get("factory_price_rmb", {})
        price_str = f"${p['price']:.2f}"
        if fr:
            factory_str = f"{fr['min']:.0f}-{fr['max']:.0f}"
        else:
            factory_str = "N/A"
        ratio = e.get("cost_ratio", 0)
        ratio_str = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else str(ratio)
        conf = e.get("confidence", "N/A")
        title = p["title"][:38]
        print(f"{title:<40s} {price_str:>8s} {factory_str:>15s} {ratio_str:>8s} {conf:>6s}")

    # Save
    outpath = Path(__file__).parent / "data" / "bom_estimates.json"
    outpath.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {outpath}")


if __name__ == "__main__":
    main()
