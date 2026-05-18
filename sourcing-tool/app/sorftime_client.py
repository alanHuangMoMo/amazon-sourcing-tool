"""Sorftime CLI 封装 — 支持 mock 模式开发，避免消耗真实 Request。"""
import subprocess
import json
import time
import os
import random
from typing import Optional


def _mock_keyword_response(keyword: str) -> dict:
    """根据真实 API 返回结构生成 mock 数据（全字段）。"""
    sv = random.randint(5000, 500000)
    cpc_val = random.randint(30, 250)
    cpc_min = random.randint(20, cpc_val)
    cpc_max = random.randint(cpc_val, 300)
    return {
        "Code": 0,
        "Message": None,
        "Data": {
            "Keyword": keyword,
            "KeywordCNName": keyword,
            "Rank": random.randint(20, 500),
            "SearchVolume": sv,
            "Cpc": cpc_val,
            "CpcRange": [cpc_min, cpc_max],
            "SearchConversionRate": round(random.uniform(0.5, 8.0), 2),
            "SearchConversionRateD90": round(random.uniform(0.5, 5.0), 2),
            "ClickConversionRateD90": round(random.uniform(0.3, 4.0), 2),
            "SalesVolumeOf90D": random.randint(100, 50000),
            "ClickOf90D": 0,
            "WordCount": len(keyword.split()),
            "ProductCount": random.randint(1000, 200000),
            "RankChangeOfWeekly": random.randint(-50, 50),
            "Top3asin": [
                f"B0{random.randint(10000000,99999999)},{random.uniform(3,20):.2f}%,{random.uniform(1,8):.2f}%",
                f"B0{random.randint(10000000,99999999)},{random.uniform(2,15):.2f}%,{random.uniform(0.5,5):.2f}%",
                f"B0{random.randint(10000000,99999999)},{random.uniform(1,10):.2f}%,{random.uniform(0.3,3):.2f}%",
            ],
            "Top3Brand": [
                random.choice(["BrandA", "BrandB", "BrandC", "GenericBrand"]),
                random.choice(["BrandD", "BrandE", "BrandF", "GenericBrand"]),
                random.choice(["BrandG", "BrandH", "BrandI", "GenericBrand"]),
            ],
            "Top3Category": [
                random.choice(["Sports", "Home", "Kitchen"]),
                random.choice(["Office", "Garden", "Tools"]),
                random.choice(["Toys", "Books", "Electronics"]),
            ],
            "ShareClickRate": round(random.uniform(10, 60), 2),
            "ShareConversionRate": round(random.uniform(3, 20), 2),
            "Season": f"{random.randint(1,12)}月",
            "Update": "20260501",
            "Department": None,
            "Images": [f"https://m.media-amazon.com/images/I/{random.randint(10,99)}abcdefg._AC_UL320_.jpg" for _ in range(random.randint(1, 5))],
            "ImagesFromAsin": [f"B0{random.randint(10000000,99999999)}" for _ in range(random.randint(5, 10))],
            "CpcTrend": [],
            "SearchVolumeTrend": [],
            "SearchVolumeGrowthTrend": [],
            "SearchVolumeGrowthRateTrend": [round(random.uniform(-20, 30), 2) for _ in range(3)],
            "SearchResultOfFP": [],
            "AssociatedWithCategory": [str(random.randint(1000000, 9999999)) for _ in range(random.randint(5, 20))],
            "AssociatedWithCategoryDetail": [],
        },
        "RequestLeft": 9999,
        "RequestConsumed": 1,
    }


def _mock_product_response(asin: str) -> dict:
    """根据真实 API 返回结构生成 mock 产品数据（全字段）。"""
    brands = ["Amazon Basics", "GenericCo", "BrandX", "BrandY", "TopSeller"]
    price = random.randint(999, 4999)  # cents
    profit_rate = round(random.uniform(15, 55), 2)
    fba_fee = random.randint(300, 800)
    platform_fee = int(price * 0.15)
    ratings_count = random.randint(10, 50000)
    return {
        "Code": 0,
        "Data": {
            "Asin": asin,
            "ParentAsin": f"B0{random.randint(10000000,99999999)}",
            "Title": f"Product {asin} - Premium Edition",
            "Description": f"High quality product {asin} with excellent features.",
            "Brand": random.choice(brands),
            "ProductType": random.choice(["KITCHEN", "SPORTS", "OFFICE", "HOME"]),
            "StoreName": random.choice(brands),
            "Price": price,
            "ListPrice": int(price * 1.2),
            "SalesPrice": price,
            "Coupon": random.choice([0, 0, 0, random.randint(50, 200)]),
            "FbaFee": fba_fee,
            "PlatformFee": platform_fee,
            "Profit": int(price * profit_rate / 100),
            "ProfitRate": profit_rate,
            "ShipCost": 0,
            "IsFBA": random.choice([True, False]),
            "ShipsFrom": random.choice(["Amazon", "ThirdParty"]),
            "BuyboxSeller": random.choice(["Amazon", "ThirdParty"]),
            "BuyboxSellerId": None,
            "BuyboxSellerAddress": None,
            "RatingsCount": ratings_count,
            "Ratings": round(random.uniform(3.5, 4.8), 1),
            "OneStartRatings": int(ratings_count * 0.02),
            "TwoStartRatings": int(ratings_count * 0.03),
            "ThreeStartRatings": int(ratings_count * 0.08),
            "FourStartRatings": int(ratings_count * 0.17),
            "FiveStartRatings": int(ratings_count * 0.70),
            "AsinSalesCount": random.randint(100, 30000),
            "OffSale": 0,
            "Rank": random.randint(1, 500),
            "Category": ["Sports & Outdoors", "sporting-goods"],
            "BsrCategory": [[random.choice(["Mats", "Gadgets", "Tools"]), str(random.randint(1000000, 9999999)), str(random.randint(1, 10)), "20260504"]],
            "SellerCount": random.randint(1, 5),
            "OnlineDate": f"201{random.randint(8,9)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "OnlineDays": random.randint(800, 2500),
            "HasVideo": random.choice([True, False]),
            "APlus": random.choice([True, False]),
            "HasBrandStore": random.choice([True, False]),
            "Size": ["10.5,8.3,2.1"],
            "Weight": random.randint(100, 5000),
            "DealType": "",
            "BrandPromotion": "",
            "ExtraSavings": [],
            "Photo": [f"https://m.media-amazon.com/images/I/{random.randint(10,99)}abcdefg.jpg" for _ in range(random.randint(3, 7))],
            "EBCPhoto": [],
            "Feature": {"Quality": round(random.uniform(3.5, 4.8), 1), "Value": round(random.uniform(3.5, 4.8), 1)},
            "Property": "[]",
            "ProductInfo": "[]",
            "ProductBadge": random.choice([[], ["Best Seller"], ["Amazon's Choice"]]),
            "DealTrend": [],
            "PriceTrend": [],
            "ListPriceTrend": [],
            "RankTrend": None,
            "BsrRankTrend": [],
            "ListingSalesVolumeOfDailyTrend": [],
            "ListingSalesVolumeOfMonthTrend": [],
            "ListingSalesOfDailyTrend": [],
            "ListingSalesOfMonthTrend": [],
            "VariationASIN": [],
            "Attribute": [],
            "FbaDetetail": [fba_fee, "1-9:46", "10-12:116"],
            "UpdateDate": "2026-05-04",
            "VariationASINCount": 0,
        },
        "RequestLeft": 9999,
        "RequestConsumed": 1,
    }


