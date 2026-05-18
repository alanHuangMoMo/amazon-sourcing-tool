"""SOP 全流程编排 — 串联 ABA清洗 → 关键词补数据 → 产品补数据 → 候选清单。"""
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .models import (
    SessionLocal, AbaRaw, AsinKeyword, DeduplicatedKeyword,
    KeywordCache, AsinCache, Candidate, Batch,
    SellerspriteKeyword, SellerspriteProduct,
)

DOMAIN_INT_TO_STR = {1: "US", 2: "UK", 3: "DE", 6: "JP", 7: "CA"}

# OpenCLI batch size (sellersprite API pageSize limits)
KCR_BATCH_MAX = 200      # keywords per opencli call
ASIN_BATCH_MAX = 2000    # ASINs per product-store batch

_OPENCLI_EXE = shutil.which("opencli") or shutil.which("opencli.cmd") or "opencli"


def _opencli_kcr_export(keywords: list[str], market: str, period: str,
                        output_path: Path, timeout: int = 300) -> Path | None:
    """Run opencli sellersprite keyword-conversion-rate with export."""
    kw_str = ",".join(keywords)
    result = subprocess.run([
        _OPENCLI_EXE, "sellersprite", "keyword-conversion-rate",
        "--keywords", kw_str,
        "--market", market,
        "--time-type", period,
        "--export",
        "--output", str(output_path),
        "-f", "json",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

    if result.returncode != 0:
        stderr = result.stderr[-300:] if result.stderr else "unknown error"
        raise RuntimeError(f"opencli KCR export failed: {stderr}")

    if output_path.exists():
        return output_path
    return None


def _opencli_product_batch(asins: list[str], market: str, batch_label: str,
                           output_path: Path) -> Path | None:
    """Run opencli sellersprite product-store: create → add → export → delete."""
    asin_str = ",".join(asins)

    # 1) Create store
    result = subprocess.run([
        _OPENCLI_EXE, "sellersprite", "product-store", "create",
        "--name", batch_label,
        "--market", market,
        "-f", "json",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"产品库创建失败: {result.stderr[-200:]}")
    store_id = json.loads(result.stdout)[0].get("storeId", 0)
    if not store_id:
        raise RuntimeError(f"产品库创建失败: storeId=0, output={result.stdout[:200]}")

    # 2) Add ASINs
    result = subprocess.run([
        _OPENCLI_EXE, "sellersprite", "product-store", "add",
        "--store-id", str(store_id),
        "--asins", asin_str,
        "--market", market,
        "-f", "json",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"产品库添加失败: {result.stderr[-200:]}")

    # 3) Export
    result = subprocess.run([
        _OPENCLI_EXE, "sellersprite", "product-store", "export",
        "--store-id", str(store_id),
        "--market", market,
        "--output", str(output_path),
        "-f", "json",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"产品库导出失败: {result.stderr[-200:]}")

    # 4) Delete
    subprocess.run([
        _OPENCLI_EXE, "sellersprite", "product-store", "delete",
        "--store-id", str(store_id),
        "--market", market,
        "-f", "json",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

    if output_path.exists():
        return output_path
    return None


def _cleanup_old_batches(keep: int = 3):
    """清理管线中间表，只保留最近 N 个批次。节约存储、加速查询。

    表: aba_raw, asin_keyword, deduplicated_keyword, candidate
    不删: aba_report, sellersprite_*, batch
    """
    from sqlalchemy import text as sa_text
    db = SessionLocal()
    try:
        for table in ["aba_raw", "asin_keyword", "deduplicated_keyword", "candidate"]:
            db.execute(sa_text(f"""
                DELETE FROM {table} WHERE batch_id NOT IN (
                    SELECT batch_id FROM batch ORDER BY created_at DESC LIMIT {keep}
                )
            """))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _run_aba_cleaning(filepath: str, config: dict) -> tuple:
    """读取并清洗 ABA 报表，返回展开/倒置/去重结果。

    返回: (df_flat, df_inverted, keywords_dedup, asins_dedup, stats)
    """
    df_raw = parse_aba_excel(filepath)
    df_flat, stats = process_aba(df_raw, config)
    df_inverted = invert_to_asin_keyword(df_flat)
    keywords_dedup = extract_dedup_keywords(df_flat)
    asins_dedup = sorted(df_inverted["asin"].drop_duplicates().tolist())
    return df_flat, df_inverted, keywords_dedup, asins_dedup, stats


from .aba_processor import (
    parse_aba_excel, process_aba, invert_to_asin_keyword,
    extract_dedup_keywords,
)
from .sorftime_client import SorftimeClient


class PipelineProgress:
    """进度回调。"""
    def __init__(self):
        self.current_step = ""
        self.percent = 0
        self.message = ""
        self.logs: list[str] = []

    def update(self, step: str, percent: int, message: str):
        self.current_step = step
        self.percent = percent
        self.message = message
        self.logs.append(f"[{percent}%] {step}: {message}")


def run_pipeline(
    filepath: str,
    domain: int = 1,
    config: dict = None,
    mock: bool = True,
    progress: PipelineProgress = None,
    data_source: str = "sellersprite",
) -> dict:
    """
    执行完整选品 SOP 流程。

    返回: {"batch_id": str, "candidates": int, "summary": dict}
    """
    if progress is None:
        progress = PipelineProgress()

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    cfg = config or {}
    client = SorftimeClient(mock=mock)
    domain_str = DOMAIN_INT_TO_STR.get(domain, "CA")
    db = None

    try:
        db = SessionLocal()
        # ═══ Step 0: 创建批次记录 ═══
        progress.update("Step 0", 0, "创建批次...")
        batch = Batch(batch_id=batch_id, domain=domain, status="processing")
        db.add(batch)
        db.commit()

        # ═══ Step 1: 读取 & 清洗 ABA ═══
        progress.update("Step 1", 5, "读取 ABA 报表...")
        df_raw = parse_aba_excel(filepath)
        batch.total_aba_rows = len(df_raw)

        progress.update("Step 1", 10, f"开始清洗 {len(df_raw)} 行数据...")
        df_flat, stats = process_aba(df_raw, cfg)
        batch.step1_passed = stats["passed"]

        progress.update("Step 1", 15,
            f"[FUNNEL] total={stats['raw_rows']} "
            f"conv_removed={stats['step']['filter_conv_index']} "
            f"brand_removed={stats['step']['filter_brand']} "
            f"share_removed={stats['step']['filter_share_50']} "
            f"passed={stats['passed']}"
        )
        progress.update("Step 1", 20,
            f"清洗完成: 保留 {stats['passed']} 行, "
            f"淘汰转化系数: {stats['step']['filter_conv_index']}, "
            f"品牌词: {stats['step']['filter_brand']}, "
            f"份额>50%: {stats['step']['filter_share_50']}"
        )

        # 保存 ABA 原始数据到数据库
        for _, row in df_flat.iterrows():
            # 每条展开行存一份（asin_rank 不关键，写1占位）
            db.add(AbaRaw(
                batch_id=batch_id,
                keyword=row["keyword"],
                asin=row["asin"],
                asin_rank=1,
                click_share=row["click_share"],
                conversion_share=row["conversion_share"],
                brand_1=row.get("brand_1", ""),
                brand_2=row.get("brand_2", ""),
                brand_3=row.get("brand_3", ""),
            ))
        db.commit()

        # ═══ Step 2: 倒置 + 去重 ═══
        progress.update("Step 2", 25, "数据倒置: 关键词→ASIN 变为 ASIN→关键词...")
        df_inverted = invert_to_asin_keyword(df_flat)
        keywords_dedup = extract_dedup_keywords(df_flat)

        progress.update("Step 2", 30, f"倒置完成: {len(df_inverted)} 个 ASIN, {len(keywords_dedup)} 个去重关键词")

        # 保存倒置映射到数据库
        for _, row in df_inverted.iterrows():
            db.add(AsinKeyword(
                batch_id=batch_id,
                asin=row["asin"],
                keyword=json.dumps(row["keywords"], ensure_ascii=False),
                conversion_index=row["avg_conversion_index"],
                click_share=row["avg_click_share"],
                conversion_share=row["avg_conversion_share"],
                passed_filter=True,
            ))

        # 保存去重关键词
        for kw in keywords_dedup:
            db.add(DeduplicatedKeyword(batch_id=batch_id, keyword=kw))
        db.commit()

        # ═══ Step 3: 关键词数据补全 ═══
        progress.update("Step 3", 35, f"加载 {len(keywords_dedup)} 个关键词数据...")

        kw_data = {}
        if data_source == "sellersprite":
            # 从卖家精灵 KCR 表读取
            ss_kw_rows = db.query(SellerspriteKeyword).filter(
                SellerspriteKeyword.domain == domain_str,
                SellerspriteKeyword.search_conversion_rate > 0,
            ).all()
            for r in ss_kw_rows:
                kw_data[r.keyword] = _ss_kw_to_dict(r)

            progress.update("Step 3", 45,
                f"卖家精灵 KCR 命中 {len(kw_data)}/{len(keywords_dedup)} 个关键词")
        else:
            # 原 Sorftime 路径（保留兼容）
            kw_need_query = []
            for kw in keywords_dedup:
                cached = db.query(KeywordCache).filter(
                    KeywordCache.keyword == kw, KeywordCache.domain == domain
                ).first()
                if cached and cached.search_conversion_rate is not None:
                    kw_data[kw] = _cache_to_kw_dict(cached)
                else:
                    kw_need_query.append(kw)

            progress.update("Step 3", 40,
                f"缓存命中 {len(keywords_dedup) - len(kw_need_query)}, 需查询 {len(kw_need_query)} 个")

            if kw_need_query:
                def kw_progress(done, total, current_kw):
                    pct = 40 + int(20 * done / total)
                    progress.update("Step 3", pct, f"关键词查询: {done}/{total} — {current_kw}")

                fresh_data = client.batch_query_keywords(kw_need_query, domain, on_progress=kw_progress)
                kw_data.update(fresh_data)

                for kw, data in fresh_data.items():
                    if data.get("_error"):
                        continue
                    existing = db.query(KeywordCache).filter(
                        KeywordCache.keyword == kw, KeywordCache.domain == domain
                    ).first()
                    if not existing:
                        cpc = data.get("Cpc", 0)
                        cpc_range = data.get("CpcRange", [0, 0])
                        db.add(KeywordCache(
                            keyword=kw, domain=domain,
                            keyword_cn_name=data.get("KeywordCNName", ""),
                            rank=data.get("Rank", 0),
                            search_volume=data.get("SearchVolume", 0),
                            cpc=cpc / 100 if cpc else 0,
                            cpc_range_min=cpc_range[0] / 100 if cpc_range else 0,
                            cpc_range_max=cpc_range[1] / 100 if len(cpc_range) > 1 else 0,
                            search_conversion_rate=data.get("SearchConversionRate", 0),
                            search_conversion_rate_d90=data.get("SearchConversionRateD90", 0),
                            click_conversion_rate_d90=data.get("ClickConversionRateD90", 0),
                            sales_volume_90d=data.get("SalesVolumeOf90D", 0),
                            click_of_90d=data.get("ClickOf90D", 0),
                            word_count=data.get("WordCount", 0),
                            product_count=data.get("ProductCount", 0),
                            rank_change_of_weekly=data.get("RankChangeOfWeekly", 0),
                            share_click_rate=data.get("ShareClickRate", 0),
                            share_conversion_rate=data.get("ShareConversionRate", 0),
                            season=data.get("Season", ""),
                            update_date=data.get("Update", ""),
                            department=data.get("Department"),
                            top3_asin=json.dumps(data.get("Top3asin", []), ensure_ascii=False),
                            top3_brand=json.dumps(data.get("Top3Brand", []), ensure_ascii=False),
                            top3_category=json.dumps(data.get("Top3Category", []), ensure_ascii=False),
                            images=json.dumps(data.get("Images", []), ensure_ascii=False),
                            images_from_asin=json.dumps(data.get("ImagesFromAsin", []), ensure_ascii=False),
                            cpc_trend=json.dumps(data.get("CpcTrend", []), ensure_ascii=False),
                            search_volume_trend=json.dumps(data.get("SearchVolumeTrend", []), ensure_ascii=False),
                            search_volume_growth_trend=json.dumps(data.get("SearchVolumeGrowthTrend", []), ensure_ascii=False),
                            search_volume_growth_rate_trend=json.dumps(data.get("SearchVolumeGrowthRateTrend", []), ensure_ascii=False),
                            search_result_of_fp=json.dumps(data.get("SearchResultOfFP", []), ensure_ascii=False),
                            associated_with_category=json.dumps(data.get("AssociatedWithCategory", []), ensure_ascii=False),
                            associated_with_category_detail=json.dumps(data.get("AssociatedWithCategoryDetail", []), ensure_ascii=False),
                            raw_response=json.dumps(data, ensure_ascii=False),
                        ))
                db.commit()

        # 过滤: SearchConversionRate = 0 的关键词
        valid_keywords = {
            kw for kw, data in kw_data.items()
            if not data.get("_error") and data.get("SearchConversionRate", 0) > 0
        }
        removed_kw = len(keywords_dedup) - len(valid_keywords)
        progress.update("Step 3", 60,
            f"商机探测器验证: 保留 {len(valid_keywords)} 个关键词, 删除 {removed_kw} 个(转化率=0)")

        # 更新倒置表的 passed_filter
        for row in db.query(AsinKeyword).filter(AsinKeyword.batch_id == batch_id).all():
            kws = json.loads(row.keyword)
            valid_count = sum(1 for k in kws if k in valid_keywords)
            row.passed_filter = valid_count > 0
        db.commit()

        # 获取有效 ASIN 列表
        valid_asins = set()
        for row in db.query(AsinKeyword).filter(
            AsinKeyword.batch_id == batch_id, AsinKeyword.passed_filter == True
        ).all():
            valid_asins.add(row.asin)

        progress.update("Step 3", 62, f"有效 ASIN: {len(valid_asins)} 个")

        # ═══ Step 4: ASIN 成本数据补全 ═══
        asin_list = sorted(valid_asins)
        progress.update("Step 4", 65, f"加载 {len(asin_list)} 个 ASIN 成本数据...")

        asin_data = {}
        if data_source == "sellersprite":
            ss_prod_rows = db.query(SellerspriteProduct).filter(
                SellerspriteProduct.domain == domain_str,
                SellerspriteProduct.asin.in_(asin_list),
            ).all()
            for r in ss_prod_rows:
                asin_data[r.asin] = _ss_prod_to_dict(r)

            progress.update("Step 4", 80,
                f"卖家精灵产品库命中 {len(asin_data)}/{len(asin_list)} 个 ASIN")
        else:
            # 原 Sorftime 路径
            asin_need_query = []
            for asin in asin_list:
                cached = db.query(AsinCache).filter(
                    AsinCache.asin == asin, AsinCache.domain == domain
                ).first()
                if cached and cached.price is not None:
                    asin_data[asin] = _cache_to_asin_dict(cached)
                else:
                    asin_need_query.append(asin)

            progress.update("Step 4", 68,
                f"ASIN 缓存命中 {len(asin_list) - len(asin_need_query)}, 需查询 {len(asin_need_query)} 个")

            if asin_need_query:
                def asin_progress(done, total, current_asin):
                    pct = 68 + int(22 * done / total)
                    progress.update("Step 4", pct, f"ASIN 查询: {done}/{total}")

                fresh_data = client.batch_query_products(asin_need_query, domain, on_progress=asin_progress)
                asin_data.update(fresh_data)

                for asin, data in fresh_data.items():
                    if data.get("_error"):
                        continue
                    existing = db.query(AsinCache).filter(
                        AsinCache.asin == asin, AsinCache.domain == domain
                    ).first()
                    if not existing:
                        db.add(AsinCache(
                            asin=asin, domain=domain,
                            title=data.get("Title", ""),
                            description=data.get("Description", ""),
                            brand=data.get("Brand", ""),
                            parent_asin=data.get("ParentAsin", ""),
                            product_type=data.get("ProductType", ""),
                            store_name=data.get("StoreName", ""),
                            price=data.get("Price", 0) / 100 if data.get("Price") else 0,
                            list_price=data.get("ListPrice", 0) / 100 if data.get("ListPrice") else 0,
                            sales_price=data.get("SalesPrice", 0) / 100 if data.get("SalesPrice") else 0,
                            coupon=data.get("Coupon", 0),
                            fba_fee=data.get("FbaFee", 0) / 100 if data.get("FbaFee") else 0,
                            platform_fee=data.get("PlatformFee", 0) / 100 if data.get("PlatformFee") else 0,
                            profit=data.get("Profit", 0) / 100 if data.get("Profit") else 0,
                            profit_rate=data.get("ProfitRate", 0),
                            ship_cost=data.get("ShipCost", 0) / 100 if data.get("ShipCost") else 0,
                            is_fba=data.get("IsFBA", False),
                            ships_from=data.get("ShipsFrom", ""),
                            buybox_seller=data.get("BuyboxSeller", ""),
                            buybox_seller_id=data.get("BuyboxSellerId"),
                            buybox_seller_address=data.get("BuyboxSellerAddress"),
                            ratings_count=data.get("RatingsCount", 0),
                            ratings=data.get("Ratings", 0),
                            one_start_ratings=data.get("OneStartRatings", 0),
                            two_start_ratings=data.get("TwoStartRatings", 0),
                            three_start_ratings=data.get("ThreeStartRatings", 0),
                            four_start_ratings=data.get("FourStartRatings", 0),
                            five_start_ratings=data.get("FiveStartRatings", 0),
                            asin_sales_count=data.get("AsinSalesCount", 0),
                            off_sale=data.get("OffSale", 0),
                            rank=data.get("Rank", 0),
                            category=json.dumps(data.get("Category", []), ensure_ascii=False),
                            bsr_category=json.dumps(data.get("BsrCategory", []), ensure_ascii=False),
                            seller_count=data.get("SellerCount", 0),
                            online_date=data.get("OnlineDate", ""),
                            online_days=data.get("OnlineDays", 0),
                            has_video=data.get("HasVideo", False),
                            aplus=data.get("APlus", False),
                            has_brand_store=data.get("HasBrandStore", False),
                            size=json.dumps(data.get("Size", []), ensure_ascii=False),
                            weight=data.get("Weight", 0),
                            deal_type=data.get("DealType", ""),
                            brand_promotion=data.get("BrandPromotion", ""),
                            extra_savings=json.dumps(data.get("ExtraSavings", []), ensure_ascii=False),
                            photo=json.dumps(data.get("Photo", []), ensure_ascii=False),
                            ebc_photo=json.dumps(data.get("EBCPhoto", []), ensure_ascii=False),
                            feature=json.dumps(data.get("Feature", {}), ensure_ascii=False),
                            property=data.get("Property", ""),
                            product_info=data.get("ProductInfo", ""),
                            product_badge=json.dumps(data.get("ProductBadge", []), ensure_ascii=False),
                            deal_trend=json.dumps(data.get("DealTrend", []), ensure_ascii=False),
                            price_trend=json.dumps(data.get("PriceTrend", []), ensure_ascii=False),
                            list_price_trend=json.dumps(data.get("ListPriceTrend", []), ensure_ascii=False),
                            rank_trend=json.dumps(data.get("RankTrend"), ensure_ascii=False),
                            bsr_rank_trend=json.dumps(data.get("BsrRankTrend", []), ensure_ascii=False),
                            listing_sales_volume_of_daily_trend=json.dumps(data.get("ListingSalesVolumeOfDailyTrend", []), ensure_ascii=False),
                            listing_sales_volume_of_month_trend=json.dumps(data.get("ListingSalesVolumeOfMonthTrend", []), ensure_ascii=False),
                            listing_sales_of_daily_trend=json.dumps(data.get("ListingSalesOfDailyTrend", []), ensure_ascii=False),
                            listing_sales_of_month_trend=json.dumps(data.get("ListingSalesOfMonthTrend", []), ensure_ascii=False),
                            variation_asin=json.dumps(data.get("VariationASIN", []), ensure_ascii=False),
                            attribute=json.dumps(data.get("Attribute", []), ensure_ascii=False),
                            fba_detail=json.dumps(data.get("FbaDetetail", []), ensure_ascii=False),
                            update_date=data.get("UpdateDate", ""),
                            variation_asin_count=data.get("VariationASINCount", 0),
                            raw_response=json.dumps(data, ensure_ascii=False),
                        ))
                db.commit()

        # ═══ Step 5: 生成候选清单 ═══
        progress.update("Step 5", 90, "计算净回款，生成候选清单...")

        # 预加载 AbaRaw 数据：按 ASIN 分组，方便快速查找 (keyword → click_share, conversion_share)
        aba_by_asin: dict[str, dict[str, dict]] = {}
        for aba_row in db.query(AbaRaw).filter(AbaRaw.batch_id == batch_id).all():
            aba_by_asin.setdefault(aba_row.asin, {})[aba_row.keyword] = {
                "click_share": aba_row.click_share,
                "conversion_share": aba_row.conversion_share,
            }

        candidates = []
        for row in db.query(AsinKeyword).filter(
            AsinKeyword.batch_id == batch_id, AsinKeyword.passed_filter == True
        ).all():
            asin = row.asin
            kws = json.loads(row.keyword)
            valid_kws = [k for k in kws if k in valid_keywords]

            if not valid_kws:
                continue

            prod = asin_data.get(asin)
            if not prod or prod.get("_error"):
                continue  # 无产品数据或查询出错→跳过，原始数据保留在 DB 不删

            # 逐关键词计算 CPA，收集 est_clicks 加权
            kw_cpa_pairs = []  # (est_clicks, cpa, cpc, sv, scr, sv90, ci)
            aba_map = aba_by_asin.get(asin, {})

            for kw in valid_kws:
                kdata = kw_data.get(kw, {})
                if not kdata or kdata.get("_error"):
                    continue

                aba = aba_map.get(kw)
                if not aba or aba["click_share"] == 0:
                    continue

                click_share = aba["click_share"]         # % (e.g. 40.41)
                conversion_share = aba["conversion_share"]  # % (e.g. 12.31)
                ci = conversion_share / click_share       # 转化系数

                if data_source == "sellersprite":
                    cpc = kdata.get("CpcRecommended", 0)
                    sv = kdata.get("SearchVolume", 0)
                    scr = kdata.get("SearchConversionRate", 0)  # 已是 % 形式
                    sv90 = kdata.get("SalesVolumeOf90D", 0)
                    click_vol = kdata.get("ClickOf90D", 0)      # 近90天点击量
                else:
                    cpc = kdata.get("Cpc", 0) / 100
                    sv = kdata.get("SearchVolume", 0)
                    scr = kdata.get("SearchConversionRate", 0)
                    sv90 = kdata.get("SalesVolumeOf90D", 0)
                    click_vol = kdata.get("ClickOf90D", 0)

                # 估算点击量 = 点击量 × 点击份额%
                if click_vol > 0:
                    est_clicks = click_vol * click_share / 100
                else:
                    est_clicks = sv * click_share / 100

                # CPA = CPC / (SCR_decimal × CI)
                scr_decimal = scr / 100
                if scr_decimal > 0 and ci > 0:
                    cpa = cpc / (scr_decimal * ci)
                else:
                    cpa = cpc

                kw_cpa_pairs.append((est_clicks, cpa, cpc, sv, scr, sv90, ci))

            if not kw_cpa_pairs:
                continue

            total_est_clicks = sum(p[0] for p in kw_cpa_pairs)

            # 按 est_clicks 加权平均
            weighted_cpa = sum(p[1] * p[0] for p in kw_cpa_pairs) / total_est_clicks
            weighted_cpc = sum(p[2] * p[0] for p in kw_cpa_pairs) / total_est_clicks
            weighted_sv = int(sum(p[3] * p[0] for p in kw_cpa_pairs) / total_est_clicks)
            weighted_scr = sum(p[4] * p[0] for p in kw_cpa_pairs) / total_est_clicks
            weighted_sv90 = int(sum(p[5] * p[0] for p in kw_cpa_pairs) / total_est_clicks)
            weighted_ci = sum(p[6] * p[0] for p in kw_cpa_pairs) / total_est_clicks

            if data_source == "sellersprite":
                price = prod.get("Price", 0)               # 本币元
                profit_rate = prod.get("ProfitRate", 0)    # 已扣 FBA+佣金
                fba_fee = prod.get("FbaFee", 0)
                platform_fee = 0                           # 已含在利润率里
            else:
                price = prod.get("Price", 0) / 100 if prod.get("Price") else 0
                profit_rate = prod.get("ProfitRate", 0) / 100 if prod.get("ProfitRate") else 0
                fba_fee = prod.get("FbaFee", 0) / 100 if prod.get("FbaFee") else 0
                platform_fee = prod.get("PlatformFee", 0) / 100 if prod.get("PlatformFee") else 0

            # 净回款 = 售价 × 利润率 - 加权CPA × 0.5（实际CPA约估算值的50%）
            net_repayment = price * profit_rate - weighted_cpa * 0.5

            # 海运费用估算
            from .shipping import estimate_shipping
            rate_overrides = {}
            if cfg.get("ship_cbm_rate"):
                rate_overrides["cbm"] = float(cfg["ship_cbm_rate"])
            if cfg.get("ship_handling") is not None:
                rate_overrides["handling"] = float(cfg["ship_handling"])
            shipping = estimate_shipping(
                prod.get("PackageSize", ""), domain_str,
                actual_weight_g=prod.get("Weight", 0),
                rate_overrides=rate_overrides or None,
            )
            shipping_cost = shipping.cost_per_unit if shipping else 0

            candidates.append({
                "asin": asin,
                "keywords": valid_kws,
                "json_keywords": json.dumps(valid_kws, ensure_ascii=False),
                "keyword_count": len(valid_kws),
                "avg_conversion_index": round(weighted_ci, 4),
                "cpc": round(weighted_cpc, 4),
                "search_volume": weighted_sv,
                "search_conversion_rate": round(weighted_scr, 2),
                "sales_volume_90d": weighted_sv90,
                "price": round(price, 2),
                "fba_fee": round(fba_fee, 2),
                "platform_fee": round(platform_fee, 2),
                "profit_rate": round(prod.get("ProfitRate", 0), 2),
                "net_repayment": round(net_repayment, 4),
                "shipping_cost": round(shipping_cost, 2),
                "brand": prod.get("Brand", ""),
                "ratings_count": prod.get("RatingsCount", 0),
                "ratings": prod.get("Ratings", 0),
                "online_date": prod.get("OnlineDate", ""),
                "is_fba": prod.get("IsFBA", False),
            })

        # 按净回款降序排列
        candidates.sort(key=lambda x: x["net_repayment"], reverse=True)

        # 写入 candidate 表
        for c in candidates:
            db.add(Candidate(
                batch_id=batch_id,
                asin=c["asin"],
                keywords=c["json_keywords"],
                keyword_count=c["keyword_count"],
                avg_conversion_index=c["avg_conversion_index"],
                cpc=c["cpc"],
                search_volume=c["search_volume"],
                search_conversion_rate=c["search_conversion_rate"],
                sales_volume_90d=c["sales_volume_90d"],
                price=c["price"],
                fba_fee=c["fba_fee"],
                platform_fee=c["platform_fee"],
                profit_rate=c["profit_rate"],
                net_repayment=c["net_repayment"],
                shipping_cost=c.get("shipping_cost", 0),
                brand=c["brand"],
                ratings_count=c["ratings_count"],
                ratings=c["ratings"],
                online_date=c["online_date"],
                is_fba=c["is_fba"],
            ))

        # 更新批次状态
        batch.status = "done"
        batch.final_candidates = len(candidates)
        batch.step3_passed = len(valid_keywords)
        db.commit()

        progress.update("完成", 100, f"候选清单: {len(candidates)} 个 ASIN")
        _cleanup_old_batches(keep=3)

        return {
            "batch_id": batch_id,
            "candidate_count": len(candidates),
            "summary": {
                "total_aba_rows": batch.total_aba_rows,
                "step1_passed": batch.step1_passed,
                "step3_passed": batch.step3_passed,
                "final_candidates": batch.final_candidates,
                "sorftime_requests": client._request_count,
            },
            "candidates": candidates,
        }

    except Exception as e:
        if db:
            db.rollback()
            try:
                batch.status = "failed"
                batch.error_message = str(e)
                db.commit()
            except Exception:
                pass
        raise
    finally:
        if db:
            db.close()


def run_auto_pipeline(
    aba_filepath: str,
    domain_str: str = "CA",
    config: dict = None,
    progress: PipelineProgress = None,
) -> dict:
    """一键全自动管线：上传 ABA → 自动跑卖家精灵脚本 → 入库 → 生成候选清单。"""
    if progress is None:
        progress = PipelineProgress()

    cfg = config or {}
    domain_map = {"US": 1, "UK": 2, "DE": 3, "JP": 6, "CA": 7}
    domain_int = domain_map.get(domain_str, 7)
    uploads_dir = _PROJECT_ROOT / "sourcing-tool" / "uploads"
    uploads_dir.mkdir(exist_ok=True)

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]

    try:
        # ═══ Step A: 解析 ABA ═══
        progress.update("解析 ABA", 5, "读取 ABA 报表...")
        df_raw = parse_aba_excel(aba_filepath)

        # ═══ Step B: 清洗筛选 ═══
        progress.update("清洗筛选", 10, "执行三大过滤（转化系数/品牌词/集中度）...")
        df_flat, df_inverted, keywords_cleaned, asins_cleaned, clean_stats = _run_aba_cleaning(
            aba_filepath, cfg)

        progress.update("清洗筛选", 15,
            f"[漏斗] 原始={clean_stats['raw_rows']} → "
            f"转化系数={clean_stats['step']['filter_conv_index']} "
            f"品牌词={clean_stats['step']['filter_brand']} "
            f"集中度={clean_stats['step']['filter_share_50']} → "
            f"清洗通过={clean_stats['passed']} 行, "
            f"去重后 {len(keywords_cleaned)} 个关键词, {len(asins_cleaned)} 个 ASIN")

        # ═══ Step C: 跑 KCR（OpenCLI adapter） ═══
        progress.update("KCR 查询", 20, f"启动卖家精灵 KCR 查询（{len(keywords_cleaned)} 个关键词，已清洗去重）...")

        period = cfg.get("period", "D90")
        kcr_batches = [keywords_cleaned[i:i + KCR_BATCH_MAX]
                       for i in range(0, len(keywords_cleaned), KCR_BATCH_MAX)]
        total_kcr_batches = len(kcr_batches)

        kcr_excels = []
        for i, batch in enumerate(kcr_batches):
            pct = 20 + int(25 * (i + 1) / total_kcr_batches)
            progress.update("KCR 查询", pct,
                f"KCR 批次 {i+1}/{total_kcr_batches} ({len(batch)} 个关键词)")

            output_path = uploads_dir / f"KCR-{batch_id}-{i+1:03d}.xlsx"
            try:
                path = _opencli_kcr_export(batch, domain_str, period, output_path)
                if path:
                    kcr_excels.append(path)
            except Exception as e:
                progress.update("KCR 查询", pct, f"KCR 批次 {i+1} 失败: {e}")
                raise

        if kcr_excels:
            from .sellersprite_import import parse_kcr_excel, import_kcr_to_db
            all_kcr_records = []
            for excel_path in kcr_excels:
                all_kcr_records.extend(parse_kcr_excel(str(excel_path)))
            kcr_count = import_kcr_to_db(all_kcr_records, domain_str, f"auto_{batch_id}",
                                         expiry_days=cfg.get("data_expiry_days", 30))
            progress.update("KCR 导入", 50, f"KCR 入库 {kcr_count} 个关键词（{len(kcr_excels)} 个文件）")
        else:
            raise RuntimeError("KCR 查询失败，未生成任何导出文件")

        # ═══ Step D: 跑产品库（OpenCLI adapter） ═══
        progress.update("产品库查询", 55, f"启动卖家精灵产品库查询（{len(asins_cleaned)} 个 ASIN，已去重）...")

        asin_batches = [asins_cleaned[i:i + ASIN_BATCH_MAX]
                        for i in range(0, len(asins_cleaned), ASIN_BATCH_MAX)]
        total_asin_batches = len(asin_batches)
        now_str = time.strftime("%Y%m%d-%H%M%S")

        prod_excels = []
        for i, batch in enumerate(asin_batches):
            pct = 55 + int(30 * (i + 1) / total_asin_batches)
            progress.update("产品库查询", pct,
                f"产品库批次 {i+1}/{total_asin_batches} ({len(batch)} 个 ASIN)")

            batch_label = f"{batch_id[:12]}-{domain_str}-{now_str}-{i+1:03d}"
            output_path = uploads_dir / f"Product-{batch_label}.xlsx"
            try:
                path = _opencli_product_batch(batch, domain_str, batch_label, output_path)
                if path:
                    prod_excels.append(path)
            except Exception as e:
                progress.update("产品库查询", pct, f"产品库批次 {i+1} 失败: {e}")
                raise

        if prod_excels:
            from .sellersprite_import import parse_product_excel, import_product_to_db
            all_prod_records = []
            for excel_path in prod_excels:
                all_prod_records.extend(parse_product_excel(str(excel_path)))
            prod_count = import_product_to_db(all_prod_records, domain_str, f"auto_{batch_id}",
                                              expiry_days=cfg.get("data_expiry_days", 30))
            progress.update("产品库导入", 85, f"产品库入库 {prod_count} 个 ASIN（{len(prod_excels)} 个文件）")
        else:
            raise RuntimeError("产品库查询失败，未生成任何导出文件")

        # ═══ Step E: 跑管线 ═══
        progress.update("管线筛选", 90, "启动漏斗筛选...")

        # 把 ABA 数据写成标准格式 CSV 给管线
        import pandas as pd
        # 直接从原始 DataFrame 取列
        pipeline_csv = uploads_dir / f"pipeline_{batch_id}.csv"

        # 用 original DataFrame columns，直接保存
        df_raw.to_csv(str(pipeline_csv), index=False, encoding="utf-8-sig")

        result = run_pipeline(
            filepath=str(pipeline_csv),
            domain=domain_int,
            config=cfg,
            mock=True,
            progress=progress,
            data_source="sellersprite",
        )

        progress.update("完成", 100, f"候选清单: {result['candidate_count']} 个 ASIN")
        _cleanup_old_batches(keep=3)
        return result

    except Exception as e:
        progress.update("失败", 0, str(e))
        raise


def _ss_kw_to_dict(r: SellerspriteKeyword) -> dict:
    """SellerspriteKeyword ORM → pipeline 用的 kw_data dict。"""
    return {
        "Keyword": r.keyword,
        "SearchVolume": r.search_volume,
        "Cpc": r.cpc_recommended,
        "CpcRecommended": r.cpc_recommended,
        "CpcHigh": r.cpc_high,
        "CpcLow": r.cpc_low,
        "SearchConversionRate": r.search_conversion_rate,   # 已是 %
        "ClickConversionRate": r.click_conversion_rate,
        "SalesVolumeOf90D": r.sales_volume_90d,
        "ClickOf90D": r.purchases_90d,
        "ShareClickRate": r.click_share,
        "ShareConversionRate": r.conv_share,
        "AvgPrice": r.avg_price,
        "CpaRecommended": r.cpa_recommended,
        "AcosRecommended": r.acos_recommended,
        "Budget": r.budget,
        "Top3asin": json.loads(r.top3_asins) if r.top3_asins else [],
        "Top10Asins": r.top10_asins,
    }


def _ss_prod_to_dict(r: SellerspriteProduct) -> dict:
    """SellerspriteProduct ORM → pipeline 用的 asin_data dict。"""
    return {
        "Asin": r.asin,
        "Title": r.title,
        "Brand": r.brand,
        "Price": r.price,               # 本币元
        "FbaFee": r.fba_fee,
        "ProfitRate": r.profit_rate,     # 小数形式（0.5 = 50%）
        "Profit": r.profit,
        "RatingsCount": r.ratings_count,
        "Ratings": r.ratings,
        "OnlineDate": r.online_date,
        "OnlineDays": r.online_days,
        "IsFBA": r.is_fba,
        "SellerCount": r.seller_count,
        "MonthlySales": r.monthly_sales,
        "MonthlyRevenue": r.monthly_revenue,
        "Aplus": r.aplus,
        "HasVideo": r.has_video,
        "Badge": r.badge,
        "Lqs": r.lqs,
        "MainCategory": r.main_category,
        "MainBsr": r.main_bsr,
        "SubCategory": r.sub_category,
        "SubBsr": r.sub_bsr,
        "ShipMethod": r.ship_method,
        "PackageSize": r.package_size or r.size or "",
    }


def _cache_to_kw_dict(cached: KeywordCache) -> dict:
    """从缓存行恢复为与 API 返回一致的数据字典。"""
    return {
        "Keyword": cached.keyword,
        "KeywordCNName": cached.keyword_cn_name,
        "Rank": cached.rank,
        "SearchVolume": cached.search_volume,
        "Cpc": (cached.cpc or 0) * 100,
        "CpcRange": [(cached.cpc_range_min or 0) * 100, (cached.cpc_range_max or 0) * 100],
        "SearchConversionRate": cached.search_conversion_rate,
        "SearchConversionRateD90": cached.search_conversion_rate_d90,
        "ClickConversionRateD90": cached.click_conversion_rate_d90,
        "SalesVolumeOf90D": cached.sales_volume_90d,
        "ClickOf90D": cached.click_of_90d,
        "WordCount": cached.word_count,
        "ProductCount": cached.product_count,
        "RankChangeOfWeekly": cached.rank_change_of_weekly,
        "ShareClickRate": cached.share_click_rate,
        "ShareConversionRate": cached.share_conversion_rate,
        "Season": cached.season,
        "Update": cached.update_date,
        "Department": cached.department,
        "Top3asin": json.loads(cached.top3_asin) if cached.top3_asin else [],
        "Top3Brand": json.loads(cached.top3_brand) if cached.top3_brand else [],
        "Top3Category": json.loads(cached.top3_category) if cached.top3_category else [],
        "Images": json.loads(cached.images) if cached.images else [],
        "ImagesFromAsin": json.loads(cached.images_from_asin) if cached.images_from_asin else [],
        "CpcTrend": json.loads(cached.cpc_trend) if cached.cpc_trend else [],
        "SearchVolumeTrend": json.loads(cached.search_volume_trend) if cached.search_volume_trend else [],
        "SearchVolumeGrowthTrend": json.loads(cached.search_volume_growth_trend) if cached.search_volume_growth_trend else [],
        "SearchVolumeGrowthRateTrend": json.loads(cached.search_volume_growth_rate_trend) if cached.search_volume_growth_rate_trend else [],
        "SearchResultOfFP": json.loads(cached.search_result_of_fp) if cached.search_result_of_fp else [],
        "AssociatedWithCategory": json.loads(cached.associated_with_category) if cached.associated_with_category else [],
        "AssociatedWithCategoryDetail": json.loads(cached.associated_with_category_detail) if cached.associated_with_category_detail else [],
    }


def _cache_to_asin_dict(cached: AsinCache) -> dict:
    """从缓存行恢复为与 API 返回一致的数据字典。"""
    return {
        "Asin": cached.asin,
        "Title": cached.title,
        "Description": cached.description,
        "Brand": cached.brand,
        "ParentAsin": cached.parent_asin,
        "ProductType": cached.product_type,
        "StoreName": cached.store_name,
        "Price": (cached.price or 0) * 100,
        "ListPrice": (cached.list_price or 0) * 100,
        "SalesPrice": (cached.sales_price or 0) * 100,
        "Coupon": cached.coupon,
        "FbaFee": (cached.fba_fee or 0) * 100,
        "PlatformFee": (cached.platform_fee or 0) * 100,
        "Profit": (cached.profit or 0) * 100,
        "ProfitRate": cached.profit_rate,
        "ShipCost": (cached.ship_cost or 0) * 100,
        "IsFBA": cached.is_fba,
        "ShipsFrom": cached.ships_from,
        "BuyboxSeller": cached.buybox_seller,
        "BuyboxSellerId": cached.buybox_seller_id,
        "BuyboxSellerAddress": cached.buybox_seller_address,
        "RatingsCount": cached.ratings_count,
        "Ratings": cached.ratings,
        "OneStartRatings": cached.one_start_ratings,
        "TwoStartRatings": cached.two_start_ratings,
        "ThreeStartRatings": cached.three_start_ratings,
        "FourStartRatings": cached.four_start_ratings,
        "FiveStartRatings": cached.five_start_ratings,
        "AsinSalesCount": cached.asin_sales_count,
        "OffSale": cached.off_sale,
        "Rank": cached.rank,
        "Category": json.loads(cached.category) if cached.category else [],
        "BsrCategory": json.loads(cached.bsr_category) if cached.bsr_category else [],
        "SellerCount": cached.seller_count,
        "OnlineDate": cached.online_date,
        "OnlineDays": cached.online_days,
        "HasVideo": cached.has_video,
        "APlus": cached.aplus,
        "HasBrandStore": cached.has_brand_store,
        "Size": json.loads(cached.size) if cached.size else [],
        "Weight": cached.weight,
        "DealType": cached.deal_type,
        "BrandPromotion": cached.brand_promotion,
        "ExtraSavings": json.loads(cached.extra_savings) if cached.extra_savings else [],
        "Photo": json.loads(cached.photo) if cached.photo else [],
        "EBCPhoto": json.loads(cached.ebc_photo) if cached.ebc_photo else [],
        "Feature": json.loads(cached.feature) if cached.feature else {},
        "Property": cached.property,
        "ProductInfo": cached.product_info,
        "ProductBadge": json.loads(cached.product_badge) if cached.product_badge else [],
        "DealTrend": json.loads(cached.deal_trend) if cached.deal_trend else [],
        "PriceTrend": json.loads(cached.price_trend) if cached.price_trend else [],
        "ListPriceTrend": json.loads(cached.list_price_trend) if cached.list_price_trend else [],
        "RankTrend": json.loads(cached.rank_trend) if cached.rank_trend else None,
        "BsrRankTrend": json.loads(cached.bsr_rank_trend) if cached.bsr_rank_trend else [],
        "ListingSalesVolumeOfDailyTrend": json.loads(cached.listing_sales_volume_of_daily_trend) if cached.listing_sales_volume_of_daily_trend else [],
        "ListingSalesVolumeOfMonthTrend": json.loads(cached.listing_sales_volume_of_month_trend) if cached.listing_sales_volume_of_month_trend else [],
        "ListingSalesOfDailyTrend": json.loads(cached.listing_sales_of_daily_trend) if cached.listing_sales_of_daily_trend else [],
        "ListingSalesOfMonthTrend": json.loads(cached.listing_sales_of_month_trend) if cached.listing_sales_of_month_trend else [],
        "VariationASIN": json.loads(cached.variation_asin) if cached.variation_asin else [],
        "Attribute": json.loads(cached.attribute) if cached.attribute else [],
        "FbaDetetail": json.loads(cached.fba_detail) if cached.fba_detail else [],
        "UpdateDate": cached.update_date,
        "VariationASINCount": cached.variation_asin_count,
    }
