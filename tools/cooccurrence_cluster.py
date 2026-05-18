"""
FCA-inspired: 词根共现图 → 社区检测 → 自动发现属性维度
纯 Python，无第三方依赖
"""
import sqlite3, json, re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

DB_PATH = "d:/claude code/sourcing-tool/data/sourcing.db"
OUT_DIR = Path("d:/claude code/tools/output")

STOP_WORDS = {
    'a','an','the','is','are','was','were','be','been','being',
    'have','has','had','having','do','does','did','doing',
    'will','would','shall','should','can','could','may','might','must',
    'i','me','my','we','our','us','you','your','he','she','it','its',
    'they','them','their','this','that','these','those',
    'in','on','at','to','for','of','with','by','from',
    'and','or','but','not','no','nor','if','so','as','than',
    'also','too','very','just','about','up','out','down','off','over','under',
    'again','all','each','every','both','few','more','most',
    'other','some','such','only','own','same','new','now',
    'then','here','there','when','where','why','how','which','who','what','whom',
    'one','two','three','first','last','get','got','go','going',
    'into','onto','after','before','during','without','within',
    'per','like','much','many','any','been','still','well',
    'back','also','even','already','yet',
    'custom','customized','personalized','personalised',
}
MIN_ROOT_COUNT = 2

