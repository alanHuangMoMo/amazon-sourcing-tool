"""MVP: expand one niche via Sorftime APIs, then run LLM split."""
import json, sys, time, re
sys.path.insert(0, '.')
from app.models import SessionLocal
from sqlalchemy import text
import subprocess

db = SessionLocal()

# ── Pick MVP niche ──
NICHE_ID = 2094  # blood pressure monitor
kws = [r[0] for r in db.execute(text(
    'SELECT keyword FROM niche_kw WHERE niche_id=:nid'), {'nid': NICHE_ID}).fetchall()]
asins = [r[0] for r in db.execute(text(
    'SELECT asin FROM niche_asin WHERE niche_id=:nid'), {'nid': NICHE_ID}).fetchall()]

print(f'Niche: blood pressure monitor')
print(f'  Keywords ({len(kws)}): {", ".join(kws[:10])}')
print(f'  ASINs ({len(asins)}): {", ".join(asins[:5])}...')

# ── API caller ──
def call_api(endpoint, params, domain=6):
    params_json = json.dumps(params)
    cmd = f"sorftime api {endpoint} '{params_json}' --domain {domain}"
    result = subprocess.run(['bash', '-c', cmd], capture_output=True,
                            text=True, timeout=120, encoding='utf-8', errors='replace')
    out = result.stdout
    start = out.find('{')
    if start < 0:
        return None
    return json.loads(out[start:])

# ── Step 1: Expand ASINs via KeywordSearchResults (keyword -> products) ──
# Pick 3 diverse keywords
seed_kws = [k for k in kws if k in [
    'blood pressure monitor', 'blood pressure machine for home use',
    'machine a pression', 'blood pressure monitor for home use',
    'tensiometre'
]][:3]

if not seed_kws:
    seed_kws = kws[:3]

print(f'\n--- Step 1: KeywordSearchResults for {len(seed_kws)} keywords ---')
all_products = {}  # keyword -> [products]
request_cost = 0

for kw in seed_kws:
    data = call_api('KeywordSearchResults', {'keyword': kw, 'pageIndex': 1, 'pageSize': 10})
    request_cost += 5
    if data and data.get('Code') == 0:
        products = data.get('Data', {}).get('Products', [])
        all_products[kw] = products
        new_asins = [p['Asin'] for p in products if 'Asin' in p]
        print(f'  [{kw}]: {len(products)} products, e.g. {new_asins[:3]}')
        time.sleep(1)
    else:
        code = data.get('Code') if data else 'N/A'
        print(f'  [{kw}]: FAILED (Code={code})')

# Collect new ASINs
new_asins = set()
for kw, prods in all_products.items():
    for p in prods:
        if 'Asin' in p:
            new_asins.add(p['Asin'])
print(f'  New ASINs from keywords: {len(new_asins)}')

# ── Step 2: Expand keywords via ASINRequestKeywordv2 (ASIN -> keywords) ──
# Pick 3 diverse ASINs from the niche
seed_asins = asins[:3]

print(f'\n--- Step 2: ASINRequestKeywordv2 for {len(seed_asins)} ASINs ---')
all_traffic_kw = {}  # asin -> [keyword entries]
for asin in seed_asins:
    data = call_api('ASINRequestKeywordv2', {'asin': asin, 'pageIndex': 1, 'pageSize': 30})
    request_cost += 1
    if data and data.get('Code') == 0:
        entries = data.get('Data', [])
        all_traffic_kw[asin] = entries
        kws_found = [e['Keyword']['Keyword'] for e in entries if 'Keyword' in e]
        shares = [e.get('ShowShare', 0) for e in entries if 'Keyword' in e]
        print(f'  [{asin}]: {len(entries)} keywords, shares={shares[:3]}')
        time.sleep(0.5)
    else:
        code = data.get('Code') if data else 'N/A'
        print(f'  [{asin}]: FAILED (Code={code})')

# Collect new keywords
new_kws = set()
for asin, entries in all_traffic_kw.items():
    for e in entries:
        if 'Keyword' in e:
            new_kws.add(e['Keyword']['Keyword'])
print(f'  New keywords from ASINs: {len(new_kws)}')

# ── Step 3: Merge ──
all_kw = set(kws) | new_kws
all_asin = set(asins) | new_asins
print(f'\n--- Result ---')
print(f'  Original: {len(kws)} kw, {len(asins)} asin')
print(f'  Expanded: {len(all_kw)} kw (+{len(all_kw)-len(kws)}), {len(all_asin)} asin (+{len(all_asin)-len(asins)})')
print(f'  Request cost: {request_cost}')
print(f'  Remaining: {322 - request_cost}')

# ── Save for LLM ──
output = {
    'niche_id': NICHE_ID,
    'niche_name': 'blood pressure monitor',
    'original_kw_count': len(kws),
    'original_asin_count': len(asins),
    'expanded_kw_count': len(all_kw),
    'expanded_asin_count': len(all_asin),
    'keywords': sorted(all_kw),
    'asins': sorted(all_asin),
    'kw_to_products': {kw: [{'asin': p['Asin'], 'title': p.get('Title',''), 'price': p.get('Price'), 'ratings': p.get('Ratings'), 'sales': p.get('ListingSalesVolumeOfMonth')} for p in prods] for kw, prods in all_products.items()},
    'asin_to_keywords': {a: [{'keyword': e['Keyword']['Keyword'], 'show_share': e.get('ShowShare'), 'show_type': e.get('ShowType')} for e in entries] for a, entries in all_traffic_kw.items()},
}

with open('data/mvp_expanded_niche.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f'\nSaved to data/mvp_expanded_niche.json')
db.close()
