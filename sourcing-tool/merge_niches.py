"""
Step 1: 按 DeepSeek 标签合并小 niche 为大 niche。

同一 niche_name 下的所有 niche 合并为一个：
- 关键词去重并集
- ASIN 去重并集
- 保留原 niche 的种子词供参考

输出: data/merged_niches.json + merged_niche / merged_niche_kw / merged_niche_asin 表
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.models import init_db, SessionLocal
from sqlalchemy import text

db = SessionLocal()

# === 1. 按 niche_name 合并 ===
rows = db.execute(text("""
    SELECT n.id, n.seed_keyword, n.niche_name, n.keyword_count, n.asin_count
    FROM niche n
    WHERE n.niche_name IS NOT NULL
    ORDER BY n.niche_name, n.keyword_count DESC
""")).fetchall()

merged: dict[str, dict] = {}
for nid, seed, name, kw_cnt, asin_cnt in rows:
    if name not in merged:
        merged[name] = {
            "tag": name,
            "niche_ids": [],
            "seeds": [],
            "total_kw": 0,
            "total_asin": 0,
        }
    merged[name]["niche_ids"].append(nid)
    merged[name]["seeds"].append(seed)
    merged[name]["total_kw"] += kw_cnt
    merged[name]["total_asin"] += asin_cnt

for name in merged:
    ids = merged[name]["niche_ids"]
    id_params = {f"id{i}": v for i, v in enumerate(ids)}
    placeholders = ",".join(f":id{i}" for i in range(len(ids)))

    kws = db.execute(text(
        f"SELECT DISTINCT keyword FROM niche_kw WHERE niche_id IN ({placeholders})"
    ), id_params).fetchall()
    merged[name]["keywords"] = sorted(kw[0] for kw in kws)

    asins = db.execute(text(
        f"SELECT DISTINCT asin FROM niche_asin WHERE niche_id IN ({placeholders})"
    ), id_params).fetchall()
    merged[name]["asins"] = sorted(a[0] for a in asins)

    merged[name]["kw_count"] = len(merged[name]["keywords"])
    merged[name]["asin_count"] = len(merged[name]["asins"])

# === 2. 统计 ===
sorted_merged = sorted(merged.values(), key=lambda x: -x["asin_count"])
print(f"合并前: {len(rows)} 个小 niche")
print(f"合并后: {len(sorted_merged)} 个大 niche\n")

print(f"{'赛道标签':30s} {'原niche数':>8s} {'Keywords':>10s} {'ASINs':>8s}")
print("-" * 60)
for m in sorted_merged[:30]:
    print(f"{m['tag']:30s} {len(m['niche_ids']):8d} {m['kw_count']:10d} {m['asin_count']:8d}")

# === 3. 分布统计 ===
size_dist = defaultdict(int)
for m in sorted_merged:
    if m["asin_count"] <= 5:
        size_dist["1-5 ASINs"] += 1
    elif m["asin_count"] <= 15:
        size_dist["6-15 ASINs"] += 1
    elif m["asin_count"] <= 30:
        size_dist["16-30 ASINs"] += 1
    elif m["asin_count"] <= 100:
        size_dist["31-100 ASINs"] += 1
    else:
        size_dist[">100 ASINs"] += 1

print(f"\n=== 合并后规模分布 ===")
for bucket in ["1-5 ASINs", "6-15 ASINs", "16-30 ASINs", "31-100 ASINs", ">100 ASINs"]:
    print(f"  {bucket}: {size_dist[bucket]}")

# === 4. 保存 JSON ===
output = {"merged_count": len(sorted_merged), "niches": sorted_merged}
Path("data/merged_niches.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved to data/merged_niches.json")

# === 5. 写 DB 表 ===
from app.models import engine, Base
from sqlalchemy import Column, String, Text, Integer, DateTime
from datetime import datetime, timezone

class MergedNiche(Base):
    __tablename__ = "merged_niche"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tag = Column(String, nullable=False, index=True)
    original_niche_count = Column(Integer)
    keyword_count = Column(Integer)
    asin_count = Column(Integer)
    keywords_json = Column(Text)
    asins_json = Column(Text)
    seeds_json = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class MergedNicheKw(Base):
    __tablename__ = "merged_niche_kw"
    id = Column(Integer, primary_key=True, autoincrement=True)
    merged_niche_id = Column(Integer, nullable=False, index=True)
    keyword = Column(String, nullable=False)

class MergedNicheAsin(Base):
    __tablename__ = "merged_niche_asin"
    id = Column(Integer, primary_key=True, autoincrement=True)
    merged_niche_id = Column(Integer, nullable=False, index=True)
    asin = Column(String, nullable=False)

# 清除旧数据
for t in [MergedNicheAsin, MergedNicheKw, MergedNiche]:
    t.__table__.drop(engine, checkfirst=True)
    t.__table__.create(engine)

for m in sorted_merged:
    mn = MergedNiche(
        tag=m["tag"],
        original_niche_count=len(m["niche_ids"]),
        keyword_count=m["kw_count"],
        asin_count=m["asin_count"],
        keywords_json=json.dumps(m["keywords"], ensure_ascii=False),
        asins_json=json.dumps(m["asins"], ensure_ascii=False),
        seeds_json=json.dumps(m["seeds"], ensure_ascii=False),
    )
    db.add(mn)
    db.flush()
    for kw in m["keywords"]:
        db.add(MergedNicheKw(merged_niche_id=mn.id, keyword=kw))
    for a in m["asins"]:
        db.add(MergedNicheAsin(merged_niche_id=mn.id, asin=a))

db.commit()
print(f"DB: merged_niche ({len(sorted_merged)}), merged_niche_kw, merged_niche_asin 已写入")
db.close()
