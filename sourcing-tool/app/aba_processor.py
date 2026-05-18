"""ABA 报表处理引擎 — 清洗、过滤、倒置、关键词去重。"""
import pandas as pd
import re
from typing import Optional


DEFAULT_CONFIG = {
    "conv_index_min": 1.0,
    "share_max": 50.0,
    "asin_prefix": "B0",
}


def parse_metadata(filepath: str) -> dict:
    """从 CSV 第一行和文件名提取元数据（domain, report_date）。"""
    import os
    basename = os.path.basename(filepath)

    # 从文件名提取 domain: CA_xxx → CA, US_xxx → US
    domain = "CA"
    if basename.startswith("US_"):
        domain = "US"
    elif basename.startswith("MX_"):
        domain = "MX"

    # 从第一行元数据提取 report_date
    report_date = None
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            meta_line = f.readline().strip()
            # 报告范围=["每月"],选择年份=["2026"],选择月份=["3 月"]
            year_m = re.search(r'选择年份=\["(\d+)"\]', meta_line)
            month_m = re.search(r'选择月份=\["(\d+)', meta_line)
            if year_m and month_m:
                y, m = int(year_m.group(1)), int(month_m.group(1))
                from calendar import monthrange
                last_day = monthrange(y, m)[1]
                report_date = f"{y}-{m:02d}-{last_day:02d}"
    except Exception:
        pass

    if not report_date:
        # 兜底：从文件名末尾取日期 2026_03_31
        date_m = re.search(r'(\d{4})_(\d{2})_(\d{2})', basename)
        if date_m:
            report_date = f"{date_m.group(1)}-{date_m.group(2)}-{date_m.group(3)}"

    return {"domain": domain, "report_date": report_date or "unknown"}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名为小写去空格。"""
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _find_col(df: pd.DataFrame, patterns: list[str]) -> Optional[str]:
    """按模式匹配列名，返回第一个匹配到的列名。"""
    for p in patterns:
        for c in df.columns:
            if p in c:
                return c
    return None


def _find_ranked_cols(df: pd.DataFrame, base_patterns: list[str], max_rank: int = 3) -> list[str]:
    """查找带序号1/2/3的列，如 asin_#1_click_share。返回排序后的列名列表。"""
    result = []
    for i in range(1, max_rank + 1):
        found = None
        for bp in base_patterns:
            for c in df.columns:
                if bp in c and str(i) in c:
                    found = c
                    break
            if found:
                break
        result.append(found)
    return result


def parse_aba_excel(filepath: str) -> pd.DataFrame:
    """读取 ABA 报表 Excel/CSV，统一为标准化列名。自动跳过第 1 行元数据。"""
    ext = filepath.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        # 检测第一行是否为元数据行
        skip = 0
        with open(filepath, "r", encoding="utf-8-sig") as f:
            first_line = f.readline().strip()
            if first_line.startswith("报告范围"):
                skip = 1

        try:
            df = pd.read_csv(filepath, encoding="utf-8-sig", skiprows=skip)
        except Exception:
            df = pd.read_csv(filepath, encoding="gbk", skiprows=skip)
    else:
        df = pd.read_excel(filepath)

    df = df.fillna(0)
    df = _normalize_columns(df)
    return df


def detect_aba_format(df: pd.DataFrame) -> dict:
    """自动识别 ABA 报表的列结构，返回列名映射。"""
    mapping = {}

    # 关键词列
    kw_col = _find_col(df, ["关键词", "搜索词", "keyword", "search_term"])
    if not kw_col:
        raise ValueError("未找到关键词列，请确认报表包含关键词/搜索词列")
    mapping["keyword"] = kw_col

    # 频率排名（可选）
    rank_col = _find_col(df, ["频率排名", "frequency_rank", "rank"])
    mapping["rank"] = rank_col

    # Top 3 ASIN: 找 asin 列并关联点击份额和转化份额
    asin_cols = sorted([c for c in df.columns if "asin" in c and any(str(n) in c for n in range(1, 4))])
    click_cols = sorted([c for c in df.columns if "点击份额" in c or ("click" in c and "share" in c)])
    conv_cols = sorted([c for c in df.columns if "转化份额" in c or ("conversion" in c and "share" in c)])

    mapping["asin_cols"] = asin_cols[:3] if len(asin_cols) >= 3 else asin_cols
    mapping["click_cols"] = click_cols[:3] if len(click_cols) >= 3 else click_cols
    mapping["conv_cols"] = conv_cols[:3] if len(conv_cols) >= 3 else conv_cols

    # Top 3 品牌列
    brand_cols = sorted([
        c for c in df.columns
        if ("品牌" in c or "brand" in c)
        and "类别" not in c
        and "category" not in c
        and any(str(n) in c for n in range(1, 4))
    ])[:3]
    mapping["brand_cols"] = brand_cols

    return mapping


