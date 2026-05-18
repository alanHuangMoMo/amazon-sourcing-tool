"""Niche 竞争分析 — 以共享关键词聚类 → 双路相似品发现 → 关键词首页撞库。

流程：
  1. 从候选 ASIN 按共享关键词分组形成 niche
  2. 每个 niche 的 ASIN 做 ProductSearch + 图搜，扩展 niche ASIN 列表
  3. 对 niche 关键词做 KeywordProductRanking，取首页 ASIN
  4. 撞库：niche ASINs ∩ 关键词首页 ASINs → 竞争密度评分
"""

import json
import time
from collections import defaultdict

import requests

API_BASE = "https://cli.sorftime.com/api/"
DEFAULT_SK = "uhlmn2fpmvzxq05cyxnicty3ystxqt09"


def _call(endpoint: str, params: dict, domain: str = "1", account_sk: str = None) -> dict:
    """调用 Sorftime API。"""
    sk = account_sk or DEFAULT_SK
    r = requests.post(
        f"{API_BASE}{endpoint}?domain={domain}",
        json=params,
        headers={"Authorization": f"BasicAuth {sk}", "Browser": "Cli"},
        timeout=60,
    )
    return r.json()


def form_niches(candidates: list[dict]) -> dict[str, dict]:
    """从候选列表按共享关键词聚类为 niche。

    输入: [{"asin": "B0xxx", "keywords": ["kw1","kw2"], ...}, ...]
    返回: {niche_name: {"asins": set, "keywords": set, "primary_kw": str}}
    """
    if not candidates:
        return {}
    # 确保是 dict 列表
    if not isinstance(candidates[0], dict):
        raise TypeError(f"candidates 需要 dict 列表，收到 {type(candidates[0])}")

    # keyword → ASINs
    kw_to_asins = defaultdict(set)
    for c in candidates:
        for kw in c.get("keywords", []):
            kw_to_asins[kw].add(c["asin"])

    # 共享关键词（≥2个 ASIN）
    shared = {kw: asins for kw, asins in kw_to_asins.items() if len(asins) >= 2}

    # 连通分量聚类
    niche_id = {}
    niches = {}
    for kw, asins in sorted(shared.items(), key=lambda x: -len(x[1])):
        existing_ids = {niche_id[a] for a in asins if a in niche_id}
        if existing_ids:
            nid = min(existing_ids)
            niches[nid]["asins"] |= asins
            niches[nid]["keywords"].add(kw)
            for a in asins:
                niche_id[a] = nid
        else:
            niches[kw] = {"asins": set(asins), "keywords": {kw}, "primary_kw": kw}
            for a in asins:
                niche_id[a] = kw

    # 未分组的单独成 niche
    all_asins = {c["asin"] for c in candidates}
    for asin in all_asins - set(niche_id):
        c = next(c for c in candidates if c["asin"] == asin)
        kws = c.get("keywords", [])
        pk = kws[0] if kws else asin
        niches[asin] = {"asins": {asin}, "keywords": set(kws), "primary_kw": pk}

    # 回填：每个 niche 内所有 ASIN 的全部关键词都汇入 niche 关键词池
    asin_to_kws = {c["asin"]: c.get("keywords", []) for c in candidates}
    for name, niche in niches.items():
        for asin in niche["asins"]:
            if asin in asin_to_kws:
                niche["keywords"].update(asin_to_kws[asin])

    return niches


def expand_niche_asins(
    niche_asins: set[str],
    domain: str = "1",
    account_sk: str = None,
    include_image_search: bool = False,
    image_urls: dict[str, str] = None,
) -> set[str]:
    """对 niche 内 ASIN 做 ProductSearch，扩展同类 ASIN。

    返回: 扩展后的 ASIN 集合（含原始 ASIN）
    """
    all_asins = set(niche_asins)

    for asin in niche_asins:
        r = _call("ProductSearch", {"asin": asin, "page": 1}, domain, account_sk)
        products = r.get("Data", {}).get("Products", [])
        for p in products:
            if p.get("Asin"):
                all_asins.add(p["Asin"])
        time.sleep(0.3)  # 限流

    # 图搜（异步提交，结果稍后取）
    if include_image_search and image_urls:
        for asin in niche_asins:
            url = image_urls.get(asin)
            if not url:
                continue
            try:
                import base64
                resp = requests.get(url, timeout=15)
                b64 = base64.b64encode(resp.content).decode()
                r = _call("SimilarProductRealtimeRequest", {"image": b64}, domain, account_sk)
                task_id = r.get("Data")
                if task_id:
                    # 简单等 6 分钟
                    for _ in range(14):
                        time.sleep(30)
                        s = _call("SimilarProductRealtimeRequestStatusQuery", {"Update": 1}, domain, account_sk)
                        tasks = s.get("Data", [])
                        status = next((t.get("Status") for t in tasks if str(t.get("TaskId")) == str(task_id)), 0)
                        if status in (3, 4):
                            break
                    c = _call("SimilarProductRealtimeRequestCollection", {"taskId": str(task_id)}, domain, account_sk)
                    for p in (c.get("Data") or []):
                        if p.get("Asin"):
                            all_asins.add(p["Asin"])
            except Exception:
                continue

    return all_asins


