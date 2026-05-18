"""Test LLM split on expanded MVP niche."""
import json, asyncio, aiohttp, sys
sys.path.insert(0, '.')
from app.models import SessionLocal
from sqlalchemy import text

with open('data/mvp_expanded_niche.json', 'r', encoding='utf-8') as f:
    mvp = json.load(f)

ASIN_TITLES = {}
for kw, prods in mvp['kw_to_products'].items():
    for p in prods:
        if p['asin'] not in ASIN_TITLES:
            ASIN_TITLES[p['asin']] = p['title']

products_text = []
for asin in mvp['asins'][:25]:
    title = ASIN_TITLES.get(asin, asin)
    products_text.append(asin + ': ' + title[:200])

keywords_text = ', '.join(mvp['keywords'])

API_KEY = 'sk-1fce6b2a9f7844d1938aa3ed512dbcde'
MODEL = 'deepseek-chat'

SYSTEM_PROMPT = """你是亚马逊产品分类专家。给你一个niche的ASIN产品标题和关键词列表，将其拆分成更精确的细分赛道。

分两步：
第一步：根据ASIN标题将产品分组。功能相同、可互相替代的产品归为一组，每组至少2个ASIN。注意区分功能结构不同的产品（如腕式血压计和上臂式血压计分开），但仅尺寸/颜色/pack数不同的产品合并。无法归组的ASIN忽略。

第二步：将关键词分配到各ASIN组。关键词有四种：
1. 人群倾向词（含性别/年龄/人群限定词）→ 根据目标用户分配
2. 功能属性词（含功能/结构/材质差异词）→ 归入对应ASIN组
3. 尺寸差异词（含尺寸/pack数/容量词）→ 不创建新组
4. 中性通用词（无人群/功能/尺寸限定的通用产品名）→ 如果niche被切成多个子赛道，必须同时放入每一个子赛道

输出纯JSON：
{"sub_niches":[{"name":"2-6字中文赛道名","asins":["B0xxx"],"keywords":["kw1"]}]}"""

USER_PROMPT = "ASIN产品标题:\n" + '\n'.join(products_text) + "\n\n关键词列表: " + keywords_text + "\n\n请拆分成细分赛道:"

async def run():
    payload = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': USER_PROMPT},
        ],
        'response_format': {'type': 'json_object'},
        'max_tokens': 1000,
        'temperature': 0,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            'https://api.deepseek.com/v1/chat/completions',
            json=payload,
            headers={'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'},
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            data = await resp.json()
            content = data['choices'][0]['message']['content']
            result = json.loads(content)

            print('Sub-niches: ' + str(len(result.get('sub_niches', []))))
            for sn in result.get('sub_niches', []):
                kws = sn.get('keywords', [])
                asins = sn.get('asins', [])
                print('  [' + sn['name'] + '] ' + str(len(asins)) + ' ASIN, ' + str(len(kws)) + ' kw')
                print('    ASINs: ' + ', '.join(asins[:5]))
                print('    Keywords: ' + ', '.join(kws[:8]))
                if len(kws) > 8:
                    print('      ... +' + str(len(kws)-8) + ' more')
                print()

asyncio.run(run())