# ── Step 1: Load & extract ──
def load_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT keyword, search_volume, cpc, search_conversion_rate_d90,
               click_of_90d, sales_volume_of_90d, product_count,
               share_click_rate, share_conversion_rate
        FROM keyword_extends WHERE keyword != ''
    """)
    rows = cur.fetchall()
    conn.close()

    keywords = []  # [(kw, sv, cpc, ...)]
    root_docs = defaultdict(list)  # root -> [keyword_indices]
    kw_roots = []  # [{roots} for each keyword]

    for idx, row in enumerate(rows):
        kw = row[0].lower()
        keywords.append(row)
        words = set()
        for w in kw.split():
            clean = re.sub(r'[^a-z]', '', w)
            if len(clean) >= 2 and clean not in STOP_WORDS:
                words.add(clean)
                root_docs[clean].append(idx)
        kw_roots.append(words)

    # Filter rare roots
    valid_roots = {r for r, idxs in root_docs.items() if len(idxs) >= MIN_ROOT_COUNT}

    # Filter keywords to only keep valid roots
    for i, roots in enumerate(kw_roots):
        kw_roots[i] = roots & valid_roots

    print(f"  Keywords: {len(keywords)}")
    print(f"  Valid roots: {len(valid_roots)}")
    return keywords, kw_roots, list(valid_roots), root_docs

# ── Step 2: Build co-occurrence graph ──
def build_graph(kw_roots, valid_roots, root_docs):
    """构建词根共现图，边权重 = 两个词根共同出现的关键词数"""
    root_to_idx = {r: i for i, r in enumerate(valid_roots)}
    n = len(valid_roots)
    edges = defaultdict(float)

    for roots in kw_roots:
        if len(roots) < 2:
            continue
        for a, b in combinations(sorted(roots), 2):
            if a in root_to_idx and b in root_to_idx:
                i, j = root_to_idx[a], root_to_idx[b]
                key = (min(i, j), max(i, j))
                edges[key] += 1

    # Normalize: Jaccard similarity
    doc_counts = {r: len(root_docs[r]) for r in valid_roots}
    for (i, j), cooc in edges.items():
        ra, rb = valid_roots[i], valid_roots[j]
        denom = doc_counts[ra] + doc_counts[rb] - cooc
        edges[(i, j)] = cooc / denom if denom > 0 else 0

    print(f"  Edges: {len(edges)} (Jaccard > 0: {sum(1 for v in edges.values() if v > 0)})")
    return edges, n, root_to_idx

# ── Step 3: Greedy community detection (simplified Louvain) ──
def detect_communities(edges, n, min_similarity=0.1):
    """贪心社区发现：合并最相似的邻居对，直到无边可合"""
    # Build adjacency list
    adj = defaultdict(dict)
    for (i, j), w in edges.items():
        if w >= min_similarity:
            adj[i][j] = w
            adj[j][i] = w

    # Start: each node in own community
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
            return True
        return False

    # Sort edges by weight descending
    sorted_edges = sorted(edges.items(), key=lambda x: -x[1])

    for (i, j), w in sorted_edges:
        if w < min_similarity:
            break
        union(i, j)

    # Collect communities
    comms = defaultdict(list)
    for i in range(n):
        comms[find(i)].append(i)

    # Filter: min 2 roots per community
    communities = {}
    for leader, members in comms.items():
        if len(members) >= 2:
            communities[leader] = members

    return communities

# ── Step 4: Aggregate metrics per community ──
def aggregate_communities(communities, valid_roots, kw_roots, keywords, root_docs):
    """对每个社区，找出含该社区词根的关键词子集，汇总指标"""
    results = {}
    root_to_idx = {r: i for i, r in enumerate(valid_roots)}

    for comm_id, member_idxs in communities.items():
        comm_roots = [valid_roots[i] for i in member_idxs]
        comm_root_set = set(comm_roots)

        # Find keywords that contain AT LEAST ONE root from this community
        # (for attribute-like communities, containment of 1 root is meaningful)
        matched_kw_indices = set()
        for root in comm_roots:
            matched_kw_indices.update(root_docs.get(root, []))

        if len(matched_kw_indices) < 5:
            continue

        # Aggregate
        total_sv = 0
        total_click = 0
        total_sales = 0
        cpcs = []
        convs = []
        shares = []
        prod_counts = []

        for idx in matched_kw_indices:
            row = keywords[idx]
            total_sv += row[1] or 0
            total_click += row[4] or 0
            total_sales += row[5] or 0
            if row[2] and row[2] > 0:
                cpcs.append(row[2])
            if row[3] and row[3] > 0:
                convs.append(row[3])
            if row[7] and row[7] > 0:
                shares.append(row[7])
            if row[6] and row[6] > 0:
                prod_counts.append(row[6])

        n_kw = len(matched_kw_indices)

        results[comm_id] = {
            "roots": comm_roots,
            "n_roots": len(comm_roots),
            "n_keywords": n_kw,
            "total_search_volume": total_sv,
            "total_click_90d": total_click,
            "total_sales_90d": total_sales,
            "avg_cpc": round(sum(cpcs) / len(cpcs), 2) if cpcs else 0,
            "avg_conv_rate": round(sum(convs) / len(convs), 4) if convs else 0,
            "avg_share_click": round(sum(shares) / len(shares), 2) if shares else 0,
            "avg_product_count": round(sum(prod_counts) / len(prod_counts)) if prod_counts else 0,
            # 需求强度
            "demand_intensity": round(total_sv / max(sum(prod_counts) / max(len(prod_counts), 1), 1), 1),
            # Top 5 roots in community (by doc freq)
            "top_roots": sorted(comm_roots, key=lambda r: len(root_docs.get(r, [])), reverse=True)[:8],
        }

    return results

# ── Main ──
def main():
    print("=== Step 1: Load & extract roots ===")
    keywords, kw_roots, valid_roots, root_docs = load_data()

    print("\n=== Step 2: Build co-occurrence graph ===")
    edges, n, root_to_idx = build_graph(kw_roots, valid_roots, root_docs)

    print("\n=== Step 3: Community detection ===")
    communities = detect_communities(edges, n, min_similarity=0.12)

    print(f"  Communities found: {len(communities)}")

    # Print root-level summary of each community
    root_idx_to_root = {v: k for k, v in root_to_idx.items()}
    for comm_id, members in sorted(communities.items(), key=lambda x: -len(x[1])):
        roots_in_comm = [valid_roots[i] for i in members]
        print(f"  Comm-{comm_id} ({len(members)} roots): {', '.join(roots_in_comm[:15])}")

    print("\n=== Step 4: Aggregate metrics ===")
    results = aggregate_communities(communities, valid_roots, kw_roots, keywords, root_docs)

    # Sort by search volume
    sorted_results = sorted(results.items(), key=lambda x: -x[1]["total_search_volume"])

    print(f"\n=== Results (top 15 communities by search volume) ===\n")
    print(f"{'#':>3} {'Roots':<40} {'Kws':>5} {'SearchVol':>10} {'Demand':>8}")
    print("-" * 72)
    for rank, (cid, r) in enumerate(sorted_results[:15], 1):
        roots_str = "+".join(r["top_roots"][:5])
        print(f"{rank:>3} {roots_str:<40} {r['n_keywords']:>5} {r['total_search_volume']:>10,} {r['demand_intensity']:>8.1f}")

    # Output full JSON
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "n_keywords": len(keywords),
        "n_roots": len(valid_roots),
        "n_communities": len(communities),
        "communities": {
            f"comm_{cid}": {
                "roots": r["roots"],
                "top_roots": r["top_roots"],
                "n_keywords": r["n_keywords"],
                "total_search_volume": r["total_search_volume"],
                "total_sales_90d": r["total_sales_90d"],
                "avg_cpc": r["avg_cpc"],
                "avg_conv_rate": r["avg_conv_rate"],
                "demand_intensity": r["demand_intensity"],
            }
            for cid, r in sorted_results
        }
    }
    outpath = OUT_DIR / "cooccurrence_communities.json"
    outpath.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull data: {outpath}")

if __name__ == "__main__":
    main()
