"""
跨标签 ASIN 去重：用 Jaccard 重叠解决一个 ASIN 出现在多个标签的问题。

逻辑：
  对每个 ASIN，取它在 ABA 中关联的关键词集合
  对每个候选标签，取该标签的关键词集合
  计算 Jaccard = |交集| / |并集|
  归属到 Jaccard 最高的标签（分差 <0.1 时保留多个）

输出：更新 merged_niche_asin（移除不属于的 ASIN），去重报告
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.models import init_db, SessionLocal
from sqlalchemy import text

db = SessionLocal()

# === 1. 加载合并后数据 ===
rows = db.execute(text("SELECT id, tag, keywords_json, asins_json FROM merged_niche")).fetchall()
niches = []
for mid, tag, kw_json, asin_json in rows:
    niches.append({
        "id": mid,
        "tag": tag,
        "keywords": set(json.loads(kw_json)),
        "asins": set(json.loads(asin_json)),
    })

# === 2. 加载 ABA 关键词→ASIN 映射 ===
print("加载 ABA 数据...")
aba_rows = db.execute(text("""
    SELECT keyword, asin_1, asin_2, asin_3
    FROM aba_report WHERE domain='CA'
""")).fetchall()

kw_to_asins = defaultdict(set)
for kw, a1, a2, a3 in aba_rows:
    for a in (a1, a2, a3):
        if a and a.strip():
            kw_to_asins[kw].add(a.strip())

# 反查：ASIN → 关键词
asin_to_kws = defaultdict(set)
for kw, asins in kw_to_asins.items():
    for a in asins:
        asin_to_kws[a].add(kw)

# === 3. 找多标签 ASIN ===
tag_asins = {}
for n in niches:
    tag_asins[n["id"]] = n["asins"]

asin_tags = defaultdict(set)
for n in niches:
    for a in n["asins"]:
        asin_tags[a].add(n["id"])

multi_asin = {a: tags for a, tags in asin_tags.items() if len(tags) > 1}
print(f"多标签 ASIN: {len(multi_asin)}/{len(asin_tags)}")

# === 4. Jaccard 去重 ===
def jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0
    return len(set_a & set_b) / len(set_a | set_b)

removed = defaultdict(list)   # niche_id → [removed asins]
conflicts = []                # 两个标签得分接近的

for asin, candidate_ids in multi_asin.items():
    asin_kws = asin_to_kws.get(asin, set())
    if not asin_kws:
        continue

    scores = []
    for nid in candidate_ids:
        niche_kws = None
        for n in niches:
            if n["id"] == nid:
                niche_kws = n["keywords"]
                break
        if niche_kws:
            scores.append((nid, jaccard(asin_kws, niche_kws)))

    if not scores:
        continue

    scores.sort(key=lambda x: -x[1])
    best_score = scores[0][1]
    keep_ids = [scores[0][0]]

    # 分差 < 0.1 的也保留
    for nid, score in scores[1:]:
        if best_score - score < 0.1 and score > 0:
            keep_ids.append(nid)

    if len(scores) > 1 and best_score - scores[1][1] < 0.1:
        conflicts.append({
            "asin": asin,
            "scores": [(nid, round(s, 4)) for nid, s in scores],
            "keep": keep_ids,
        })

    for nid in candidate_ids:
        if nid not in keep_ids:
            removed[nid].append(asin)

# === 5. 统计 ===
total_removed = sum(len(v) for v in removed.values())
print(f"\n=== 去重结果 ===")
print(f"从 {len(removed)} 个 niche 移除 {total_removed} 个 ASIN 归属")
print(f"Jaccard 分差<0.1 的冲突: {len(conflicts)}")
print(f"理想去重率: {total_removed}/{len(multi_asin)} = {total_removed/len(multi_asin)*100:.1f}%")

# 移除最多的 niche
top_removed = sorted(removed.items(), key=lambda x: -len(x[1]))[:10]
print(f"\n移除 ASIN 最多的 10 个 niche:")
for nid, asins in top_removed:
    tag = next((n["tag"] for n in niches if n["id"] == nid), "?")
    print(f"  {tag}: -{len(asins)} ASINs")

# 保留冲突详情
out = {
    "multi_asin_count": len(multi_asin),
    "total_removed": total_removed,
    "niches_affected": len(removed),
    "conflict_count": len(conflicts),
    "removed_by_niche": {str(k): v for k, v in removed.items()},
    "conflicts": conflicts,
}
Path("data/dedup_report.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

# === 6. 更新 DB ===
print(f"\n更新 merged_niche_asin 表...")
for nid, asins_to_remove in removed.items():
    remove_set = set(asins_to_remove)
    for a in remove_set:
        db.execute(text(
            "DELETE FROM merged_niche_asin WHERE merged_niche_id=:nid AND asin=:a"
        ), {"nid": nid, "a": a})

# 更新合并表的 asin_count
for n in niches:
    count = db.execute(text(
        "SELECT COUNT(*) FROM merged_niche_asin WHERE merged_niche_id=:nid"
    ), {"nid": n["id"]}).scalar()
    db.execute(text(
        "UPDATE merged_niche SET asin_count=:c WHERE id=:nid"
    ), {"c": count, "nid": n["id"]})

db.commit()
print("DB 更新完成")

# === 7. 去重后规模分布 ===
rows = db.execute(text(
    "SELECT asin_count, COUNT(*) FROM merged_niche GROUP BY 1 ORDER BY 1"
)).fetchall()
print(f"\n去重后 niche 规模分布:")
dist = defaultdict(int)
for cnt, n in rows:
    if cnt <= 5:
        dist["1-5"] += n
    elif cnt <= 15:
        dist["6-15"] += n
    elif cnt <= 30:
        dist["16-30"] += n
    else:
        dist["31+"] += n
for k in ["1-5", "6-15", "16-30", "31+"]:
    print(f"  {k} ASINs: {dist[k]}")

db.close()
print("\nDone. Report: data/dedup_report.json")
