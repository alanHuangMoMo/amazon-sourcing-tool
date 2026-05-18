"""
Step 2-5: ASIN 嵌入聚类 → 关键词分配 → LLM 命名修正

流程:
  1. 从 merged_niche 取一个赛道
  2. 获取 ASIN 标题（优先 sellersprite_product，fallback aba_report）
  3. sentence-transformers 嵌入 → HDBSCAN 聚类
  4. 关键词按规则分配到各簇（中性词广播）
  5. 输出给 LLM 命名 + 纠错

用法:
  python cluster_niche.py                          # 处理所有 ≥16 ASIN 的合并 niche
  python cluster_niche.py --tag "血压计"            # 只处理单个赛道
  python cluster_niche.py --tag "血压计" --dry-run  # 只跑聚类，不调 LLM
"""
import argparse
import asyncio
import aiohttp
import json
import sys
import time
import re
from collections import defaultdict, Counter
from pathlib import Path

import numpy as np
from sklearn.preprocessing import normalize

sys.path.insert(0, str(Path(__file__).parent))
from app.models import init_db, SessionLocal
from sqlalchemy import text

API_KEY = "sk-1fce6b2a9f7844d1938aa3ed512dbcde"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# === 聚类参数 ===
MIN_CLUSTER_SIZE = 3       # 最少 ASIN 数形成一个子赛道
MIN_SAMPLES = 2            # HDBSCAN 核心点参数
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# === LLM Prompt ===
CLUSTER_PROMPT = """你是亚马逊产品分类专家。我通过算法将一个赛道的产品聚类成了若干子赛道，请你：
1. 给每个簇起一个 2-6 字的简短中文名
2. ★以ASIN标题为主、关键词为辅★ 判断每个簇是否真正属于这个赛道。如果一个簇的所有ASIN产品类型完全不同（比如赛道是血压计但簇里全是高压清洗机），标 belongs_to_niche: false
3. 检查每个簇里是否有不合理的 ASIN（标出异常 ASIN 列表）
4. 判断是否有应该合并的簇（两个簇产品本质相同只是叫法不同）

输出 JSON，不要其他文字：
{
  "clusters": [
    {
      "id": 0,
      "name": "簇的中文名",
      "belongs_to_niche": true,
      "is_coherent": true,
      "anomaly_asins": [],
      "merge_with": null,
      "notes": ""
    }
  ]
}"""


def get_asin_titles(db, asins: list[str]) -> dict[str, str]:
    """优先 sellersprite_product，缺的从 aba_report 补。"""
    titles = {}

    # 先从产品库取
    placeholders = ",".join(f":a{i}" for i in range(len(asins)))
    params = {f"a{i}": a for i, a in enumerate(asins)}
    try:
        rows = db.execute(text(
            f"SELECT asin, title FROM sellersprite_product WHERE domain='CA' AND asin IN ({placeholders})"
        ), params).fetchall()
        for asin, title in rows:
            if title and title.strip():
                titles[asin] = title.strip()
    except Exception:
        pass

    # 缺失的从 ABA 补（查 asin_1/2/3 三个位置）
    missing = [a for a in asins if a not in titles]
    if missing:
        for a in missing:
            row = None
            for col in ("asin_1", "asin_2", "asin_3"):
                row = db.execute(text(
                    f"SELECT {col}_title FROM aba_report WHERE domain='CA' AND {col}=:a AND {col}_title IS NOT NULL LIMIT 1"
                ), {"a": a}).fetchone()
                if row and row[0]:
                    break
            if row and row[0]:
                titles[a] = row[0].strip()
    return titles


def embed_titles(titles: list[str]) -> np.ndarray:
    """句子嵌入（本地模型）。优先从缓存加载，不联网。"""
    import os
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    embeddings = model.encode(titles, show_progress_bar=False, batch_size=32)
    # L2 归一化，cosine 距离 = 1 - dot
    return normalize(embeddings, norm="l2")


