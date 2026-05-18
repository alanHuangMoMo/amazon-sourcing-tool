"""
Tag 17,905 niches as medical/non-medical using DeepSeek V4.
- One niche per API call, 80 concurrent
- Each niche: keywords + top 3 ASIN titles (most frequent in niche)
- Output: {medical: bool, name: "赛道名"}
"""
import asyncio
import aiohttp
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.models import init_db, SessionLocal
from sqlalchemy import text

API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"
CONCURRENCY = 80

SYSTEM_PROMPT = """你是亚马逊产品分类助手。严格判断关键词+产品是否属于"医疗器械/医疗耗材"赛道。

医疗器械=用于诊断/治疗/预防/康复/护理的器械、设备、器具、耗材。包括：诊断监测设备、手术器械、牙科设备、呼吸设备、移动辅具、矫形护具、伤口护理产品（敷料/绷带/胶带）、注射器/输液器/导管、消毒灭菌设备、听力辅助、视力矫正、康复理疗设备、医用防护（口罩/手套/隔离衣）、医用家具、实验室设备、兽医器械。

以下不是医疗器械，必须判false：
1. 口服药物/药品（止痛药、止咳糖浆、含片、药丸、胶囊、冲剂）→ false
2. 外用药品（药膏、抗生素软膏、消炎贴膏、药油）→ false
3. 保健品/维生素/补剂/蛋白粉 → false
4. 眼药水/人工泪液/滴眼液（药品）→ false
5. 化妆品/护肤品/美容仪/洗发水/去屑/沐浴露 → false
6. 食品饮料/糖果/口香糖 → false
7. 卫生纸/湿巾/卫生巾/纸尿裤（日用消耗）→ false
8. 普通服装/饰品/普通口罩（非医用）→ false
9. 玩具/游戏/书籍/消费电子 → false
10. 普通家具/家纺/床垫/枕头 → false
11. 非康复类运动器材/健身设备 → false
12. 宠物食品/宠物零食（非药品）→ false
13. 戒烟产品（尼古丁替代品属于药品）→ false
14. 驱虫剂/杀虫剂 → false

关键区分：能拿在手里的"器械/工具/设备/耗材"→true；吃进去/涂上去/喝下去的"药品/补剂/食品/化妆品"→false。

name字段必须是该niche对应的具体产品品类名称（2-8字），禁止使用"医疗器械""医疗耗材""医疗设备"等笼统泛称。

必须输出JSON，不要任何其他文字：
{"medical": true, "name": "具体品类名"}
或
{"medical": false, "name": ""}"""

USER_TEMPLATE = """关键词: {keywords}
代表产品:
{products}"""


def build_niche_data():
    """Load all data into memory, compute top ASINs per niche efficiently."""
    print("Loading data into memory...")
    init_db()
    db = SessionLocal()

    # 1. Load niche keywords and ASINs
    rows = db.execute(text(
        'SELECT niche_id, keyword FROM niche_kw ORDER BY niche_id')).fetchall()
    niche_kw_map = {}
    for nid, kw in rows:
        if nid not in niche_kw_map:
            niche_kw_map[nid] = []
        niche_kw_map[nid].append(kw)

    rows = db.execute(text(
        'SELECT niche_id, asin FROM niche_asin ORDER BY niche_id')).fetchall()
    niche_asin_map = {}
    for nid, asin in rows:
        if nid not in niche_asin_map:
            niche_asin_map[nid] = []
        niche_asin_map[nid].append(asin)

    # 2. Load ABA: keyword -> [(asin1,title1), (asin2,title2), (asin3,title3)]
    print("Loading ABA data...")
    aba_rows = db.execute(text("""
        SELECT keyword, asin_1, asin_1_title,
               asin_2, asin_2_title,
               asin_3, asin_3_title
        FROM aba_report WHERE domain='CA'
          AND keyword IN (SELECT keyword FROM niche_kw)
    """)).fetchall()

    kw_to_asins = {}
    for r in aba_rows:
        kw = r[0]
        entries = []
        for i in range(3):
            asin = r[1 + i*2]
            title = r[2 + i*2]
            if asin and asin.strip():
                entries.append((asin, title or asin))
        if entries:
            kw_to_asins[kw] = entries

    # 3. Get niche metadata
    niche_rows = db.execute(text(
        'SELECT id, seed_keyword, keyword_count, asin_count FROM niche WHERE domain="CA" ORDER BY id'
    )).fetchall()

    db.close()
    print(f"  Keywords: {len(kw_to_asins)}")
    print(f"  Niches: {len(niche_rows)}")

    # 4. Compute top 3 ASINs per niche
    print("Computing top ASINs per niche...")
    niche_data = []
    for i, nr in enumerate(niche_rows):
        nid = nr[0]
        kws = niche_kw_map.get(nid, [])
        niche_asins = set(niche_asin_map.get(nid, []))

        asin_counter = Counter()
        asin_title = {}

        for kw in kws:
            for asin, title in kw_to_asins.get(kw, []):
                if asin in niche_asins:
                    asin_counter[asin] += 1
                    if asin not in asin_title:
                        asin_title[asin] = title

        top3 = asin_counter.most_common(3)
        products = [f"[{c}] {asin_title.get(a, a)[:200]}" for a, c in top3]

        niche_data.append({
            'id': nid,
            'seed': nr[1],
            'keywords': kws,
            'products': products,
            'kw_count': nr[2],
            'asin_count': nr[3],
        })

        if (i + 1) % 5000 == 0:
            print(f"  ... {i+1}/{len(niche_rows)}")

    print(f"Built {len(niche_data)} niche data packs")
    return niche_data


