"""从 Sorftime KeywordExtends 拓词并入库"""
import subprocess, json, sqlite3, sys, os

SEEDS = ["custom", "customized", "personalized"]
DOMAIN = 1  # US
PAGE_SIZE = 200
DB_PATH = "d:/claude code/sourcing-tool/data/sourcing.db"

def run_extend(keyword: str) -> list[dict]:
    """调用 sorftime api KeywordExtends，返回关键词列表"""
    import tempfile
    outfile = tempfile.mktemp(suffix=".json")
    params = json.dumps({"keyword": keyword, "pageIndex": 1, "pageSize": PAGE_SIZE})
    # 输出到文件避免 Windows GBK 编码问题
    full_cmd = (
        f'bash C:/Users/alanh/AppData/Roaming/npm/sorftime '
        f'api KeywordExtends \'{params}\' --domain {DOMAIN} > "{outfile}" 2>&1'
    )
    subprocess.run(full_cmd, shell=True, timeout=30)
    with open(outfile, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    os.unlink(outfile)
    # sorftime CLI 输出前有 ANSI 进度文字，找到 JSON 起始
    idx = text.index("{")
    data = json.loads(text[idx:])
    if data.get("Code") != 0:
        raise RuntimeError(f"API error: {data.get('Message')}")
    print(f"  {keyword}: {len(data['Data'])} 个延伸词, 剩余 {data['RequestLeft']} 次")
    return data["Data"]

def store_results(seed: str, items: list[dict]):
    """将延伸词存入 sourcing.db"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS keyword_extends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seed_keyword TEXT NOT NULL,
            keyword TEXT NOT NULL,
            keyword_cn TEXT,
            search_volume INTEGER,
            cpc REAL,
            search_conversion_rate_d90 REAL,
            click_of_90d INTEGER,
            sales_volume_of_90d INTEGER,
            product_count INTEGER,
            share_click_rate REAL,
            share_conversion_rate REAL,
            rank INTEGER,
            word_count INTEGER,
            top3_asin TEXT,
            top3_brand TEXT,
            season TEXT,
            item_index TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(seed_keyword, keyword)
        )
    """)

    inserted = 0
    for item in items:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO keyword_extends
                (seed_keyword, keyword, keyword_cn, search_volume, cpc,
                 search_conversion_rate_d90, click_of_90d, sales_volume_of_90d,
                 product_count, share_click_rate, share_conversion_rate,
                 rank, word_count, top3_asin, top3_brand, season, item_index, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                seed,
                item.get("Keyword", ""),
                item.get("KeywordCNName", ""),
                item.get("SearchVolume", 0),
                item.get("Cpc", 0),
                item.get("SearchConversionRateD90", 0),
                item.get("ClickOf90D", 0),
                item.get("SalesVolumeOf90D", 0),
                item.get("ProductCount", 0),
                item.get("ShareClickRate", 0),
                item.get("ShareConversionRate", 0),
                item.get("Rank", 0),
                item.get("WordCount", 0),
                json.dumps(item.get("Top3asin", []), ensure_ascii=False),
                json.dumps(item.get("Top3Brand", []), ensure_ascii=False),
                item.get("Season", ""),
                item.get("ItemIndex", ""),
                json.dumps(item, ensure_ascii=False)
            ))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            print(f"  skip {item.get('Keyword', '?')}: {e}")

    conn.commit()
    conn.close()
    print(f"  入库 {inserted} 条 (去重后)")
    return inserted

def main():
    print("=== Sorftime KeywordExtends 拓词 ===\n")
    total_stored = 0
    for seed in SEEDS:
        print(f"正在拓展: {seed}")
        items = run_extend(seed)
        n = store_results(seed, items)
        total_stored += n
    print(f"\n=== 完成: 共入库 {total_stored} 条 ===")

if __name__ == "__main__":
    main()