def cluster_asins(asins: list[str], titles: dict[str, str]) -> list[dict]:
    """HDBSCAN 聚类。返回簇列表。"""
    import hdbscan

    ordered = sorted(asins)
    title_list = [titles.get(a, a) for a in ordered]

    print(f"  嵌入 {len(title_list)} 个标题...")
    embs = embed_titles(title_list)

    # 先试 HDBSCAN，如果所有点都是噪声（-1），降参数再试
    for min_cluster in [MIN_CLUSTER_SIZE, 2]:
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster,
            min_samples=min(min_cluster, len(ordered) - 1),
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(embs)

        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = sum(1 for l in labels if l == -1)

        if n_clusters >= 2:
            break
        print(f"  min_cluster={min_cluster} → 仅 {n_clusters} 个簇 (噪声={n_noise})，重试...")

    # 噪声点分配到最近簇
    if -1 in labels:
        n_noise = sum(1 for l in labels if l == -1)
        print(f"  噪声点: {n_noise}/{len(ordered)}，分配到最近簇...")
        if n_clusters > 0:
            noise_indices = [i for i, l in enumerate(labels) if l == -1]
            cluster_centroids = {}
            for cid in set(labels) - {-1}:
                cluster_centroids[cid] = embs[labels == cid].mean(axis=0)

            for ni in noise_indices:
                best_cid = min(cluster_centroids,
                               key=lambda c: np.linalg.norm(embs[ni] - cluster_centroids[c]))
                labels[ni] = best_cid

    # 按簇组织
    clusters = defaultdict(list)
    for i, (asin, label) in enumerate(zip(ordered, labels)):
        clusters[int(label)].append({
            "asin": asin,
            "title": title_list[i],
        })

    # 按 ASIN 数降序
    result = []
    for cid in sorted(clusters, key=lambda c: -len(clusters[c])):
        result.append({
            "cluster_id": cid,
            "size": len(clusters[cid]),
            "asins": clusters[cid],
            "asin_list": [a["asin"] for a in clusters[cid]],
        })

    print(f"  → {len(result)} 个簇: {[(r['cluster_id'], r['size']) for r in result]}")
    return result


def assign_keywords(db, merged_niche_id: int, clusters: list[dict],
                    all_asins: set[str]) -> dict[int, list[str]]:
    """将关键词分配到各簇（中性词广播）。"""
    # 加载该合并 niche 的所有关键词
    rows = db.execute(text(
        "SELECT keyword FROM merged_niche_kw WHERE merged_niche_id=:mid"
    ), {"mid": merged_niche_id}).fetchall()
    all_kws = [r[0] for r in rows]

    # 构建 ASIN → 簇映射
    asin_to_cluster = {}
    for c in clusters:
        for a in c["asins"]:
            asin_to_cluster[a["asin"]] = c["cluster_id"]

    # 加载 ABA 数据：关键词 → ASIN 映射
    kw_placeholders = ",".join(f":k{i}" for i in range(len(all_kws)))
    kw_params = {f"k{i}": k for i, k in enumerate(all_kws)}
    aba_rows = db.execute(text(f"""
        SELECT keyword, asin_1, asin_2, asin_3
        FROM aba_report WHERE domain='CA' AND keyword IN ({kw_placeholders})
    """), kw_params).fetchall()

    kw_to_asins = defaultdict(set)
    for kw, a1, a2, a3 in aba_rows:
        for a in (a1, a2, a3):
            if a and a.strip():
                kw_to_asins[kw].add(a.strip())

    # 中性词识别：2字以下或纯产品名，不含功能/属性/人群词
    def is_neutral(kw: str) -> bool:
        kw_lower = kw.lower()
        feature_words = [
            "compression", "hinged", "sleeve", "strap", "wrap", "gel", "silicone",
            "digital", "automatic", "manual", "rechargeable", "wireless", "bluetooth",
            "adjustable", "orthopedic", "extra", "large", "small", "pack", "count",
            "women", "men", "kids", "baby", "adult", "senior",
            "for", "with", "and", "the",
        ]
        # 长词有功能描述 → 非中性
        if len(kw_lower.split()) >= 4:
            return False
        for fw in feature_words:
            if fw in kw_lower:
                return False
        return True

    # 分配
    cluster_kws: dict[int, set[str]] = defaultdict(set)
    neutral_kws = set()

    for kw in all_kws:
        linked_asins = kw_to_asins.get(kw, set())
        # 哪些簇包含这个关键词的关联 ASIN
        linked_clusters = set()
        for a in linked_asins:
            if a in asin_to_cluster:
                linked_clusters.add(asin_to_cluster[a])

        if linked_clusters:
            for cid in linked_clusters:
                cluster_kws[cid].add(kw)
        else:
            # 无关联 ASIN 信息 → 中性词
            neutral_kws.add(kw)

    # 中性词广播到所有簇
    neutral = {kw for kw in all_kws if is_neutral(kw)} | neutral_kws
    for kw in neutral:
        for c in clusters:
            cluster_kws[c["cluster_id"]].add(kw)

    # 转 list
    result = {}
    for c in clusters:
        result[c["cluster_id"]] = sorted(cluster_kws[c["cluster_id"]])
    return result


