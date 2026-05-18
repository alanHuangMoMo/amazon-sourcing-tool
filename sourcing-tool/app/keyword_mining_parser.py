"""卖家精灵「关键词挖掘」Excel 解析 — 34 列固定顺序 + Unique Words sheet。

与 sellersprite_import.py（KCR 格式）互补，专门处理关键词挖掘导出格式。
"""
import json
import re
import pandas as pd

from .models import SessionLocal, SellerspriteKeyword, WordRoot


def extract_period_from_filename(filename: str) -> str:
    """从关键词挖掘文件名提取数据月份。
    KeywordMining-US-custom-202403-357091 → "2024-03"
    """
    m = re.search(r'(\d{4})(\d{2})-', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


def parse_keyword_mining(filepath: str) -> list[dict]:
    """解析关键词挖掘 Excel（34 列固定顺序），返回 records 列表。"""
    df = pd.read_excel(filepath, sheet_name=0)
    if df.empty:
        raise ValueError("关键词挖掘 Excel 为空")
    if len(df.columns) < 30:
        raise ValueError(f"列数异常: {len(df.columns)}（预期 ≥30）")

    records = []
    for _, row in df.iterrows():
        try:
            kw = _val(row, 0)
            if not kw or kw in ("0", "nan"):
                continue

            # 解析建议竞价范围 "$0.74-$1.24" → low/high
            cpc_range = _val(row, 19)
            cpc_low, cpc_high = 0.0, 0.0
            if "-" in cpc_range:
                parts = cpc_range.replace("$", "").split("-")
                try:
                    cpc_low = float(parts[0])
                    cpc_high = float(parts[1])
                except ValueError:
                    pass

            # 解析 TOP3 ASIN（cols 24-32：3 组 ASIN + 点击占比 + 转化占比）
            top3 = []
            for base in (24, 27, 30):
                asin = _val(row, base)
                if asin:
                    top3.append({
                        "asin": asin,
                        "click_share": _num(row, base + 1),
                        "conv_share": _num(row, base + 2),
                    })

            records.append({
                "keyword": kw,
                "keyword_cn": _val(row, 1),
                "ac_recommended": _val(row, 2) == "Y",
                "relevance": _num(row, 3),
                "aba_weekly_rank": int(_num(row, 4)),
                "aba_monthly_rank": int(_num(row, 5)),
                "search_volume": int(_num(row, 6)),          # 月搜索量
                "purchases": int(_num(row, 7)),              # 月购买量
                "purchase_rate": _num(row, 8),               # 购买率（小数）
                "impressions": int(_num(row, 9)),            # 展示量
                "clicks": int(_num(row, 10)),                # 点击量
                "spr": int(_num(row, 11)),
                "search_heat": int(_num(row, 12)),           # 搜索热度
                "product_count": int(_num(row, 13)),         # 产品数
                "avg_supply_price": _num(row, 14),           # 均供价
                "ad_competitors": int(_num(row, 15)),        # 广告竞品数
                "click_share": _num(row, 16),                # 广告点击占比（小数）
                "conv_share": _num(row, 17),                 # 转化点击占比（小数）
                "cpc": _num(row, 18),                        # PPC竞价（本币元，$0.99）
                "cpc_low": cpc_low,
                "cpc_high": cpc_high,
                "avg_price": _num(row, 20),                  # 均价
                "review_count": int(_num(row, 21)),          # 评论数
                "rating": _num(row, 22),                     # 评分值
                "category": _val(row, 23),                   # 所属类目
                "top3_asins": top3,
                "top10_asins": _val(row, 33),                # 前十ASIN（逗号分隔）
            })
        except Exception:
            continue

    return records


def parse_unique_words(filepath: str) -> list[dict]:
    """解析关键词挖掘 Excel 的 Unique Words sheet。"""
    try:
        df = pd.read_excel(filepath, sheet_name="Unique Words")
    except (ValueError, Exception):
        return []

    if df.empty or len(df.columns) < 2:
        return []

    records = []
    for _, row in df.iterrows():
        word = _val(row, 0).lower()
        if not word or len(word) < 2:
            continue
        try:
            freq = int(float(str(row.iloc[1]).replace(",", "")))
        except (ValueError, TypeError):
            freq = 0
        if freq > 0:
            records.append({"word": word, "frequency": freq})

    return records


def import_keyword_mining_to_db(records: list[dict], domain: str, batch_label: str, data_period: str = "") -> int:
    """批量写入关键词挖掘数据到 sellersprite_keyword 表。"""
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        count = 0
        now = datetime.now(timezone.utc)

        for r in records:
            try:
                # 删旧插新（同 keyword + domain）
                existing = db.query(SellerspriteKeyword).filter(
                    SellerspriteKeyword.keyword == r["keyword"],
                    SellerspriteKeyword.domain == domain,
                ).first()
                if existing:
                    db.delete(existing)
                    db.flush()

                db.add(SellerspriteKeyword(
                    keyword=r["keyword"],
                    keyword_cn=r.get("keyword_cn", ""),
                    domain=domain,
                    batch_id=batch_label,
                    data_period=data_period,
                    search_volume=r.get("search_volume", 0),
                    purchases_90d=r.get("purchases", 0),
                    clicks=r.get("clicks", 0),
                    search_conversion_rate=r.get("purchase_rate", 0) * 100,
                    click_conversion_rate=r.get("conv_share", 0) * 100,
                    cpc_recommended=r.get("cpc", 0),
                    cpc_high=r.get("cpc_high", 0),
                    cpc_low=r.get("cpc_low", 0),
                    avg_price=r.get("avg_price", 0),
                    click_share=r.get("click_share", 0) * 100,
                    conv_share=r.get("conv_share", 0) * 100,
                    top3_asins=json.dumps(r.get("top3_asins", []), ensure_ascii=False),
                    top10_asins=r.get("top10_asins", ""),
                    raw_response=json.dumps(r, ensure_ascii=False),
                    queried_at=now,
                ))
                count += 1
            except Exception:
                continue

        db.commit()
        return count
    finally:
        db.close()


def import_unique_words_to_db(records: list[dict], domain: str, batch_label: str) -> int:
    """批量写入词根频次到 word_root 表。"""
    db = SessionLocal()
    try:
        count = 0
        for r in records:
            try:
                existing = db.query(WordRoot).filter(
                    WordRoot.word == r["word"],
                    WordRoot.batch_label == batch_label,
                    WordRoot.domain == domain,
                ).first()
                if existing:
                    existing.frequency = r["frequency"]
                else:
                    db.add(WordRoot(
                        word=r["word"],
                        frequency=r["frequency"],
                        domain=domain,
                        batch_label=batch_label,
                    ))
                count += 1
            except Exception:
                continue

        db.commit()
        return count
    finally:
        db.close()


def _val(row, col_idx: int) -> str:
    if col_idx >= len(row.index):
        return ""
    v = row.iloc[col_idx]
    if pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s in ("0", "nan", "None") else s


def _num(row, col_idx: int) -> float:
    if col_idx >= len(row.index):
        return 0.0
    v = row.iloc[col_idx]
    if pd.isna(v):
        return 0.0
    try:
        s = str(v).replace("%", "").replace(",", "").replace("CDN$", "").replace("US$", "").replace("$", "")
        for unit in [" kg", " g", " oz", " lb", " cm", " mm", " m", " in", " inch"]:
            if s.lower().endswith(unit):
                s = s[:-len(unit)]
                break
        return float(s)
    except (ValueError, TypeError):
        return 0.0
