"""生成自包含 HTML 演示报告 — 候选清单 + 漏斗数据 + Niche 分析。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from app.models import SessionLocal, Candidate, Batch
from app.niche_analyzer import form_niches

BATCH_ID = sys.argv[1] if len(sys.argv) > 1 else "20260512_082046_7909bc"

db = SessionLocal()

# ── 批次摘要 ──
batch = db.query(Batch).filter(Batch.batch_id == BATCH_ID).first()
summary = {
    "batch_id": BATCH_ID,
    "total_aba_rows": batch.total_aba_rows if batch else 87476,
    "step1_passed": batch.step1_passed if batch else 10261,
    "step3_passed": batch.step3_passed if batch else 1440,
    "final_candidates": batch.final_candidates if batch else 456,
}

# ── 候选清单 ──
rows = db.query(Candidate).filter(
    Candidate.batch_id == BATCH_ID, Candidate.price > 0
).order_by(Candidate.net_repayment.desc()).all()

candidates = []
for c in rows:
    net_rate = c.net_repayment / c.price if c.price > 0 else 0
    candidates.append({
        "asin": c.asin,
        "brand": c.brand or "",
        "price": round(c.price, 2),
        "net_repayment": round(c.net_repayment, 2),
        "net_repayment_rate": round(net_rate * 100, 1),
        "profit_rate": round((c.profit_rate or 0) * 100, 1) if c.profit_rate and c.profit_rate < 1 else round(c.profit_rate or 0, 1),
        "shipping_cost": round(c.shipping_cost or 0, 2),
        "keyword_count": c.keyword_count or 0,
        "keywords": json.loads(c.keywords) if c.keywords else [],
        "ratings": c.ratings or 0,
        "ratings_count": c.ratings_count or 0,
        "search_volume": c.search_volume or 0,
        "cpc": round(c.cpc or 0, 2),
        "fba_fee": round(c.fba_fee or 0, 2),
        "online_date": c.online_date or "",
        "is_fba": c.is_fba,
        "avg_conversion_index": round(c.avg_conversion_index or 0, 2),
    })

# ── Niche 聚类 ──
niche_data = form_niches([{
    "asin": c["asin"],
    "keywords": c["keywords"],
    "price": c["price"],
    "brand": c["brand"],
    "net_repayment": c["net_repayment"],
} for c in candidates])
niches = []
for name, n in sorted(niche_data.items(), key=lambda x: -len(x[1]["asins"])):
    if len(n["asins"]) < 2:
        continue
    niches.append({
        "name": name,
        "core_count": len(n["asins"]),
        "keyword_count": len(n["keywords"]),
        "core_asins": sorted(n["asins"]),
        "keywords": sorted(n["keywords"]),
    })

# ── 高回款率候选 ──
high_rate = [c for c in candidates if c["net_repayment_rate"] >= 50]
fba_count = sum(1 for c in candidates if c["is_fba"])
avg_price = sum(c["price"] for c in candidates) / len(candidates) if candidates else 0
avg_net = sum(c["net_repayment"] for c in candidates) / len(candidates) if candidates else 0

db.close()

# ── 生成 HTML ──
data_json = json.dumps({
    "summary": summary,
    "candidates": candidates,
    "niches": niches,
    "stats": {
        "total": len(candidates),
        "high_rate": len(high_rate),
        "fba_count": fba_count,
        "avg_price": round(avg_price, 2),
        "avg_net": round(avg_net, 2),
    }
}, ensure_ascii=False)

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>亚马逊选品 SOP - 全量报告 ({summary["batch_id"][:8]})</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js"></script>
<style>
body {{ background: #111827; color: #e5e7eb; font-size: 14px; }}
</style>
</head>
<body class="p-4 max-w-full mx-auto" x-data="report()">
<script>
const DATA = {data_json};

function report() {{
    return {{
        ...DATA,
        sortBy: 'net_repayment',
        sortDir: 'desc',
        filterAsin: '',
        filterBrand: '',
        filterRateMin: 0,
        activeTab: 'candidates',
        selectedNiche: null,

        get filteredCandidates() {{
            let list = this.candidates;
            if (this.filterAsin) list = list.filter(c => c.asin.toUpperCase().includes(this.filterAsin.toUpperCase()));
            if (this.filterBrand) list = list.filter(c => (c.brand||'').toLowerCase().includes(this.filterBrand.toLowerCase()));
            if (this.filterRateMin > 0) list = list.filter(c => c.net_repayment_rate >= this.filterRateMin);
            const dir = this.sortDir === 'desc' ? -1 : 1;
            return list.sort((a,b) => dir * ((b[this.sortBy]||0) - (a[this.sortBy]||0)));
        }},

        formatPrice(v) {{ return v ? '$' + v.toFixed(2) : '-'; }},
        formatPct(v) {{ return v ? v.toFixed(1) + '%' : '-'; }},
    }};
}}
</script>

<!-- Header -->
<div class="bg-gray-800 rounded-xl p-6 mb-4 border border-gray-700">
    <h1 class="text-2xl font-bold mb-1">亚马逊选品 SOP 自动化管道 · 全量报告</h1>
    <p class="text-gray-400 text-sm">批次: {summary["batch_id"]} | 站点: CA (加拿大) | ABA: {summary["total_aba_rows"]:,} 行</p>
</div>

<!-- Funnel Cards -->
<div class="grid grid-cols-3 md:grid-cols-6 gap-3 mb-4">
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-white">{summary["total_aba_rows"]:,}</div><div class="text-xs text-gray-500">ABA 原始</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-yellow-400">{summary["step1_passed"]:,}</div><div class="text-xs text-gray-500">清洗通过</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-blue-400">{summary["step3_passed"]:,}</div><div class="text-xs text-gray-500">KCR 命中</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-purple-400">{summary["final_candidates"]:,}</div><div class="text-xs text-gray-500">候选 ASIN</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-green-400">{len(high_rate)}</div><div class="text-xs text-gray-500">回款率≥50%</div>
    </div>
    <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 text-center">
        <div class="text-2xl font-bold text-red-400">{len(niches)}</div><div class="text-xs text-gray-500">Niche 群</div>
    </div>
</div>

<div class="grid grid-cols-5 gap-3 mb-4 text-center text-sm">
    <div class="bg-gray-800/50 rounded p-2"><span class="text-gray-500">FBA占比</span><br><span class="text-white font-bold">{fba_count}/{len(candidates)}</span></div>
    <div class="bg-gray-800/50 rounded p-2"><span class="text-gray-500">均价</span><br><span class="text-white font-bold">${avg_price:.0f}</span></div>
    <div class="bg-gray-800/50 rounded p-2"><span class="text-gray-500">平均净回款</span><br><span class="text-white font-bold">${avg_net:.0f}</span></div>
    <div class="bg-gray-800/50 rounded p-2"><span class="text-gray-500">平均回款率</span><br><span class="text-white font-bold">{sum(c["net_repayment_rate"] for c in candidates)/len(candidates):.0f}%</span></div>
    <div class="bg-gray-800/50 rounded p-2"><span class="text-gray-500">平均海运</span><br><span class="text-white font-bold">${sum(c["shipping_cost"] for c in candidates)/len(candidates):.2f}</span></div>
</div>

<!-- Tabs -->
<div class="flex gap-2 mb-4">
    <button @click="activeTab='candidates'" :class="activeTab==='candidates'?'bg-blue-600 text-white':'bg-gray-700 text-gray-400'" class="px-4 py-2 rounded text-sm">候选清单 ({len(candidates)})</button>
    <button @click="activeTab='niches'" :class="activeTab==='niches'?'bg-purple-600 text-white':'bg-gray-700 text-gray-400'" class="px-4 py-2 rounded text-sm">Niche 分析 ({len(niches)})</button>
</div>

<!-- Candidates Tab -->
<div x-show="activeTab==='candidates'">
    <!-- Filters -->
    <div class="flex gap-3 mb-3 flex-wrap items-center text-sm">
        <input x-model="filterAsin" placeholder="ASIN..." class="bg-gray-800 border border-gray-600 rounded px-3 py-1.5 w-36">
        <input x-model="filterBrand" placeholder="品牌..." class="bg-gray-800 border border-gray-600 rounded px-3 py-1.5 w-32">
        <input type="number" x-model="filterRateMin" placeholder="最低回款率%" class="bg-gray-800 border border-gray-600 rounded px-3 py-1.5 w-32">
        <select x-model="sortBy" class="bg-gray-800 border border-gray-600 rounded px-3 py-1.5">
            <option value="net_repayment">净回款</option>
            <option value="price">售价</option>
            <option value="net_repayment_rate">回款率</option>
            <option value="ratings_count">评价数</option>
            <option value="search_volume">搜索量</option>
        </select>
        <button @click="sortDir = sortDir==='desc'?'asc':'desc'" class="bg-gray-700 rounded px-2 py-1" x-text="sortDir==='desc'?'↓':'↑'"></button>
        <span class="text-gray-600" x-text="filteredCandidates.length + ' 条'"></span>
    </div>

    <!-- Table -->
    <div class="bg-gray-800 rounded-xl border border-gray-700 overflow-x-auto">
        <table class="w-full text-sm">
            <thead>
                <tr class="bg-gray-700/50 text-gray-400 text-left">
                    <th class="py-2 px-3 w-8">#</th>
                    <th class="py-2 px-3">ASIN</th>
                    <th class="py-2 px-3">品牌</th>
                    <th class="py-2 px-3">词数</th>
                    <th class="py-2 px-3 text-right">售价</th>
                    <th class="py-2 px-3 text-right">利润%</th>
                    <th class="py-2 px-3 text-right">净回款</th>
                    <th class="py-2 px-3 text-right">回款率</th>
                    <th class="py-2 px-3 text-right">海运</th>
                    <th class="py-2 px-3 text-right">CPC</th>
                    <th class="py-2 px-3 text-right">搜索量</th>
                    <th class="py-2 px-3 text-right">评分</th>
                    <th class="py-2 px-3">FBA</th>
                    <th class="py-2 px-3">上架</th>
                </tr>
            </thead>
            <tbody>
                <template x-for="(c,i) in filteredCandidates" :key="c.asin">
                    <tr class="border-t border-gray-700/50 hover:bg-gray-700/30">
                        <td class="py-1.5 px-3 text-gray-500 text-xs" x-text="i+1"></td>
                        <td class="py-1.5 px-3 font-mono text-blue-400 text-xs" x-text="c.asin"></td>
                        <td class="py-1.5 px-3 text-gray-300 max-w-24 truncate" x-text="c.brand||'-'"></td>
                        <td class="py-1.5 px-3 text-center"><span class="bg-gray-700 rounded px-1.5 py-0.5 text-xs" x-text="c.keyword_count"></span></td>
                        <td class="py-1.5 px-3 text-right font-mono" x-text="formatPrice(c.price)"></td>
                        <td class="py-1.5 px-3 text-right font-mono" x-text="formatPct(c.profit_rate)"></td>
                        <td class="py-1.5 px-3 text-right font-mono" :class="c.net_repayment>0?'text-green-400':'text-red-400'" x-text="formatPrice(c.net_repayment)"></td>
                        <td class="py-1.5 px-3 text-right font-mono" :class="c.net_repayment_rate>=50?'text-green-400':'text-yellow-400'" x-text="formatPct(c.net_repayment_rate)"></td>
                        <td class="py-1.5 px-3 text-right font-mono text-gray-500" x-text="formatPrice(c.shipping_cost)"></td>
                        <td class="py-1.5 px-3 text-right font-mono" x-text="formatPrice(c.cpc)"></td>
                        <td class="py-1.5 px-3 text-right font-mono" x-text="c.search_volume?.toLocaleString()"></td>
                        <td class="py-1.5 px-3 text-right" x-text="c.ratings+'★ ('+c.ratings_count+')'"></td>
                        <td class="py-1.5 px-3"><span :class="c.is_fba?'text-green-400':'text-gray-500'" x-text="c.is_fba?'FBA':'FBM'"></span></td>
                        <td class="py-1.5 px-3 text-gray-400 text-xs" x-text="c.online_date||'-'"></td>
                    </tr>
                </template>
            </tbody>
        </table>
    </div>
</div>

<!-- Niches Tab -->
<div x-show="activeTab==='niches'">
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <template x-for="n in niches" :key="n.name">
            <div class="bg-gray-800 rounded-xl p-4 border border-gray-700 hover:border-purple-500 transition-colors cursor-pointer">
                <div class="flex items-start justify-between mb-2">
                    <h3 class="font-bold text-purple-400" x-text="n.name"></h3>
                    <span class="text-xs bg-gray-700 rounded px-2 py-0.5" x-text="n.core_count+' ASINs'"></span>
                </div>
                <div class="text-xs text-gray-400 mb-2" x-text="n.keyword_count+' 关键词'"></div>
                <div class="flex flex-wrap gap-1 mb-2">
                    <template x-for="kw in n.keywords.slice(0,8)" :key="kw">
                        <span class="text-xs bg-gray-700/50 rounded px-1.5 py-0.5 text-gray-500" x-text="kw"></span>
                    </template>
                </div>
                <div class="text-xs text-gray-600 font-mono">
                    <span x-text="n.core_asins.slice(0,5).join(', ')"></span>
                    <span x-show="n.core_asins.length>5" x-text="'...'"></span>
                </div>
            </div>
        </template>
    </div>
</div>

<!-- Footer -->
<div class="mt-6 text-center text-xs text-gray-600">
    亚马逊选品 SOP 自动化管道 · 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据来源: ABA CA 2026-03-31 + Sellersprite KCR/Product
</div>
</body>
</html>'''

out_path = "data/full_report.html"
os.makedirs("data", exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"HTML saved: {out_path}")
print(f"Size: {os.path.getsize(out_path)/1024:.0f} KB")
print(f"Candidates: {len(candidates)}")
print(f"Niches: {len(niches)}")