def call_llm(clusters: list[dict], cluster_kws: dict[int, list[str]]) -> dict:
    """调 DeepSeek 给簇命名 + 纠错。"""
    # 每个簇取 Top 10 ASIN 标题 + Top 20 关键词作为样本
    samples = []
    for c in clusters:
        titles = [a["title"][:120] for a in c["asins"][:10]]
        kws = cluster_kws.get(c["cluster_id"], [])[:20]
        samples.append({
            "id": c["cluster_id"],
            "size": c["size"],
            "sample_titles": titles,
            "sample_keywords": kws,
        })

    prompt = json.dumps({"clusters": samples}, ensure_ascii=False, indent=2)
    user_msg = f"请分析以下聚类结果:\n{prompt}"

    async def _call():
        async with aiohttp.ClientSession() as session:
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
            async with session.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                data = await resp.json()
                return json.loads(data["choices"][0]["message"]["content"])

    return asyncio.run(_call())


def run_niche(db, tag: str, dry_run: bool = False) -> dict:
    """对单个合并赛道跑完整聚类+命名流程。"""
    # 取合并 niche 数据
    row = db.execute(text(
        "SELECT id, tag, keyword_count, asin_count, asins_json FROM merged_niche WHERE tag=:tag"
    ), {"tag": tag}).fetchone()
    if not row:
        print(f"赛道 '{tag}' 不存在")
        return {}

    mid, tag, kw_cnt, asin_cnt, asins_json = row
    all_asins = json.loads(asins_json)
    print(f"\n{'='*60}")
    print(f"赛道: {tag} | {kw_cnt} kw | {asin_cnt} ASIN")
    print(f"{'='*60}")

    # 1) 获取 ASIN 标题
    titles = get_asin_titles(db, all_asins)
    print(f"获取标题: {len(titles)}/{len(all_asins)}")

    asins_with_titles = sorted(titles.keys())

    # 2) 聚类
    clusters = cluster_asins(asins_with_titles, titles)

    # 3) 关键词分配
    cluster_kws = assign_keywords(db, mid, clusters, set(all_asins))

    # 4) 展示结果
    print(f"\n=== 聚类结果 ===")
    for c in clusters:
        cid = c["cluster_id"]
        print(f"\n--- 簇 {cid} ({c['size']} ASINs, {len(cluster_kws.get(cid, []))} kws) ---")
        print("  Top ASINs:")
        for a in c["asins"][:5]:
            print(f"    {a['asin']}: {a['title'][:100]}")
        print(f"  Keywords:")
        kws = cluster_kws.get(cid, [])
        print(f"    {', '.join(kws[:15])}{'...' if len(kws) > 15 else ''}")

    # 5) LLM 命名
    if not dry_run:
        print(f"\n=== LLM 命名修正 ===")
        llm_result = call_llm(clusters, cluster_kws)
        print(json.dumps(llm_result, ensure_ascii=False, indent=2))
    else:
        llm_result = {}

    return {
        "tag": tag,
        "asin_count": len(asins_with_titles),
        "clusters": [
            {
                "cluster_id": c["cluster_id"],
                "size": c["size"],
                "asins": c["asin_list"],
                "keywords": cluster_kws.get(c["cluster_id"], []),
            }
            for c in clusters
        ],
        "llm_result": llm_result,
    }


def main():
    parser = argparse.ArgumentParser(description="Niche 聚类拆分")
    parser.add_argument("--tag", default="", help="指定赛道标签（默认: 所有 ≥16 ASIN 的赛道）")
    parser.add_argument("--dry-run", action="store_true", help="只聚类不调 LLM")
    parser.add_argument("--min-asin", type=int, default=16, help="最少 ASIN 数才处理")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    if args.tag:
        result = run_niche(db, args.tag, dry_run=args.dry_run)
        out_path = f"data/cluster_{args.tag}.json"
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved: {out_path}")
    else:
        # 批量处理所有大 niche
        rows = db.execute(text(
            "SELECT tag, asin_count FROM merged_niche WHERE asin_count >= :min_asn ORDER BY asin_count DESC"
        ), {"min_asn": args.min_asin}).fetchall()

        print(f"共 {len(rows)} 个赛道 (≥{args.min_asn} ASIN) 待处理")
        all_results = []

        for tag, asin_cnt in rows:
            try:
                result = run_niche(db, tag, dry_run=args.dry_run)
                if result:
                    all_results.append(result)
            except Exception as e:
                print(f"  {tag} 失败: {e}")
                continue

        out_path = "data/cluster_all_niches.json"
        Path(out_path).write_text(
            json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{'='*60}")
        print(f"完成: {len(all_results)}/{len(rows)} 个赛道，Saved: {out_path}")

    db.close()


if __name__ == "__main__":
    main()