def analyze_keyword_competition(
    keywords: list[str],
    niche_asins: set[str],
    domain: str = "1",
    account_sk: str = None,
) -> dict:
    """对 niche 关键词查首页 ASIN，撞库计算竞争密度。

    返回: {keyword: {"page1_count": int, "niche_hits": int, "hit_asins": list, "density": float}}
    """
    results = {}
    for kw in keywords[:10]:  # 限制 10 个关键词
        r = _call("KeywordProductRanking", {"keyword": kw, "month": "2026-04"}, domain, account_sk)
        pages = r.get("Data", [])
        all_records = []
        for page_obj in pages:
            all_records.extend(page_obj.get("records", []))
        page1 = {rec["asin"] for rec in all_records if rec.get("page") == 1}
        hits = page1 & niche_asins
        density = len(hits) / len(niche_asins) * 100 if niche_asins else 0
        results[kw] = {
            "page1_count": len(page1),
            "niche_hits": len(hits),
            "density": round(density, 1),
            "hit_asins": sorted(hits),
        }
        time.sleep(0.5)

    return results


def _fetch_products_batch(
    asins: list[str],
    core_asins: set[str],
    domain: str = "1",
    account_sk: str = None,
    keyword_hits: dict[str, set[str]] = None,
) -> list[dict]:
    """批量获取 ASIN 产品详情（优先 DB，缺失的调 Sorftime API）。

    keyword_hits: {keyword: set of ASINs that rank on page 1 for this keyword}
    """
    from .models import SessionLocal, SellerspriteProduct

    products = []
    db = SessionLocal()
    try:
        # 先从 DB 查
        db_asins = set()
        rows = db.query(SellerspriteProduct).filter(
            SellerspriteProduct.asin.in_(asins), SellerspriteProduct.domain == "CA"
        ).all()
        row_map = {r.asin: r for r in rows}

        for asin in asins:
            is_core = asin in core_asins
            r = row_map.get(asin)

            if r and r.price and r.price > 0:
                db_asins.add(asin)
                hit_kws = [kw for kw, hit_set in (keyword_hits or {}).items() if asin in hit_set]
                products.append({
                    "asin": asin,
                    "brand": r.brand or "",
                    "price": r.price,
                    "image": (r.main_image or ""),
                    "ratings": r.ratings or 0,
                    "ratings_count": r.ratings_count or 0,
                    "monthly_sales": r.monthly_sales or 0,
                    "online_date": r.online_date or "",
                    "is_core": is_core,
                    "source": "core" if is_core else "similar",
                    "hit_keywords": hit_kws,
                })

        # 缺失的调 Sorftime API（限 10 个避免太慢）
        missing = [a for a in asins if a not in db_asins][:10]
        for asin in missing:
            try:
                r = _call("ProductRequest", {"asin": asin}, domain, account_sk)
                p = r.get("Data", {})
                if p and p.get("Price"):
                    hit_kws = [kw for kw, hit_set in (keyword_hits or {}).items() if asin in hit_set]
                    photos = p.get("Photo", [])
                    img = photos[0] if isinstance(photos, list) and photos else (p.get("Photo") if isinstance(p.get("Photo"), str) else "")
                    products.append({
                        "asin": asin,
                        "brand": p.get("Brand", ""),
                        "price": p.get("Price", 0) / 100,
                        "image": img or "",
                        "ratings": p.get("Ratings", 0),
                        "ratings_count": p.get("RatingsCount", 0),
                        "monthly_sales": p.get("ListingSalesVolumeOfMonth", 0),
                        "online_date": p.get("OnlineDate", ""),
                        "is_core": asin in core_asins,
                        "source": "core" if asin in core_asins else "similar",
                        "hit_keywords": hit_kws,
                    })
                time.sleep(0.2)
            except Exception:
                continue
    finally:
        db.close()

    return products


