"""卖家精灵 Excel 导入 — 解析 KCR 和产品库导出，写入数据库。

列顺序固定（卖家精灵导出模板不变），优先按位置索引取值，异常时回落列名匹配。
"""
import json
from pathlib import Path

import pandas as pd

from .models import SessionLocal, SellerspriteKeyword, SellerspriteProduct


# ═══════════════════════════════════════════════════════
# KCR Excel 解析（33 列固定顺序）
# ═══════════════════════════════════════════════════════

def parse_kcr_excel(filepath: str) -> list[dict]:
    """解析卖家精灵 KCR 导出 Excel，按列位置取值。"""
    df = pd.read_excel(filepath)
    if df.empty:
        raise ValueError("KCR Excel 为空")
    if len(df.columns) < 30:
        raise ValueError(f"KCR 列数异常: {len(df.columns)}（预期 ≥30）")

    records = []
    for _, row in df.iterrows():
        try:
            keyword = _val(row, 0)
            if not keyword or keyword in ("0", "nan"):
                continue

            top3 = []
            for base in range(23, 30, 3):  # cols 23, 26, 29
                asin = _val(row, base)
                if asin:
                    top3.append({
                        "asin": asin,
                        "click_share": _num(row, base + 1),
                        "conv_share": _num(row, base + 2),
                    })

            records.append({
                "keyword": keyword,
                "keyword_cn": _val(row, 1),
                "search_volume": int(_num(row, 3)),
                "sales_volume_90d": int(_num(row, 4)),
                "purchases_90d": int(_num(row, 5)),
                "search_conversion_rate": _num(row, 6) * 100,     # 小数 → 百分比
                "click_conversion_rate": _num(row, 7) * 100,
                "cpc_high": _num(row, 8),
                "cpc_recommended": _num(row, 9),
                "cpc_low": _num(row, 10),
                "cpa_high": _num(row, 11),
                "cpa_recommended": _num(row, 12),
                "cpa_low": _num(row, 13),
                "max_price": _num(row, 14),
                "avg_price": _num(row, 15),
                "min_price": _num(row, 16),
                "acos_high": _num(row, 17),
                "acos_recommended": _num(row, 18),
                "acos_low": _num(row, 19),
                "budget": _num(row, 20),
                "click_share": _num(row, 21) * 100,                # 小数 → 百分比
                "conv_share": _num(row, 22) * 100,
                "top3_asins": json.dumps(top3, ensure_ascii=False),
                "top10_asins": _val(row, 32),
            })
        except Exception:
            continue

    return records


# ═══════════════════════════════════════════════════════
# 产品库 Excel 解析（64 列固定顺序）
# ═══════════════════════════════════════════════════════

def parse_product_excel(filepath: str) -> list[dict]:
    """解析卖家精灵产品库导出 Excel，按列位置取值（64列）。"""
    df = pd.read_excel(filepath)
    if df.empty:
        raise ValueError("产品 Excel 为空")
    if len(df.columns) < 50:
        raise ValueError(f"产品列数异常: {len(df.columns)}（预期 ≥50）")

    records = []
    for _, row in df.iterrows():
        try:
            asin = _val(row, 0)
            if not asin or not asin.startswith("B0"):
                continue

            ship_method = _val(row, 35)
            is_fba = ship_method.upper() in ("FBA", "AMZ")

            badge_parts = []
            if _val(row, 44) in ("Y", "Yes"):
                badge_parts.append("Best Seller")
            if _val(row, 45) in ("Y", "Yes"):
                badge_parts.append("Amazon's Choice")
            if _val(row, 46) in ("Y", "Yes"):
                badge_parts.append("New Release")

            records.append({
                "asin": asin,
                "sku": _val(row, 1),
                "title": _val(row, 5) or _val(row, 2),
                "brand": _val(row, 3),
                "brand_url": _val(row, 4),
                "product_url": _val(row, 6),
                "main_image": _val(row, 7),
                "parent_asin": _val(row, 8),
                "category_path": _val(row, 9),
                "main_category": _val(row, 10),
                "main_bsr": int(_num(row, 11)),
                "main_bsr_trend": _val(row, 12),
                "main_bsr_trend_detail": _val(row, 13),
                "sub_category": _val(row, 14),
                "sub_bsr": int(_num(row, 15)),
                "ratings_count": int(_num(row, 16)),
                "reviews_trend": _val(row, 17),
                "monthly_revenue": _num(row, 18),
                "monthly_sales": int(_num(row, 19)),
                "total_revenue": _num(row, 20),
                "total_sales": int(_num(row, 21)),
                "price": _num(row, 22),
                "prime_price": _num(row, 23),
                "coupon": _num(row, 24),
                "qa_count": int(_num(row, 25)),
                "reviews_count": int(_num(row, 26)),
                "seller_count": int(_num(row, 27)),
                "ratings": _num(row, 28),
                "_ratings_count2": int(_num(row, 29)),   # 与 col16 一致，取 max
                "fba_fee": _num(row, 30),
                "profit_rate": _num(row, 31),
                "profit": _num(row, 32),
                "online_date": _val(row, 33),
                "online_days": int(_num(row, 34)),
                "is_fba": is_fba,
                "ship_method": ship_method,
                "shipping_fee": _num(row, 36),
                "lqs": int(_num(row, 37)),
                "variation_count": int(_num(row, 38)),
                "buybox_seller": _val(row, 39),
                "buybox_price": _num(row, 40),
                "seller_country": _val(row, 41),
                "seller_info": _val(row, 42),
                "seller_page": _val(row, 43),
                "aplus": _val(row, 47) in ("Y", "Yes"),
                "has_video": int(_num(row, 48)) > 0,
                "badge": ", ".join(badge_parts) if badge_parts else "",
                "sp_ad": _val(row, 49) in ("Y", "Yes"),
                "brand_ad": _val(row, 50) in ("Y", "Yes"),
                "brand_promotion": _val(row, 51) in ("Y", "Yes"),
                "deal_7day": _val(row, 52) in ("Y", "Yes"),
                "ac_keywords": _val(row, 53),
                "weight": _num(row, 54),
                "weight_conv": _num(row, 55),
                "size": _val(row, 56),
                "size_conv": _val(row, 57),
                "package_weight": _num(row, 58),
                "package_weight_conv": _num(row, 59),
                "package_size": _val(row, 60),
                "package_size_conv": _val(row, 61),
                "package_size_field": _val(row, 62),
                "tags": _val(row, 63),
            })
        except Exception:
            continue

    return records


