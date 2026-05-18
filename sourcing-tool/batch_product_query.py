"""
Phase 3: 产品数据批量查询 — 取所有子赛道 ASIN，查卖家精灵产品库。

流程:
  1. 从 final_sub_niche_asin 读取所有唯一 ASIN
  2. 分批 ≤2000 → 调 asin_batch.py 导出
  3. 解析 Excel → 写入 sellersprite_product

用法:
  python batch_product_query.py
  python batch_product_query.py --resume
  python batch_product_query.py --dry-run
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASIN_SCRIPT = PROJECT_ROOT / "sellersprite_asin" / "asin_batch.py"
SCRIPT_PYTHON = r"C:\Users\alanh\AppData\Local\Programs\Python\Python313\python.exe"
DATA_DIR = Path(__file__).resolve().parent / "data"
BATCH_DIR = DATA_DIR / "product_batches"
OUTPUT_DIR = DATA_DIR / "product_outputs"

BATCH_MAX = 2000
MARKET = "CA"


def load_db():
    db_path = DATA_DIR / "sourcing.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    return db


def get_asin_batches(db) -> list[list[str]]:
    """读取所有唯一 ASIN（不含已在产品库中的），分成 ≤2000 的批次"""
    # 先看哪些 ASIN 已有产品数据
    existing = set(a[0] for a in db.execute(
        "SELECT DISTINCT asin FROM sellersprite_product WHERE domain=?", (MARKET,)
    ).fetchall())

    all_asins = [a[0] for a in db.execute(
        "SELECT DISTINCT asin FROM final_sub_niche_asin ORDER BY asin"
    ).fetchall()]

    new_asins = [a for a in all_asins if a not in existing]
    print(f"总 ASIN: {len(all_asins)}, 已有产品数据: {len(existing)}, 需查询: {len(new_asins)}")

    batches = []
    for i in range(0, len(new_asins), BATCH_MAX):
        batches.append(new_asins[i:i + BATCH_MAX])
    return batches


def run_asin_batch(asins: list[str], batch_idx: int) -> Path | None:
    """调 asin_batch.py，返回下载的 xlsx 路径"""
    batch_file = BATCH_DIR / f"product_batch_{batch_idx:04d}.txt"
    batch_file.parent.mkdir(parents=True, exist_ok=True)
    batch_file.write_text("\n".join(asins), encoding="utf-8")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        SCRIPT_PYTHON, str(ASIN_SCRIPT),
        str(batch_file),
        "--market", MARKET,
        "--output", str(OUTPUT_DIR),
        "--prefix", f"PROD-{batch_idx:04d}",
    ]

    print(f"\n{'='*60}")
    print(f"产品批次 {batch_idx}: {len(asins)} ASINs")
    print(f"{'='*60}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=600,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT / "sellersprite_asin"),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        stdout = result.stdout
        stderr = result.stderr
        if stderr:
            print(f"  [stderr]: {stderr[:500]}")

        lines = stdout.splitlines()
        for line in lines[-20:]:
            print(line)

        if result.returncode != 0:
            print(f"  产品批次 {batch_idx} 失败 (exit {result.returncode})")
            return None

        for line in lines:
            if "导出完成:" in line:
                path_str = line.split("导出完成:", 1)[-1].strip()
                p = Path(path_str)
                if p.exists():
                    return p

        xlsx_files = sorted(OUTPUT_DIR.glob("*.xlsx"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
        if xlsx_files:
            return xlsx_files[0]

        print(f"  产品批次 {batch_idx}: 找不到输出文件")
        return None

    except subprocess.TimeoutExpired:
        print(f"  产品批次 {batch_idx} 超时")
        return None


def import_product_excel(xlsx_path: Path) -> int:
    """解析产品 Excel 并写入 sellersprite_product，返回导入数"""
    # 使用 app 模块的解析和导入逻辑
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app.sellersprite_import import parse_product_excel, import_product_to_db

    records = parse_product_excel(str(xlsx_path))
    batch_id = f"batch_{int(time.time())}"
    count = import_product_to_db(records, MARKET, batch_id)
    return count


def save_progress(batch_idx: int, total: int):
    """记录完成进度。只允许递增，防止并发 session 互相覆盖"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    progress_file = DATA_DIR / "product_query_progress.json"
    data = load_progress()
    if data and data.get("last_completed_batch", -1) >= batch_idx:
        return
    progress_file.write_text(json.dumps({
        "last_completed_batch": batch_idx,
        "total_batches": total,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }))


def load_progress():
    progress_file = DATA_DIR / "product_query_progress.json"
    if progress_file.exists():
        return json.loads(progress_file.read_text())
    return None


def _cleanup_chrome():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batches", type=int, default=0, help="本次只跑 N 批，完成后自动停（0=不限）")
    args = parser.parse_args()

    db = load_db()
    batches = get_asin_batches(db)

    if args.dry_run:
        for i, batch in enumerate(batches):
            print(f"  产品批次 {i}: {len(batch)} ASINs")
        print(f"  共 {len(batches)} 批次")
        db.close()
        return

    start_batch = 0
    if args.resume:
        prog = load_progress()
        if prog:
            start_batch = prog["last_completed_batch"] + 1
            print(f"续跑: 从批次 {start_batch} 开始")

    total_imported = 0
    failed_batches = []

    for i in range(start_batch, len(batches)):
        batch = batches[i]

        if args.batches and (i - start_batch) >= args.batches:
            print(f"本次已跑 {args.batches} 批，交棒给下个 session")
            break

        xlsx_path = run_asin_batch(batch, i)
        if not xlsx_path:
            print(f"!!! 产品批次 {i} 失败，跳过继续")
            failed_batches.append(i)
            save_progress(i, len(batches))
            time.sleep(5)
            continue

        count = import_product_excel(xlsx_path)
        total_imported += count
        print(f"  入库: {count} 产品")

        save_progress(i, len(batches))
        # 不杀 Chrome，让 persistent profile 保持登录态
        time.sleep(3)

    print(f"\n{'='*60}")
    print(f"Phase 3 完成! 导入产品: {total_imported}")
    if failed_batches:
        print(f"失败批次: {failed_batches}")

    product_count = db.execute(
        "SELECT COUNT(*) FROM sellersprite_product WHERE domain=?", (MARKET,)
    ).fetchone()[0]
    print(f"sellersprite_product 总计: {product_count} 行")
    db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback, time
        tb = traceback.format_exc()
        print(f"FATAL: {tb[:1000]}")
        err_log = Path(__file__).resolve().parent / "data" / "product_query_errors.log"
        with open(err_log, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"FATAL 崩溃 @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n")
            f.write(tb)
            f.write("\n")
        sys.exit(1)
