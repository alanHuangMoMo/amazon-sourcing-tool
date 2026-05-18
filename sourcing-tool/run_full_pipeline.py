"""全量 ABA → 管线 → HTML 报告。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from app.pipeline import run_auto_pipeline, PipelineProgress

class PrintProgress(PipelineProgress):
    def update(self, step, pct, message):
        super().update(step, pct, message)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{pct}%] {step}: {message}")

if __name__ == "__main__":
    aba_file = sys.argv[1] if len(sys.argv) > 1 else "../CA_热门搜索词_简单_Month_2026_03_31.csv"
    print(f"=== 全量管线启动 ===")
    print(f"ABA: {aba_file}")
    print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    progress = PrintProgress()
    try:
        result = run_auto_pipeline(
            aba_filepath=aba_file,
            domain_str="CA",
            config={
                "conv_index_min": 1.0,
                "share_max": 50.0,
                "data_expiry_days": 30,
                "ship_cbm_rate": 120,
                "ship_handling": 0.8,
            },
            progress=progress,
        )
        print(f"\n=== 管线完成 ===")
        print(f"Batch ID: {result['batch_id']}")
        print(f"候选数: {result['candidate_count']}")
        print(f"Summary: {json.dumps(result['summary'], ensure_ascii=False)}")

        # Save batch_id for HTML generation
        with open("data/last_batch.txt", "w") as f:
            f.write(result['batch_id'])

    except Exception as e:
        print(f"\n=== 管线失败 ===")
        print(f"错误: {e}")
        sys.exit(1)
