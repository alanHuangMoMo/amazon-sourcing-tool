"""
Split overlapping niches into finer sub-niches using DeepSeek.
Each niche: keywords + top ASIN titles -> LLM splits into sub-niches.
Output stored in sub_niche table for review.
"""
import asyncio, aiohttp, json, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.models import init_db, SessionLocal, Base, engine
from sqlalchemy import text, Column, String, Text, Integer, DateTime, Float
from datetime import datetime, timezone

API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"
CONCURRENCY = 80

SYSTEM_PROMPT = """你是亚马逊产品分类专家。给你一个niche的ASIN产品标题和关键词列表，将其拆分成更精确的细分赛道。

分两步：

第一步：根据ASIN标题将产品分组。
- 功能相同、可互相替代的产品归为一组
- 每个组至少2个ASIN，一个ASIN只能属于一个组
- 注意区分：功能结构不同=不同赛道（如"护膝"和"髌骨带"分开）
- 不要区分：仅尺寸/颜色/数量/pack数/容量不同的产品合并
- 无法归组的ASIN忽略

第二步：将关键词分配到各ASIN组。
关键词有四种类型，分配规则不同：
1. 人群倾向词（含性别/年龄/人群限定词，如"for women""kids""seniors"）：根据ASIN组的目标用户分配
2. 功能属性词（含功能/结构/材质差异词，如"compression""hinged""silicone"）：归入功能对应的ASIN组
3. 尺寸差异词（含尺寸/pack数/容量词，如"3 pack""large""500ml"）：这种差异不创建新组，归入对应ASIN组
4. 中性通用词（通用产品名，无人群/功能/尺寸限定，如"knee brace""blood pressure monitor"）：★如果本niche被切成了多个子赛道，中性词必须同时放入每一个子赛道★

输出JSON，不要加任何其他文字：
{"sub_niches":[{"name":"2-6字中文赛道名","asins":["B0xxx"],"keywords":["kw1","kw2"]}]}"""

USER_TEMPLATE = """ASIN产品标题:
{products}

关键词列表: {keywords}

请拆分成细分赛道:"""


def build_niche_data():
    """Same as before - load everything into memory."""
    print("Loading data...")
    init_db()
    db = SessionLocal()

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

    niche_rows = db.execute(text(
        'SELECT id, seed_keyword, keyword_count, asin_count, niche_name FROM niche WHERE niche_name IS NOT NULL ORDER BY id'
    )).fetchall()

    db.close()
    print(f"  Niches: {len(niche_rows)}")

    niche_data = []
    for nr in niche_rows:
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

        top = asin_counter.most_common(20)
        products = [f"{a}: {asin_title.get(a, a)[:200]}" for a, c in top]

        niche_data.append({
            'id': nid,
            'name': nr[4],
            'keywords': kws,
            'products': products,
        })

    print(f"Built {len(niche_data)} niche packs")
    return niche_data


async def split_niche(session, niche, sem):
    kws_text = ", ".join(niche['keywords'])
    products_text = "\n".join(niche['products'])
    prompt = USER_TEMPLATE.format(keywords=kws_text, products=products_text)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1000,
        "temperature": 0,
    }

    for attempt in range(3):
        try:
            async with sem:
                async with session.post(API_URL, json=payload,
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()
                    if "choices" not in data:
                        await asyncio.sleep(1)
                        continue
                    content = data["choices"][0]["message"]["content"]
                    result = json.loads(content)
                    result['niche_id'] = niche['id']
                    result['niche_name'] = niche['name']
                    return result
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)

    return {"niche_id": niche['id'], "niche_name": niche['name'], "sub_niches": [], "error": "max retries"}


async def main():
    niche_data = build_niche_data()

    print(f"\nSplitting {len(niche_data)} niches with {CONCURRENCY} concurrent...")
    t0 = time.time()

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [split_niche(session, n, sem) for n in niche_data]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.0f}s ({len(niche_data)/elapsed:.1f} niches/s)")

    # Stats
    total_subs = sum(len(r.get('sub_niches', [])) for r in results)
    print(f"Total sub-niches: {total_subs}")
    empty = sum(1 for r in results if not r.get('sub_niches'))
    print(f"Niches with 0 sub-niches: {empty}")

    # Save results
    with open('data/sub_niche_split.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Saved to data/sub_niche_split.json")

    # Create DB table
    init_db()
    db = SessionLocal()

    db.execute(text('DROP TABLE IF EXISTS sub_niche_asin'))
    db.execute(text('DROP TABLE IF EXISTS sub_niche_kw'))
    db.execute(text('DROP TABLE IF EXISTS sub_niche'))
    db.commit()

    class SubNiche(Base):
        __tablename__ = 'sub_niche'
        id = Column(Integer, primary_key=True, autoincrement=True)
        parent_niche_id = Column(Integer, nullable=False, index=True)
        parent_niche_name = Column(String)
        name = Column(String, nullable=False)
        keyword_count = Column(Integer, default=0)
        asin_count = Column(Integer, default=0)
        created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    class SubNicheKw(Base):
        __tablename__ = 'sub_niche_kw'
        id = Column(Integer, primary_key=True, autoincrement=True)
        sub_niche_id = Column(Integer, nullable=False, index=True)
        keyword = Column(String, nullable=False)

    class SubNicheAsin(Base):
        __tablename__ = 'sub_niche_asin'
        id = Column(Integer, primary_key=True, autoincrement=True)
        sub_niche_id = Column(Integer, nullable=False, index=True)
        asin = Column(String, nullable=False)

    SubNiche.__table__.create(engine)
    SubNicheKw.__table__.create(engine)
    SubNicheAsin.__table__.create(engine)

    total_sub = 0
    total_kw = 0
    total_asin = 0
    for r in results:
        for sn in r.get('sub_niches', []):
            kws = sn.get('keywords', [])
            asins = sn.get('asins', [])
            if not asins:
                continue
            sn_row = SubNiche(
                parent_niche_id=r['niche_id'],
                parent_niche_name=r.get('niche_name', ''),
                name=sn.get('name', ''),
                keyword_count=len(kws),
                asin_count=len(asins),
            )
            db.add(sn_row)
            db.flush()

            for kw in kws:
                db.add(SubNicheKw(sub_niche_id=sn_row.id, keyword=kw))
                total_kw += 1
            for a in asins:
                db.add(SubNicheAsin(sub_niche_id=sn_row.id, asin=a))
                total_asin += 1
            total_sub += 1

    db.commit()

    sub_count = db.execute(text('SELECT COUNT(*) FROM sub_niche')).scalar()
    unique_kw = db.execute(text('SELECT COUNT(DISTINCT keyword) FROM sub_niche_kw')).scalar()
    unique_asin = db.execute(text('SELECT COUNT(DISTINCT asin) FROM sub_niche_asin')).scalar()

    print(f"\n=== Sub-niche table ===")
    print(f"Sub-niches: {sub_count}")
    print(f"Keywords: {unique_kw} unique ({total_kw} total)")
    print(f"ASINs: {unique_asin} unique ({total_asin} total)")
    if sub_count:
        print(f"Avg: {total_kw/sub_count:.1f} kw, {total_asin/sub_count:.1f} asin per sub-niche")

    db.close()
    print("\nDone!")


if __name__ == '__main__':
    asyncio.run(main())