# ═══════════════════════════════════════════════════════
# 入库
# ═══════════════════════════════════════════════════════

def import_kcr_to_db(records: list[dict], domain: str, batch_id: str,
                     expiry_days: int = 30) -> int:
    """批量写入 KCR 数据到 sellersprite_keyword 表。返回导入数。

    已存在且未过期的关键词跳过，避免重复 Playwright 查询。
    expiry_days: 数据有效期(天)，超过则视为过期重新查询更新。
    """
    from datetime import datetime, timezone, timedelta
    db = SessionLocal()
    try:
        count = 0
        skipped = 0
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days) if expiry_days > 0 else None

        for r in records:
            try:
                existing = db.query(SellerspriteKeyword).filter(
                    SellerspriteKeyword.keyword == r["keyword"],
                    SellerspriteKeyword.domain == domain,
                ).first()
                if existing and existing.queried_at:
                    if cutoff and existing.queried_at > cutoff:
                        skipped += 1
                        continue
                    # 过期或首次 → 删旧插新
                    if existing:
                        db.delete(existing)
                        db.flush()

                db.add(SellerspriteKeyword(
                    keyword=r["keyword"],
                    keyword_cn=r.get("keyword_cn", ""),
                    domain=domain,
                    batch_id=batch_id,
                    search_volume=r.get("search_volume", 0),
                    sales_volume_90d=r.get("sales_volume_90d", 0),
                    purchases_90d=r.get("purchases_90d", 0),
                    search_conversion_rate=r.get("search_conversion_rate", 0),
                    click_conversion_rate=r.get("click_conversion_rate", 0),
                    cpc_recommended=r.get("cpc_recommended", 0),
                    cpc_high=r.get("cpc_high", 0),
                    cpc_low=r.get("cpc_low", 0),
                    cpa_recommended=r.get("cpa_recommended", 0),
                    cpa_high=r.get("cpa_high", 0),
                    cpa_low=r.get("cpa_low", 0),
                    avg_price=r.get("avg_price", 0),
                    min_price=r.get("min_price", 0),
                    max_price=r.get("max_price", 0),
                    acos_recommended=r.get("acos_recommended", 0),
                    acos_high=r.get("acos_high", 0),
                    acos_low=r.get("acos_low", 0),
                    budget=r.get("budget", 0),
                    click_share=r.get("click_share", 0),
                    conv_share=r.get("conv_share", 0),
                    top3_asins=r.get("top3_asins", "[]"),
                    top10_asins=r.get("top10_asins", ""),
                    raw_response=json.dumps(r, ensure_ascii=False),
                ))
                count += 1
            except Exception:
                continue

        db.commit()
        return count
    finally:
        db.close()


