"""
全量聚类：批量处理去重后的所有赛道。

流程:
  1. 取所有 ≥6 ASIN 的 merged_niche
  2. 逐赛道: 嵌入 → HDBSCAN → 关键词分配（不上 LLM，先本地跑完）
  3. 批量并发调 LLM 命名+纠错
  4. 结果写入 sub_niche 表
"""
import asyncio
import aiohttp
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cluster_niche import (
    embed_titles, get_asin_titles, cluster_asins, assign_keywords,
    CLUSTER_PROMPT, API_KEY, API_URL, MODEL,
)
from app.models import init_db, SessionLocal, engine, Base
from sqlalchemy import text, Column, String, Text, Integer, DateTime, Boolean, Float
from datetime import datetime, timezone

LLM_CONCURRENCY = 15


# === DB 表 ===
class SubNiche(Base):
    __tablename__ = "sub_niche"
    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_tag = Column(String, nullable=False, index=True)
    merged_niche_id = Column(Integer, nullable=False, index=True)
    name = Column(String)
    belongs_to_niche = Column(Boolean, default=True)
    is_coherent = Column(Boolean, default=True)
    size = Column(Integer, default=0)
    keyword_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SubNicheKw(Base):
    __tablename__ = "sub_niche_kw"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sub_niche_id = Column(Integer, nullable=False, index=True)
    keyword = Column(String, nullable=False)


class SubNicheAsin(Base):
    __tablename__ = "sub_niche_asin"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sub_niche_id = Column(Integer, nullable=False, index=True)
    asin = Column(String, nullable=False)


def _ensure_tables():
    for t in [SubNicheAsin, SubNicheKw, SubNiche]:
        t.__table__.drop(engine, checkfirst=True)
        t.__table__.create(engine)