def process_aba(df: pd.DataFrame, config: dict = None) -> tuple[pd.DataFrame, dict]:
    """
    执行 ABA 清洗三件套：
    1. 计算转化系数 = 转化份额 / 点击份额
    2. 排除转化系数 < 阈值的行
    3. 排除关键词包含 Top3 品牌名的行
    4. 排除任一 ASIN 点击份额或转化份额 > 阈值的行

    返回 (清洗后DataFrame, 统计dict)
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    stats = {"raw_rows": len(df), "step": {}}

    # 自动识别列结构
    col_map = detect_aba_format(df)
    kw_col = col_map["keyword"]
    asin_cols = col_map["asin_cols"]
    click_cols = col_map["click_cols"]
    conv_cols = col_map["conv_cols"]
    brand_cols = col_map["brand_cols"]

    if len(asin_cols) < 3 or len(click_cols) < 3 or len(conv_cols) < 3:
        raise ValueError(
            f"未找到完整的 Top3 ASIN 数据列。"
            f"ASIN列: {len(asin_cols)}, 点击份额列: {len(click_cols)}, 转化份额列: {len(conv_cols)}。"
            f"\n请确认 Excel 包含 #1/#2/#3 号 ASIN、点击份额、转化份额。"
        )

    # ── 展开：每行3个ASIN → 3行 ──
    rows = []
    for _, row in df.iterrows():
        keyword = str(row[kw_col]).strip().lower()
        if keyword in ("0", "nan", ""):
            continue
        for i in range(3):
            asin = str(row[asin_cols[i]]).upper().strip()
            if asin in ("0", "NAN", ""):
                continue
            if not asin.startswith(cfg["asin_prefix"]):
                continue
            try:
                click_s = float(str(row[click_cols[i]]).replace("%", ""))
                conv_s = float(str(row[conv_cols[i]]).replace("%", ""))
            except (ValueError, IndexError):
                continue
            rows.append({
                "keyword": keyword,
                "asin": asin,
                "click_share": click_s,
                "conversion_share": conv_s,
                "brand_1": str(row[brand_cols[0]]) if len(brand_cols) > 0 and brand_cols[0] else "",
                "brand_2": str(row[brand_cols[1]]) if len(brand_cols) > 1 and brand_cols[1] else "",
                "brand_3": str(row[brand_cols[2]]) if len(brand_cols) > 2 and brand_cols[2] else "",
            })

    df_flat = pd.DataFrame(rows)
    if df_flat.empty:
        raise ValueError("展开后无有效数据，请检查报表格式。")

    stats["after_expand"] = len(df_flat)

    # ── 计算转化系数 ──
    df_flat["conversion_index"] = df_flat["conversion_share"] / df_flat["click_share"]
    df_flat["conversion_index"] = df_flat["conversion_index"].replace([float("inf"), -float("inf")], 0)

    # ── 过滤1：转化系数 < 阈值 ──
    before = len(df_flat)
    df_flat = df_flat[df_flat["conversion_index"] >= cfg["conv_index_min"]]
    stats["step"]["filter_conv_index"] = before - len(df_flat)

    # ── 过滤2：品牌词 ──
    brand_names = set()
    for bc in brand_cols:
        if bc:
            brand_names.update(
                str(v).lower().strip()
                for v in df[bc].dropna().unique()
                if str(v).lower().strip() not in ("0", "nan", "")
            )

    def is_brand_keyword(keyword: str) -> bool:
        for brand in brand_names:
            if brand and len(brand) > 1 and brand in keyword:
                return True
        return False

    before = len(df_flat)
    df_flat = df_flat[~df_flat["keyword"].apply(is_brand_keyword)]
    stats["step"]["filter_brand"] = before - len(df_flat)

    # ── 过滤3：单份额 > 50% ──
    before = len(df_flat)
    df_flat = df_flat[
        (df_flat["click_share"] <= cfg["share_max"]) &
        (df_flat["conversion_share"] <= cfg["share_max"])
    ]
    stats["step"]["filter_share_50"] = before - len(df_flat)
    stats["passed"] = len(df_flat)
    stats["removed"] = stats["raw_rows"] - len(df_flat)

    return df_flat, stats


def invert_to_asin_keyword(df_flat: pd.DataFrame) -> pd.DataFrame:
    """
    倒置：关键词→ASIN 变为 ASIN→关键词列表。
    每个ASIN聚合其所有关联关键词和转化系数。
    """
    grouped = df_flat.groupby("asin").agg(
        keywords=("keyword", lambda x: sorted(set(x))),
        keyword_count=("keyword", "count"),
        avg_conversion_index=("conversion_index", "mean"),
        avg_click_share=("click_share", "mean"),
        avg_conversion_share=("conversion_share", "mean"),
    ).reset_index()
    return grouped


def extract_dedup_keywords(df_flat: pd.DataFrame) -> list[str]:
    """提取去重关键词列表。"""
    return sorted(df_flat["keyword"].drop_duplicates().tolist())


def export_to_excel(candidates: list[dict], filepath: str):
    """导出候选清单到 Excel。"""
    df = pd.DataFrame(candidates)
    with pd.ExcelWriter(filepath, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="候选清单", index=False)