async def tag_niche(session, niche, sem):
    """Tag one niche. Retry up to 3 times."""
    kws_text = ", ".join(niche['keywords'][:50])
    products_text = "\n".join(niche['products'])
    prompt = USER_TEMPLATE.format(keywords=kws_text, products=products_text)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 50,
        "temperature": 0,
    }

    for attempt in range(3):
        try:
            async with sem:
                async with session.post(API_URL, json=payload,
                    headers={"Authorization": f"Bearer {API_KEY}",
                             "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)) as resp:

                    if resp.status == 429:
                        await asyncio.sleep(min(10, 2 ** attempt))
                        continue

                    data = await resp.json()
                    if "choices" not in data:
                        await asyncio.sleep(1)
                        continue

                    content = data["choices"][0]["message"]["content"]
                    return json.loads(content)

        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)

    return {"medical": False, "name": "", "error": "max retries"}


async def main():
    niche_data = build_niche_data()

    # Check if re-running on already-tagged niches (round 2+)
    prev_tags = {}
    if Path('data/niche_tags.json').exists():
        with open('data/niche_tags.json', 'r', encoding='utf-8') as f:
            prev = json.load(f)
        prev_tags = {p['id']: p for p in prev}
        # Only reprocess niches previously marked as medical
        prev_ids = {p['id'] for p in prev if p.get('medical')}
        niche_data = [n for n in niche_data if n['id'] in prev_ids]
        print(f"Re-running on {len(niche_data)} previously-medical niches")

    print(f"\nTagging {len(niche_data)} niches with {CONCURRENCY} concurrent...")
    t0 = time.time()

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [tag_niche(session, n, sem) for n in niche_data]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.0f}s ({len(niche_data)/elapsed:.1f} niches/s)")

    medical = sum(1 for r in results if r.get('medical'))
    non_med = sum(1 for r in results if not r.get('medical'))
    errors = sum(1 for r in results if 'error' in r)
    print(f"Results: {medical} medical, {non_med} non-medical, {errors} errors")

    # Merge with previous tags
    output = []
    for n, r in zip(niche_data, results):
        output.append({
            'id': n['id'],
            'seed': n['seed'],
            'kw_count': n['kw_count'],
            'medical': r.get('medical', False),
            'name': r.get('name', ''),
            'error': r.get('error', ''),
        })

    # Keep old results for niches we didn't re-process
    for pid, pt in prev_tags.items():
        if pid not in {n['id'] for n in niche_data}:
            output.append(pt)

    with open('data/niche_tags.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved to data/niche_tags.json")

    # Apply to DB
    print("\nApplying to database...")
    init_db()
    db = SessionLocal()

    try:
        db.execute(text('ALTER TABLE niche ADD COLUMN niche_name TEXT'))
        db.commit()
    except:
        pass

    # Reset all names first
    db.execute(text('UPDATE niche SET niche_name=NULL'))
    db.commit()

    updated = 0
    deleted = 0
    for item in output:
        if item['medical'] and item['name']:
            db.execute(text('UPDATE niche SET niche_name=:name WHERE id=:id'),
                       {'name': item['name'], 'id': item['id']})
            updated += 1
        elif not item['medical']:
            nid = item['id']
            db.execute(text('DELETE FROM niche_kw WHERE niche_id=:nid'), {'nid': nid})
            db.execute(text('DELETE FROM niche_asin WHERE niche_id=:nid'), {'nid': nid})
            db.execute(text('DELETE FROM niche WHERE id=:nid'), {'nid': nid})
            deleted += 1

    db.commit()
    remaining = db.execute(text('SELECT COUNT(*) FROM niche')).scalar()
    print(f"Updated: {updated}, Deleted: {deleted}, Remaining niches: {remaining}")

    samples = db.execute(text(
        "SELECT seed_keyword, niche_name FROM niche WHERE niche_name IS NOT NULL ORDER BY RANDOM() LIMIT 20"
    )).fetchall()
    print("\nSample niche names:")
    for s in samples:
        print(f"  {s[0][:45]:45s} -> {s[1]}")

    db.close()
    print("\nDone!")


if __name__ == '__main__':
    asyncio.run(main())