async def _call_llm_batch(batch: list[dict], sem) -> list[dict]:
    """并发调 LLM。每批最多 5 个赛道一起发给 LLM。"""
    async def call_one(session, item, sem):
        clusters = item["clusters"]
        kws_map = item["cluster_kws"]

        samples = []
        for c in clusters:
            titles = [a["title"][:120] for a in c["asins"][:10]]
            kws = kws_map.get(str(c["cluster_id"]), [])[:20]
            samples.append({
                "id": c["cluster_id"],
                "size": c["size"],
                "sample_titles": titles,
                "sample_keywords": kws,
            })

        prompt = json.dumps({"clusters": samples}, ensure_ascii=False, indent=2)
        user_msg = f"赛道「{item['tag']}」的聚类结果:\n{prompt}"

        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": CLUSTER_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 2000,
            "temperature": 0,
        }

        async with sem:
            for attempt in range(3):
                try:
                    async with session.post(
                        API_URL,
                        json=payload,
                        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        data = await resp.json()
                        if "choices" in data:
                            return {
                                "tag": item["tag"],
                                "result": json.loads(data["choices"][0]["message"]["content"]),
                            }
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(2)
            return {"tag": item["tag"], "result": {"clusters": []}, "error": "max retries"}

    sem = asyncio.Semaphore(LLM_CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [call_one(session, item, sem) for item in batch]
        return await asyncio.gather(*tasks)


def main():
    print("=== 全量聚类 Pipeline ===\n")
    init_db()
    _ensure_tables()
    db = SessionLocal()

    # 1. 取所有 >5 ASIN 的赛道
    rows = db.execute(text(
        "SELECT id, tag, keyword_count, asin_count, asins_json FROM merged_niche WHERE asin_count > 5 ORDER BY asin_count DESC"
    )).fetchall()

    print(f"待聚类赛道: {len(rows)} 个 (≥6 ASIN)")
    all_niches = []
    for mid, tag, kw_cnt, asin_cnt, asins_json in rows:
        all_niches.append({
            "id": mid,
            "tag": tag,
            "kw_count": kw_cnt,
            "asin_count": asin_cnt,
            "asins": json.loads(asins_json),
        })

    # 2. 逐赛道聚类（本地，不调 LLM）
    print(f"\n--- Phase 1: 嵌入 + 聚类 ---")
    cluster_data = []  # 需要调 LLM 的
    no_cluster_needed = []  # 只有 1 个簇或无需拆分的

    for i, niche in enumerate(all_niches):
        tag = niche["tag"]
        asins = niche["asins"]
        print(f"  [{i+1}/{len(all_niches)}] {tag}: {len(asins)} ASIN...", end="", flush=True)

        try:
            titles = get_asin_titles(db, asins)
            asins_with_titles = sorted(titles.keys())

            if len(asins_with_titles) < 3:
                # 太少，不聚类
                no_cluster_needed.append({
                    "tag": tag, "size": len(asins_with_titles),
                    "asins": asins, "keywords": [], "reason": "too few ASINs with titles"
                })
                print(f" skip (<3 titles)")
                continue

            clusters = cluster_asins(asins_with_titles, titles)

            if len(clusters) <= 1:
                # 只有一个簇，不需要 LLM 拆分
                no_cluster_needed.append({
                    "tag": tag, "size": len(asins_with_titles),
                    "clusters": clusters,
                    "asins": asins,
                    "cluster_kws": {},
                })
                print(f" 1 cluster, skip LLM")
                continue

            # 需要调 LLM
            cluster_kws = assign_keywords(db, niche["id"], clusters, set(asins))
            cluster_data.append({
                "merged_id": niche["id"],
                "tag": tag,
                "asin_count": len(asins_with_titles),
                "clusters": clusters,
                "cluster_kws": {str(c["cluster_id"]): v for c, v in zip(clusters,
                                 [cluster_kws.get(c["cluster_id"], []) for c in clusters])},
            })
            print(f" {len(clusters)} clusters, queued for LLM")

        except Exception as e:
            print(f" error: {e}")

    print(f"\n需 LLM 命名: {len(cluster_data)} 个赛道")
    print(f"无需 LLM: {len(no_cluster_needed)} 个赛道")

    # 3. 批量并发调 LLM
    if cluster_data:
        print(f"\n--- Phase 2: LLM 命名 ({LLM_CONCURRENCY} 并发) ---")
        t0 = time.time()
        llm_results = asyncio.run(_call_llm_batch(cluster_data, None))
        elapsed = time.time() - t0
        print(f"完成: {len(llm_results)} 个赛道, {elapsed:.0f}s ({len(llm_results)/elapsed:.1f}/s)")

        # 合并 LLM 结果
        for item, llm_r in zip(cluster_data, llm_results):
            item["llm_result"] = llm_r.get("result", {})
    else:
        llm_results = []

    # 4. 写入 DB
    print(f"\n--- Phase 3: 写入 DB ---")
    total_sub = 0
    total_kw = 0
    total_asin = 0
    to_delete = 0

    def write_clusters(tag, merged_id, clusters, cluster_kws, llm_result):
        nonlocal total_sub, total_kw, total_asin, to_delete

        llm_clusters = {}
        if llm_result and "clusters" in llm_result:
            llm_clusters = {c["id"]: c for c in llm_result["clusters"]}

        for c in clusters:
            cid = c["cluster_id"]
            lc = llm_clusters.get(cid, {})

            belongs = lc.get("belongs_to_niche", True)
            if not belongs:
                to_delete += c["size"]

            sn = SubNiche(
                parent_tag=tag,
                merged_niche_id=merged_id,
                name=lc.get("name", tag),
                belongs_to_niche=belongs,
                is_coherent=lc.get("is_coherent", True),
                size=c["size"],
                keyword_count=len(cluster_kws.get(str(cid), [])),
            )
            db.add(sn)
            db.flush()

            for kw in cluster_kws.get(str(cid), []):
                db.add(SubNicheKw(sub_niche_id=sn.id, keyword=kw))
                total_kw += 1
            for a in c["asin_list"]:
                db.add(SubNicheAsin(sub_niche_id=sn.id, asin=a))
                total_asin += 1
            total_sub += 1

    for item in cluster_data:
        write_clusters(item["tag"], item["merged_id"],
                       item["clusters"], item["cluster_kws"],
                       item.get("llm_result", {}))

    for item in no_cluster_needed:
        clusters = item.get("clusters", [])
        if not clusters:
            # 没标题的，写一个默认簇
            sn = SubNiche(
                parent_tag=item["tag"],
                merged_niche_id=-1,
                name=item["tag"],
                size=item["size"],
                keyword_count=0,
            )
            db.add(sn)
            db.flush()
            for a in item.get("asins", []):
                db.add(SubNicheAsin(sub_niche_id=sn.id, asin=a))
            total_sub += 1
            total_asin += len(item.get("asins", []))
        else:
            # 单簇，直接写入
            c = clusters[0]
            sn = SubNiche(
                parent_tag=item["tag"],
                merged_niche_id=-1,
                name=item["tag"],
                size=c["size"],
                keyword_count=0,
            )
            db.add(sn)
            db.flush()
            for a in c["asin_list"]:
                db.add(SubNicheAsin(sub_niche_id=sn.id, asin=a))
            total_sub += 1
            total_asin += c["size"]

    db.commit()

    # 5. 总体统计
    # 还有 1-5 ASIN 的小 niche，直接作为子赛道
    small_rows = db.execute(text(
        "SELECT id, tag, asin_count, asins_json FROM merged_niche WHERE asin_count <= 5"
    )).fetchall()
    for mid, tag, ac, aj in small_rows:
        asins = json.loads(aj)
        sn = SubNiche(
            parent_tag=tag, merged_niche_id=mid, name=tag,
            size=len(asins), keyword_count=0,
        )
        db.add(sn)
        db.flush()
        for a in asins:
            db.add(SubNicheAsin(sub_niche_id=sn.id, asin=a))
        total_sub += 1
        total_asin += len(asins)

    db.commit()

    all_sub = db.execute(text("SELECT COUNT(*) FROM sub_niche")).scalar()
    all_sub_kw = db.execute(text("SELECT COUNT(*) FROM sub_niche_kw")).scalar()
    all_sub_asin = db.execute(text("SELECT COUNT(DISTINCT asin) FROM sub_niche_asin")).scalar()

    print(f"\n{'='*60}")
    print(f"=== FINAL RESULT ===")
    print(f"Sub-niches: {all_sub}")
    print(f"Unique ASINs: {all_sub_asin}")
    print(f"Keywords in sub-niches: {all_sub_kw}")
    print(f"Flagged to delete (belongs_to_niche=false): {to_delete} ASINs")
    print(f"\nSaved to DB: sub_niche, sub_niche_kw, sub_niche_asin")

    db.close()
    print("Done!")


if __name__ == "__main__":
    main()