def import_product_to_db(records: list[dict], domain: str, batch_id: str,
                         expiry_days: int = 30) -> int:
    """批量写入产品数据到 sellersprite_product 表。返回导入数。

    已存在且未过期的 ASIN 跳过，避免重复 Playwright 查询。
    expiry_days: 数据有效期(天)，超过则视为过期重新查询更新。
    """
    from datetime import datetime, timezone, timedelta
    db = SessionLocal()
    try:
        count = 0
        skipped = 0
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days) if expiry_days > 0 else None

        for r in records:
            try:
                existing = db.query(SellerspriteProduct).filter(
                    SellerspriteProduct.asin == r["asin"],
                    SellerspriteProduct.domain == domain,
                ).first()
                if existing and existing.queried_at:
                    if cutoff and existing.queried_at > cutoff:
                        skipped += 1
                        continue
                    if existing:
                        db.delete(existing)
                        db.flush()

                db.add(SellerspriteProduct(
                    asin=r["asin"],
                    domain=domain,
                    batch_id=batch_id,
                    sku=r.get("sku", ""),
                    title=r.get("title", ""),
                    brand=r.get("brand", ""),
                    brand_url=r.get("brand_url", ""),
                    product_url=r.get("product_url", ""),
                    main_image=r.get("main_image", ""),
                    parent_asin=r.get("parent_asin", ""),
                    category_path=r.get("category_path", ""),
                    main_category=r.get("main_category", ""),
                    main_bsr=r.get("main_bsr", 0),
                    main_bsr_trend=r.get("main_bsr_trend", ""),
                    main_bsr_trend_detail=r.get("main_bsr_trend_detail", ""),
                    sub_category=r.get("sub_category", ""),
                    sub_bsr=r.get("sub_bsr", 0),
                    price=r.get("price", 0),
                    prime_price=r.get("prime_price", 0),
                    coupon=r.get("coupon", 0),
                    fba_fee=r.get("fba_fee", 0),
                    shipping_fee=r.get("shipping_fee", 0),
                    profit_rate=r.get("profit_rate", 0),
                    profit=r.get("profit", 0),
                    gross_margin=r.get("profit_rate", 0),
                    monthly_sales=r.get("monthly_sales", 0),
                    monthly_revenue=r.get("monthly_revenue", 0),
                    total_sales=r.get("total_sales", 0),
                    total_revenue=r.get("total_revenue", 0),
                    ratings=r.get("ratings", 0),
                    ratings_count=max(r.get("ratings_count", 0), r.get("_ratings_count2", 0)),
                    reviews_count=r.get("reviews_count", 0),
                    reviews_trend=r.get("reviews_trend", ""),
                    qa_count=r.get("qa_count", 0),
                    online_date=r.get("online_date", ""),
                    online_days=r.get("online_days", 0),
                    is_fba=r.get("is_fba", False),
                    ship_method=r.get("ship_method", ""),
                    seller_count=r.get("seller_count", 0),
                    buybox_seller=r.get("buybox_seller", ""),
                    buybox_price=r.get("buybox_price", 0),
                    seller_country=r.get("seller_country", ""),
                    seller_info=r.get("seller_info", ""),
                    seller_page=r.get("seller_page", ""),
                    lqs=r.get("lqs", 0),
                    variation_count=r.get("variation_count", 0),
                    aplus=r.get("aplus", False),
                    has_video=r.get("has_video", False),
                    badge=r.get("badge", ""),
                    sp_ad=r.get("sp_ad", False),
                    brand_ad=r.get("brand_ad", False),
                    brand_promotion=r.get("brand_promotion", False),
                    deal_7day=r.get("deal_7day", False),
                    weight=r.get("weight", 0),
                    weight_conv=r.get("weight_conv", 0),
                    size=r.get("size", ""),
                    size_conv=r.get("size_conv", ""),
                    package_weight=r.get("package_weight", 0),
                    package_weight_conv=r.get("package_weight_conv", 0),
                    package_size=r.get("package_size", ""),
                    package_size_conv=r.get("package_size_conv", ""),
                    package_size_field=r.get("package_size_field", ""),
                    tags=r.get("tags", ""),
                    ac_keywords=r.get("ac_keywords", ""),
                    raw_response=json.dumps(r, ensure_ascii=False),
                ))
                count += 1
            except Exception:
                continue

        db.commit()
        return count
    finally:
        db.close()


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════

def _val(row, col_idx: int) -> str:
    """按列索引取值，返回字符串。"""
    if col_idx >= len(row.index):
        return ""
    v = row.iloc[col_idx]
    if pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s in ("0", "nan", "None") else s


def _num(row, col_idx: int) -> float:
    """按列索引取值，返回 float。"""
    if col_idx >= len(row.index):
        return 0.0
    v = row.iloc[col_idx]
    if pd.isna(v):
        return 0.0
    try:
        s = str(v).replace("%", "").replace(",", "").replace("CDN$", "").replace("US$", "")
        # 去掉常见单位后缀
        for unit in [" kg", " g", " oz", " lb", " cm", " mm", " m", " in", " inch"]:
            if s.endswith(unit):
                s = s[:-len(unit)]
                break
        return float(s)
    except (ValueError, TypeError):
        return 0.0
