"""Niche expansion pipeline v2."""
import sys, json, re, time
sys.path.insert(0, '.')
from app.models import SessionLocal
from sqlalchemy import text
import subprocess

db = SessionLocal()

def call_api(endpoint, params, domain=6):
    params_json = json.dumps(params)
    cmd = f"sorftime api {endpoint} '{params_json}' --domain {domain}"
    result = subprocess.run(['bash', '-c', cmd], capture_output=True,
                            text=True, timeout=120, encoding='utf-8', errors='replace')
    # Parse from raw bytes to handle encoding
    raw = result.stdout
    start = raw.find('{')
    if start < 0:
        return None
    return json.loads(raw[start:])

def count_sp_page1(raw_text):
    """Count SearchPosition page 1 from raw text."""
    matches = re.findall(r'"SearchPosition": "([^"]+)"', raw_text)
    # Read raw bytes for accurate matching
    raw_bytes = raw_text.encode('utf-8', errors='surrogateescape')
    byte_matches = re.findall(b'"SearchPosition": "([^"]*)"', raw_bytes)
    page1 = 0
    for m in byte_matches:
        try:
            s = m.decode('utf-8')
            if s.startswith('第1页'):
                page1 += 1
        except:
            pass
    return page1

def get_raw_api(endpoint, params, domain=6):
    """Return raw text from API call."""
    params_json = json.dumps(params)
    cmd = f"sorftime api {endpoint} '{params_json}' --domain {domain}"
    result = subprocess.run(['bash', '-c', cmd], capture_output=True,
                            text=True, timeout=120, encoding='utf-8', errors='replace')
    return result.stdout

# ── Pick MVP niche ──
NICHE_ID = 2094  # blood pressure monitor
niche_row = db.execute(text(
    'SELECT niche_name, keyword_count, asin_count FROM niche WHERE id=:nid'
), {'nid': NICHE_ID}).fetchone()
print(f"Niche: {niche_row[0]} ({niche_row[1]} kw, {niche_row[2]} asin)")

kws_raw = db.execute(text(
    'SELECT keyword FROM niche_kw WHERE niche_id=:nid'), {'nid': NICHE_ID}).fetchall()
kws = [r[0] for r in kws_raw]
asins_raw = db.execute(text(
    'SELECT asin FROM niche_asin WHERE niche_id=:nid'), {'nid': NICHE_ID}).fetchall()
asins = [r[0] for r in asins_raw]

# ── Step 1: Clean brand keywords ──
print(f"\n=== Step 1: Brand cleaning ===")
kws_placeholders = ','.join([f':kw{i}' for i in range(len(kws))])
kw_params = {f'kw{i}': k for i, k in enumerate(kws)}
brand_rows = db.execute(text(f"""
    SELECT keyword, brand_1, brand_2, brand_3, search_rank
    FROM aba_report WHERE domain='CA' AND keyword IN ({kws_placeholders})
"""), kw_params).fetchall()

kw_brands = {}  # ba keyword -> set of brands
kw_rank = {}    # keyword -> ABA rank
for r in brand_rows:
    brands = set()
    for b in [r[1], r[2], r[3]]:
        if b and b.strip():
            brands.add(b.strip().lower())
    kw_brands[r[0]] = brands
    kw_rank[r[0]] = r[4] if r[4] else 99999

# Remove keywords that ARE brand names
cleaned_kws = []
removed = []
for kw in kws:
    kw_lower = kw.strip().lower()
    is_brand = False
    for brands in kw_brands.values():
        if kw_lower in brands:
            is_brand = True
            break
    if is_brand:
        removed.append(kw)
    else:
        cleaned_kws.append(kw)

print(f"  Removed {len(removed)} brand keywords: {removed[:5]}")
print(f"  Remaining: {len(cleaned_kws)} keywords")

# ── Step 2: Top 3 ASINs by keyword association ──
print(f"\n=== Step 2: Top 3 ASINs ===")
asin_kw_count = {}
for asin in asins:
    count = db.execute(text("""
        SELECT COUNT(*) FROM niche_kw nk
        JOIN aba_report aba ON aba.keyword = nk.keyword AND aba.domain='CA'
        WHERE nk.niche_id=:nid
        AND (aba.asin_1=:a OR aba.asin_2=:a OR aba.asin_3=:a)
    """), {'nid': NICHE_ID, 'a': asin}).scalar()
    asin_kw_count[asin] = count

top3_asins = sorted(asin_kw_count.items(), key=lambda x: -x[1])[:3]
print(f"  Top 3 ASINs by keyword association:")
for a, c in top3_asins:
    print(f"    {a}: {c} keywords")

# ── Step 3: ASIN -> keywords (page 1 only) ──
print(f"\n=== Step 3: ASINRequestKeywordv2 ===")
all_new_kws = {}