def _parse_top3_asin(raw: list[str]) -> list[dict]:
    """解析 Top3asin 数组: 'ASIN,click%,conv%' → dict"""
    result = []
    for item in raw:
        parts = item.split(",")
        if len(parts) >= 3:
            result.append({
                "asin": parts[0],
                "click_share": float(parts[1].replace("%", "")),
                "conversion_share": float(parts[2].replace("%", "")),
            })
    return result


class SorftimeClient:
    """Sorftime CLI 客户端。SORFTIME_MOCK=true 时使用 mock 数据。"""

    def __init__(self, mock: bool = None):
        if mock is None:
            mock = os.environ.get("SORFTIME_MOCK", "true").lower() == "true"
        self.mock = mock
        self._request_count = 0

    def _call_api(self, endpoint: str, params: dict, domain: int = 1) -> dict:
        params_json = json.dumps(params)
        cmd = f"sorftime api {endpoint} '{params_json}' --domain {domain}"
        result = subprocess.run(["bash", "-c", cmd], capture_output=True,
                                text=True, timeout=120, encoding="utf-8",
                                errors="replace")

        if result.returncode != 0:
            raise RuntimeError(f"Sorftime CLI failed: {result.stderr}")

        # Find the JSON portion in stdout (skip spinner text)
        stdout = result.stdout
        json_start = stdout.find('{')
        if json_start < 0:
            raise RuntimeError(f"No JSON in Sorftime response: {stdout[:500]}")

        data = json.loads(stdout[json_start:])
        code = data.get("Code", -1)
        if code == 11:
            return {"Code": 11, "Message": "No data", "Data": []}
        if code != 0:
            raise RuntimeError(
                f"Sorftime API error {code}: {data.get('Message')}")
        return data

    def query_keyword(self, keyword: str, domain: int = 1) -> dict:
        self._request_count += 1
        if self.mock:
            data = _mock_keyword_response(keyword)
        else:
            data = self._call_api("KeywordRequest", {"keyword": keyword}, domain)
        # 补充解析后的 Top3 ASIN
        top3_raw = data["Data"].get("Top3asin", [])
        data["Data"]["Top3asinParsed"] = _parse_top3_asin(top3_raw)
        return data["Data"]

    def query_product(self, asin: str, domain: int = 1) -> dict:
        self._request_count += 1
        if self.mock:
            data = _mock_product_response(asin)
        else:
            data = self._call_api("ProductRequest", {"asin": asin, "trend": 0}, domain)
        return data["Data"]

    def batch_query_keywords(self, keywords: list[str], domain: int = 1,
                              on_progress=None) -> dict[str, dict]:
        """逐条查询关键词，内置 1s 间隔。返回 {keyword: data_dict}。"""
        results = {}
        total = len(keywords)
        for i, kw in enumerate(keywords):
            try:
                results[kw] = self.query_keyword(kw, domain)
            except Exception as e:
                results[kw] = {"_error": str(e)}
            if on_progress:
                on_progress(i + 1, total, kw)
            if not self.mock and i < total - 1:
                time.sleep(1.0)  # 速率控制
        return results

    def batch_query_products(self, asins: list[str], domain: int = 1,
                              on_progress=None) -> dict[str, dict]:
        """查询 ASIN 详情，逐条调用。返回 {asin: data_dict}。"""
        results = {}
        total = len(asins)
        for i, asin in enumerate(asins):
            try:
                results[asin] = self.query_product(asin, domain)
            except Exception as e:
                results[asin] = {"_error": str(e)}
            if on_progress:
                on_progress(i + 1, total, asin)
            if not self.mock and i < total - 1:
                time.sleep(0.5)
        return results