def run_niche_analysis(
    batch_id: str,
    domain: str = "1",
    account_sk: str = None,
    include_image_search: bool = False,
) -> dict:
    """对指定批次的候选 ASIN 执行完整 niche 分析。

    返回: {
        "niche_count": int,
        "niches": [{name, summary, keywords, products, competition_matrix}]
    }
    """
    from .models import SessionLocal, Candidate

    db = SessionLocal()
    try:
        candidates_rows = db.query(Candidate).filter(
            Candidate.batch_id == batch_id, Candidate.price > 0
        ).all()

        candidates = []
        rejected = []
        for c in candidates_rows:
            net_rate = c.net_repayment / c.price if c.price > 0 else 0
            item = {
                "asin": c.asin,
                "keywords": json.loads(c.keywords) if c.keywords else [],
                "price": c.price,
                "brand": c.brand,
                "net_repayment": c.net_repayment,
                "net_repayment_rate": round(net_rate, 4),
            }
            # 净回款率 < 50% → 淘汰（DB保留，不参与 niche 分析）
            if net_rate < 0.5:
                rejected.append(item)
            else:
                candidates.append(item)

        if not candidates:
            return {"error": "无候选数据", "niche_count": 0, "niches": []}

        niches = form_niches(candidates)

        # 只分析 ASIN ≥ 2 的 niche
        shared_niches = {
            k: v for k, v in niches.items()
            if len(v["asins"]) >= 2
        }

        results = []
        for name, niche in sorted(shared_niches.items(), key=lambda x: -len(x[1]["asins"])):
            asins = niche["asins"]
            keywords = sorted(niche["keywords"])
            core_asins = asins  # 原始 ASIN = 核心

            # 扩展
            expanded_asins = expand_niche_asins(
                asins, domain, account_sk, include_image_search
            )

            # 关键词首页撞库
            competition = analyze_keyword_competition(
                keywords, expanded_asins, domain, account_sk
            )

            # 构建 keyword_hits 映射
            keyword_hits = {
                kw: set(comp["hit_asins"])
                for kw, comp in competition.items()
            }

            # 获取产品数据（核心 ASIN + 排首页的扩展 ASIN）
            prioritized = set()
            for hit_set in keyword_hits.values():
                prioritized.update(hit_set)
            prioritized.update(core_asins)
            product_list = sorted(prioritized)

            products = _fetch_products_batch(
                product_list, core_asins, domain, account_sk, keyword_hits
            )

            # 竞争矩阵（关键词 × 首页命中 ASIN）
            matrix = []
            for kw in keywords:
                hits = competition.get(kw, {}).get("hit_asins", [])
                for asin in hits:
                    p = next((p for p in products if p["asin"] == asin), None)
                    matrix.append({
                        "keyword": kw,
                        "asin": asin,
                        "brand": p["brand"] if p else "",
                        "price": p["price"] if p else 0,
                        "ratings": p["ratings"] if p else 0,
                        "ratings_count": p["ratings_count"] if p else 0,
                        "is_core": asin in core_asins,
                    })

            # Summary
            prices = [p["price"] for p in products if p["price"] > 0]
            ratings = [p["ratings"] for p in products if p.get("ratings", 0) > 0]
            avg_density = sum(c["density"] for c in competition.values()) / len(competition) if competition else 0

            results.append({
                "name": name,
                "summary": {
                    "core_count": len(asins),
                    "keyword_count": len(keywords),
                    "expanded_count": len(expanded_asins),
                    "density": round(avg_density, 1),
                    "price_min": min(prices) if prices else 0,
                    "price_max": max(prices) if prices else 0,
                    "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
                },
                "keywords": [
                    {
                        "keyword": kw,
                        "page1_count": competition[kw]["page1_count"],
                        "niche_hits": competition[kw]["niche_hits"],
                        "density": competition[kw]["density"],
                    }
                    for kw in keywords if kw in competition
                ],
                "products": products,
                "matrix": matrix,
            })

        return {
            "niche_count": len(results),
            "niches": results,
        }

    finally:
        db.close()