def parse_page1_kws(raw_text):
    """Extract page-1 natural keywords with ShowShare from raw API text."""
    raw_bytes = raw_text.encode('utf-8', errors='surrogateescape')
    results = []

    # Find each keyword entry: from '"ShowType"' to the closing '}' of Keyword object
    # Pattern: "ShowType": "...", ... "Keyword": { ... }
    # Simpler: find SearchPosition values and associate with ShowShare + keyword
    # Use byte-level parsing to avoid encoding issues

    # Split by keyword boundaries: find all ShowType entries
    pos = 0
    while True:
        # Find next SearchPosition
        sp_start = raw_bytes.find(b'"SearchPosition"', pos)
        if sp_start < 0:
            break

        # Find the value
        val_start = raw_bytes.find(b'"', sp_start + 16) + 1
        val_end = raw_bytes.find(b'"', val_start)
        sp_val = raw_bytes[val_start:val_end]

        # Check if page 1
        try:
            sp_str = sp_val.decode('utf-8')
            is_page1 = sp_str.startswith('第1页')
        except:
            is_page1 = b'1' in sp_val[:4]  # fallback: byte-level check

        # Find associated ShowShare (look backwards from sp_start, find nearest ShowShare)
        ss_start = raw_bytes.rfind(b'"ShowShare"', 0, sp_start)
        ss_val = None
        if ss_start > 0:
            ss_colon = raw_bytes.find(b':', ss_start) + 1
            ss_end = raw_bytes.find(b',', ss_colon)
            if ss_end < 0:
                ss_end = raw_bytes.find(b'}', ss_colon)
            if ss_end > ss_colon:
                try:
                    ss_val = float(raw_bytes[ss_colon:ss_end].strip())
                except:
                    pass

        # Find associated keyword
        kw_start = raw_bytes.rfind(b'"Keyword":', 0, sp_start)
        kw_val = None
        if kw_start > 0:
            inner_start = raw_bytes.find(b'"Keyword": "', kw_start) + len(b'"Keyword": "')
            inner_end = raw_bytes.find(b'"', inner_start)
            if inner_end > inner_start:
                try:
                    kw_val = raw_bytes[inner_start:inner_end].decode('utf-8')
                except:
                    pass

        results.append({'pos': sp_str if is_page1 else 'other', 'share': ss_val, 'kw': kw_val,
                        'sp_raw': sp_val[:40], 'page1': is_page1})

        pos = val_end + 1

    return results

for asin, _ in top3_asins:
    raw = get_raw_api('ASINRequestKeywordv2', {'asin': asin, 'pageIndex': 1, 'pageSize': 50})
    time.sleep(0.5)

    results = parse_page1_kws(raw)
    page1_results = [(r['kw'], r['share']) for r in results if r['page1'] and r['kw'] and r['share']]
    page1_results.sort(key=lambda x: -x[1])

    print(f"  [{asin}]: {len(results)} total, {len(page1_results)} page-1 natural (top: {page1_results[0][0][:30] if page1_results else 'N/A'} {page1_results[0][1]:.1f}% )" if page1_results else f"  [{asin}]: {len(results)} total, 0 page-1")

    for kw, share in page1_results[:20]:
        if kw not in all_new_kws or share > all_new_kws[kw]:
            all_new_kws[kw] = share

    time.sleep(0.5)

new_kw_count = len(all_new_kws)
print(f"  Total unique new keywords from ASINs: {new_kw_count}")

# ── Step 4: Keywords -> products ──
print(f"\n=== Step 4: KeywordSearchResults ===")
# Top 3 keywords by ABA rank (ascending)
top3_kws = sorted(kw_rank.items(), key=lambda x: x[1])[:3]
top3_kws = [(k, r) for k, r in top3_kws if k in cleaned_kws]
if not top3_kws:
    top3_kws = [(cleaned_kws[0], 1)]

print(f"  Top 3 keywords by ABA rank:")
for kw, rank in top3_kws:
    print(f"    [{rank}] {kw}")

new_asins = set()
for kw, rank in top3_kws:
    data = call_api('KeywordSearchResults', {'keyword': kw, 'pageIndex': 1, 'pageSize': 40})
    time.sleep(1)
    if data and data.get('Code') == 0:
        products = data.get('Data', {}).get('Products', [])
        page_count = data.get('Data', {}).get('PageCount', 0)
        for p in products:
            if 'Asin' in p:
                new_asins.add(p['Asin'])
        print(f"  [{kw}]: {len(products)} products, {len(new_asins)} total unique ASINs so far, {page_count} pages available")

# ── Step 5: Merge ──
all_kw_set = set(cleaned_kws) | set(all_new_kws.keys())
all_asin_set = set(asins) | new_asins
print(f"\n=== Result ===")
print(f"  Original: {len(kws)} kw, {len(asins)} asin")
print(f"  After brand clean: {len(cleaned_kws)} kw")
print(f"  After expansion: {len(all_kw_set)} kw (+{len(all_kw_set)-len(cleaned_kws)}), {len(all_asin_set)} asin (+{len(all_asin_set)-len(asins)})")

# Save expanded data
output = {
    'niche_id': NICHE_ID,
    'niche_name': niche_row[0],
    'brand_removed': removed,
    'cleaned_kws': sorted(cleaned_kws),
    'expanded_kws': sorted(all_kw_set),
    'expanded_asins': sorted(all_asin_set),
    'new_kws_from_asins': {k: v for k, v in sorted(all_new_kws.items(), key=lambda x: -x[1])},
}

with open('data/mvp_v2_expanded.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\nSaved to data/mvp_v2_expanded.json")
db.close()
