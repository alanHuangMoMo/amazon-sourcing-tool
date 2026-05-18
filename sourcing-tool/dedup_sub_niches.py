"""
跨子赛道去重：用嵌入距离做最终 ASIN 归属。
丢掉父赛道层级，输出扁平的不重叠子赛道清单。
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from cluster_niche import embed_titles, get_asin_titles
from app.models import init_db, SessionLocal, engine, Base
from sqlalchemy import text, Column, String, Text, Integer, DateTime, Float
from datetime import datetime, timezone

db = SessionLocal()

# === 1. 加载有效子赛道 ===
rows = db.execute(text("""
    SELECT sn.id, sn.parent_tag, sn.name, sn.size
    FROM sub_niche sn
    WHERE sn.belongs_to_niche = 1
    ORDER BY sn.size DESC
""")).fetchall()

sub_asins = {}
for r in rows:
    sid = r[0]
    asin_rows = db.execute(text(
        "SELECT asin FROM sub_niche_asin WHERE sub_niche_id=:sid"
    ), {"sid": sid}).fetchall()
    sub_asins[sid] = [a[0] for a in asin_rows]

# 收集所有 ASIN
all_asins = set()
for asins in sub_asins.values():
    all_asins.update(asins)

# 获取标题
titles = get_asin_titles(db, all_asins)
print(f"ASIN 标题覆盖率: {len(titles)}/{len(all_asins)}")

# === 2. 计算每个子赛道中心点 ===
print(f"\n计算 {len(rows)} 个子赛道中心点...")
sub_centroids = {}
sub_info = {}

sid_to_title_list = {}
for r in rows:
    sid, tag, name, size = r
    asins = sub_asins.get(sid, [])
    title_list = [titles.get(a) for a in asins if a in titles]
    if not title_list:
        continue
    sid_to_title_list[sid] = title_list

# 嵌入所有子赛道 ASIN（一起嵌避免多次加载模型）
all_title_lists = list(sid_to_title_list.values())
all_titles_flat = [t for lst in all_title_lists for t in lst]
print(f"嵌入 {len(all_titles_flat)} 个标题...")
all_embs = embed_titles(all_titles_flat)

# 分回各子赛道
idx = 0
for sid, title_list in sid_to_title_list.items():
    n = len(title_list)
    centroid = all_embs[idx:idx+n].mean(axis=0)
    # L2 归一化保持同分布
    centroid = centroid / np.linalg.norm(centroid)
    sub_centroids[sid] = centroid
    sub_info[sid] = {
        "parent_tag": next(r[1] for r in rows if r[0] == sid),
        "name": next(r[2] for r in rows if r[0] == sid),
        "size": n,
    }
    idx += n

print(f"有效子赛道（有嵌入）: {len(sub_centroids)}")

# === 3. 找重叠 ASIN 并重新分配 ===
print(f"\n检测 ASIN 重叠...")
asin_to_subs = defaultdict(set)
for sid, asins in sub_asins.items():
    for a in asins:
        asin_to_subs[a].add(sid)

multi_asin = {a: subs for a, subs in asin_to_subs.items() if len(subs) > 1 and a in titles}
print(f"重叠 ASIN: {len(multi_asin)}")
sub_removals = defaultdict(list)

# 嵌入所有重叠 ASIN
multi_titles = [titles[a] for a in multi_asin]
multi_embs = embed_titles(multi_titles)

# 计算距离
centroid_ids = list(sub_centroids.keys())
centroid_matrix = np.stack([sub_centroids[sid] for sid in centroid_ids])

for a, emb in zip(multi_asin, multi_embs):
    candidate_sids = multi_asin[a]
    # 只跟候选子赛道比距离
    candidate_indices = [centroid_ids.index(sid) for sid in candidate_sids]
    candidate_centroids = centroid_matrix[candidate_indices]
    # cosine 距离 = 1 - dot (因为已 L2 归一化)
    distances = 1 - np.dot(candidate_centroids, emb)
    best_idx = candidate_indices[np.argmin(distances)]
    best_sid = centroid_ids[best_idx]

    for sid in candidate_sids:
        if sid != best_sid:
            sub_removals[sid].append(a)

total_removed = sum(len(v) for v in sub_removals.values())
print(f"从 {len(sub_removals)} 个子赛道移除 {total_removed} 个 ASIN")

# === 4. 生成最终扁平清单 ===
print(f"\n生成最终子赛道...")
final_subs = []

for r in rows:
    sid, tag, name, size = r
    asins = set(sub_asins.get(sid, []))
    # 移除不属于的
    for a in sub_removals.get(sid, []):
        asins.discard(a)

    if len(asins) < 2:
        # 去重后剩余<2个 ASIN，合并到最近的子赛道
        continue

    final_subs.append({
        "id": sid,
        "name": name or tag,
        "original_parent": tag,
        "asin_count": len(asins),
        "asins": sorted(asins),
    })

# 按 ASIN 数排序
final_subs.sort(key=lambda x: -x["asin_count"])

# 统计
total_asins_final = sum(s["asin_count"] for s in final_subs)
unique_asins_final = len(set(a for s in final_subs for a in s["asins"]))

print(f"\n=== 最终结果 ===")
print(f"去重前: {sum(1 for r in rows if r[0] in sub_info)} 个子赛道")
print(f"去重后: {len(final_subs)} 个子赛道")
print(f"ASIN 总出现: {total_asins_final}")
print(f"唯一 ASIN: {unique_asins_final}")
print(f"重叠率: {(total_asins_final - unique_asins_final)/unique_asins_final*100:.1f}%")

# 规模分布
size_dist = defaultdict(int)
for s in final_subs:
    if s["asin_count"] <= 3:
        size_dist["2-3"] += 1
    elif s["asin_count"] <= 10:
        size_dist["4-10"] += 1
    elif s["asin_count"] <= 30:
        size_dist["11-30"] += 1
    else:
        size_dist["31+"] += 1
print(f"\n规模分布:")
for k in ["2-3", "4-10", "11-30", "31+"]:
    print(f"  {k} ASINs: {size_dist[k]}")

# === 5. 保存 JSON + DB ===
out = {
    "sub_niche_count": len(final_subs),
    "unique_asins": unique_asins_final,
    "sub_niches": final_subs,
}
Path("data/final_sub_niches.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

# DB
class FinalSubNiche(Base):
    __tablename__ = "final_sub_niche"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    original_parent = Column(String)
    asin_count = Column(Integer)
    asins_json = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class FinalSubNicheAsin(Base):
    __tablename__ = "final_sub_niche_asin"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sub_niche_id = Column(Integer, nullable=False, index=True)
    asin = Column(String, nullable=False)

for t in [FinalSubNicheAsin, FinalSubNiche]:
    t.__table__.drop(engine, checkfirst=True)
    t.__table__.create(engine)

for i, s in enumerate(final_subs):
    fn = FinalSubNiche(
        id=s["id"],
        name=s["name"],
        original_parent=s["original_parent"],
        asin_count=s["asin_count"],
        asins_json=json.dumps(s["asins"], ensure_ascii=False),
    )
    db.add(fn)
    db.flush()
    for a in s["asins"]:
        db.add(FinalSubNicheAsin(sub_niche_id=fn.id, asin=a))

db.commit()
db.close()
print(f"\nSaved: data/final_sub_niches.json + DB tables")
print("Done!")
