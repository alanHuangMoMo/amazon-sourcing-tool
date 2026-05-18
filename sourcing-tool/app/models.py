from sqlalchemy import create_engine, Column, Integer, Float, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from datetime import datetime, timezone
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    if "sslmode" not in DATABASE_URL.lower() and "supabase" in DATABASE_URL and ":6543" not in DATABASE_URL:
        DATABASE_URL += "?sslmode=require" if "?" not in DATABASE_URL else "&sslmode=require"
    engine = create_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "sourcing.db")
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


# ── ABA 月度报表（替代旧的 aba_raw） ───────────
class AbaReport(Base):
    __tablename__ = "aba_report"
    __table_args__ = (UniqueConstraint("keyword", "domain", "report_date", name="uq_kw_domain_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=False, default="CA")
    report_date = Column(String, nullable=False)

    search_rank = Column(Integer)
    brand_1 = Column(String)
    brand_2 = Column(String)
    brand_3 = Column(String)
    category_1 = Column(String)
    category_2 = Column(String)
    category_3 = Column(String)

    asin_1 = Column(String)
    asin_1_title = Column(Text)
    asin_1_click_share = Column(Float)
    asin_1_conversion_share = Column(Float)

    asin_2 = Column(String)
    asin_2_title = Column(Text)
    asin_2_click_share = Column(Float)
    asin_2_conversion_share = Column(Float)

    asin_3 = Column(String)
    asin_3_title = Column(Text)
    asin_3_click_share = Column(Float)
    asin_3_conversion_share = Column(Float)

    raw_response = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── ABA 原始导入（旧表，保留兼容） ───────────
class AbaRaw(Base):
    __tablename__ = "aba_raw"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)
    asin_rank = Column(Integer, nullable=False)
    asin = Column(String, nullable=False)
    click_share = Column(Float, nullable=False)
    conversion_share = Column(Float, nullable=False)
    brand_1 = Column(String, default="")
    brand_2 = Column(String, default="")
    brand_3 = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 倒置表: ASIN → 关键词 ─────────────────────
class AsinKeyword(Base):
    __tablename__ = "asin_keyword"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, index=True)
    asin = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)
    conversion_index = Column(Float, nullable=False)
    click_share = Column(Float, nullable=False)
    conversion_share = Column(Float, nullable=False)
    passed_filter = Column(Boolean, default=True)


# ── 去重关键词 ────────────────────────────────
class DeduplicatedKeyword(Base):
    __tablename__ = "deduplicated_keyword"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)


# ── 关键词 Sorftime 缓存（全字段） ─────────────
class KeywordCache(Base):
    """Sorftime KeywordRequest 返回的全部字段，一次查询永久缓存。"""
    __tablename__ = "keyword_cache"
    __table_args__ = (UniqueConstraint("keyword", "domain", name="uq_keyword_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False, index=True)
    domain = Column(Integer, nullable=False, default=1)

    # ── 标量字段 ──
    keyword_cn_name = Column(String)
    rank = Column(Integer)
    search_volume = Column(Integer)
    cpc = Column(Float)                    # 美分 ÷ 100 = USD
    cpc_range_min = Column(Float)
    cpc_range_max = Column(Float)
    search_conversion_rate = Column(Float)
    search_conversion_rate_d90 = Column(Float)
    click_conversion_rate_d90 = Column(Float)
    sales_volume_90d = Column(Integer)
    click_of_90d = Column(Integer)
    word_count = Column(Integer)
    product_count = Column(Integer)
    rank_change_of_weekly = Column(Integer)
    share_click_rate = Column(Float)
    share_conversion_rate = Column(Float)
    season = Column(String)
    update_date = Column(String)
    department = Column(String)

    # ── JSON / 数组字段 ──
    top3_asin = Column(Text)
    top3_brand = Column(Text)
    top3_category = Column(Text)
    images = Column(Text)
    images_from_asin = Column(Text)
    cpc_trend = Column(Text)
    search_volume_trend = Column(Text)
    search_volume_growth_trend = Column(Text)
    search_volume_growth_rate_trend = Column(Text)
    search_result_of_fp = Column(Text)
    associated_with_category = Column(Text)
    associated_with_category_detail = Column(Text)

    # ── 完整原始返回（兜底） ──
    raw_response = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── ASIN Sorftime 缓存（全字段） ────────────────
class AsinCache(Base):
    """Sorftime ProductRequest 返回的全部字段，一次查询永久缓存。"""
    __tablename__ = "asin_cache"
    __table_args__ = (UniqueConstraint("asin", "domain", name="uq_asin_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    asin = Column(String, nullable=False, index=True)
    domain = Column(Integer, nullable=False, default=1)

    # ── 基本信息 ──
    title = Column(Text)
    description = Column(Text)
    brand = Column(String)
    parent_asin = Column(String)
    product_type = Column(String)
    store_name = Column(String)

    # ── 价格相关（美分 ÷ 100 = USD） ──
    price = Column(Float)
    list_price = Column(Float)
    sales_price = Column(Float)
    coupon = Column(Integer)

    # ── 费用与利润 ──
    fba_fee = Column(Float)
    platform_fee = Column(Float)
    profit = Column(Float)
    profit_rate = Column(Float)
    ship_cost = Column(Float)

    # ── 购物车 / FBA ──
    is_fba = Column(Boolean)
    ships_from = Column(String)
    buybox_seller = Column(String)
    buybox_seller_id = Column(String)
    buybox_seller_address = Column(String)

    # ── 评价与销量 ──
    ratings_count = Column(Integer)
    ratings = Column(Float)
    one_start_ratings = Column(Integer)
    two_start_ratings = Column(Integer)
    three_start_ratings = Column(Integer)
    four_start_ratings = Column(Integer)
    five_start_ratings = Column(Integer)
    asin_sales_count = Column(Integer)
    off_sale = Column(Integer)

    # ── 排名与类目 ──
    rank = Column(Integer)
    category = Column(Text)
    bsr_category = Column(Text)
    seller_count = Column(Integer)

    # ── 上架信息 ──
    online_date = Column(String)
    online_days = Column(Integer)

    # ── 标记位 ──
    has_video = Column(Boolean)
    aplus = Column(Boolean)
    has_brand_store = Column(Boolean)

    # ── 尺寸重量 ──
    size = Column(Text)
    weight = Column(Integer)

    # ── 活动 ──
    deal_type = Column(String)
    brand_promotion = Column(String)
    extra_savings = Column(Text)

    # ── 多媒体 ──
    photo = Column(Text)
    ebc_photo = Column(Text)

    # ── 属性 ──
    feature = Column(Text)
    property = Column(Text)
    product_info = Column(Text)
    product_badge = Column(Text)

    # ── 趋势数据 ──
    deal_trend = Column(Text)
    price_trend = Column(Text)
    list_price_trend = Column(Text)
    rank_trend = Column(Text)
    bsr_rank_trend = Column(Text)
    listing_sales_volume_of_daily_trend = Column(Text)
    listing_sales_volume_of_month_trend = Column(Text)
    listing_sales_of_daily_trend = Column(Text)
    listing_sales_of_month_trend = Column(Text)
    variation_asin = Column(Text)
    attribute = Column(Text)
    fba_detail = Column(Text)

    # ── 杂项 ──
    update_date = Column(String)
    variation_asin_count = Column(Integer)

    # ── 完整原始返回（兜底） ──
    raw_response = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 最终候选清单 ──────────────────────────────
class Candidate(Base):
    __tablename__ = "candidate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, index=True)
    asin = Column(String, nullable=False)
    keywords = Column(Text, nullable=False)
    keyword_count = Column(Integer, default=0)
    avg_conversion_index = Column(Float, default=0)
    cpc = Column(Float, default=0)
    cpc_range_min = Column(Float)
    cpc_range_max = Column(Float)
    search_volume = Column(Integer, default=0)
    search_conversion_rate = Column(Float, default=0)
    sales_volume_90d = Column(Integer, default=0)
    price = Column(Float, default=0)
    fba_fee = Column(Float, default=0)
    platform_fee = Column(Float, default=0)
    profit = Column(Float, default=0)
    profit_rate = Column(Float, default=0)
    net_repayment = Column(Float, default=0)
    shipping_cost = Column(Float, default=0)       # 海运预估 (USD)
    brand = Column(String)
    ratings_count = Column(Integer, default=0)
    ratings = Column(Float)
    online_date = Column(String)
    is_fba = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 批次状态 ──────────────────────────────────
class Batch(Base):
    __tablename__ = "batch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, nullable=False, unique=True, index=True)
    domain = Column(Integer, default=1)
    status = Column(String, default="uploaded")
    total_aba_rows = Column(Integer, default=0)
    step1_passed = Column(Integer, default=0)
    step3_passed = Column(Integer, default=0)
    final_candidates = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 卖家精灵 KCR 关键词数据 ────────────────────
class SellerspriteKeyword(Base):
    """卖家精灵关键词转化率导出，替代 Sorftime 关键词查询用于初筛。"""
    __tablename__ = "sellersprite_keyword"
    __table_args__ = (UniqueConstraint("keyword", "domain", name="uq_ss_kw_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False, index=True)
    keyword_cn = Column(String)
    domain = Column(String, nullable=False, default="US")
    batch_id = Column(String, index=True)

    search_volume = Column(Integer)               # 近90天搜索量
    sales_volume_90d = Column(Integer)            # 近90天销量
    purchases_90d = Column(Integer)               # 近90天购买量
    search_conversion_rate = Column(Float)        # 搜索转化率 %
    click_conversion_rate = Column(Float)         # 销量转化率 %

    cpc_recommended = Column(Float)               # PPC竞价-推荐（本币元）
    cpc_high = Column(Float)                      # PPC竞价-顶格
    cpc_low = Column(Float)                       # PPC竞价-底格

    cpa_recommended = Column(Float)               # CPA-推荐（本币元）
    cpa_high = Column(Float)
    cpa_low = Column(Float)

    avg_price = Column(Float)                     # 产品价格-平均（本币元）
    min_price = Column(Float)
    max_price = Column(Float)

    acos_recommended = Column(Float)              # ACOS-推荐 %
    acos_high = Column(Float)
    acos_low = Column(Float)

    budget = Column(Float)                        # 费用预算（本币元）
    click_share = Column(Float)                   # 点击量占比 %
    conv_share = Column(Float)                    # 转化量占比 %

    top3_asins = Column(Text)                     # JSON: [#1/#2/#3 ASIN + 点击占比 + 转化占比]
    top10_asins = Column(Text)                    # 关键词前10 ASIN 列表

    raw_response = Column(Text)
    queried_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 卖家精灵产品库数据 ──────────────────────────
class SellerspriteProduct(Base):
    """卖家精灵产品库导出，替代 Sorftime 产品查询用于初筛。"""
    __tablename__ = "sellersprite_product"
    __table_args__ = (UniqueConstraint("asin", "domain", name="uq_ss_asin_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    asin = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=False, default="US")
    batch_id = Column(String, index=True)

    sku = Column(Text)
    title = Column(Text)
    brand = Column(String)
    brand_url = Column(Text)
    product_url = Column(Text)                     # [6] 产品详情页链接
    main_image = Column(Text)                      # [7] 产品主图
    parent_asin = Column(String)                   # [8] 父ASIN
    category_path = Column(Text)
    main_category = Column(String)
    main_bsr = Column(Integer)
    main_bsr_trend = Column(Text)                  # [12] 大类BSR趋势图
    main_bsr_trend_detail = Column(Text)           # [13] 大类BSR趋势详情
    sub_category = Column(String)
    sub_bsr = Column(Integer)

    price = Column(Float)                         # 价格（本币元）
    prime_price = Column(Float)
    coupon = Column(Float)
    fba_fee = Column(Float)                       # FBA费（本币元）
    shipping_fee = Column(Float)                   # [36] 运费（本币元）
    profit_rate = Column(Float)                   # 毛利率 %
    profit = Column(Float)                        # 净利（本币元）
    gross_margin = Column(Float)                  # 毛利润（本币元）

    monthly_sales = Column(Integer)               # 月销量
    monthly_revenue = Column(Float)               # 月销售额（本币元）
    total_sales = Column(Integer)
    total_revenue = Column(Float)

    ratings = Column(Float)
    ratings_count = Column(Integer)
    reviews_count = Column(Integer)
    reviews_trend = Column(Text)                   # [17] 评论数趋势图
    qa_count = Column(Integer)                     # [25] Q&A

    online_date = Column(String)
    online_days = Column(Integer)
    is_fba = Column(Boolean, default=False)       # FBA/AMZ → True
    ship_method = Column(String)

    seller_count = Column(Integer)
    buybox_seller = Column(String)
    buybox_price = Column(Float)                   # [40] BuyBox价格（本币元）
    seller_country = Column(String)
    seller_info = Column(Text)
    seller_page = Column(Text)                     # [43] 卖家主页

    lqs = Column(Integer)                         # Listing Quality Score
    variation_count = Column(Integer)
    aplus = Column(Boolean, default=False)
    has_video = Column(Boolean, default=False)
    badge = Column(Text)                          # Best Seller / Amazon's Choice

    sp_ad = Column(Boolean, default=False)
    brand_ad = Column(Boolean, default=False)
    brand_promotion = Column(Boolean, default=False)
    deal_7day = Column(Boolean, default=False)

    weight = Column(Float)                        # 产品重量 g
    weight_conv = Column(Float)                    # [55] 产品重量（单位换算）
    size = Column(Text)                           # 产品尺寸
    size_conv = Column(Text)                       # [57] 产品尺寸（单位换算）
    package_weight = Column(Float)                 # [58] 包装重量 g
    package_weight_conv = Column(Float)            # [59] 包装重量（单位换算）
    package_size = Column(Text)                    # [60] 包装尺寸
    package_size_conv = Column(Text)               # [61] 包装尺寸（单位换算）
    package_size_field = Column(Text)              # [62] 包装尺寸字段
    tags = Column(Text)                            # [63] 标签
    ac_keywords = Column(Text)

    raw_response = Column(Text)
    queried_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 医疗器械类目树（关键词拓词项目） ──────────────
class MedicalCategory(Base):
    """Amazon 医疗器械相关底层类目，用于 CategoryRequestKeyword 按类目拓词。"""
    __tablename__ = "medical_category"
    __table_args__ = (UniqueConstraint("node_id", "domain", name="uq_medical_nodeid_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=False, default="US")
    name = Column(String, nullable=False)
    cn_name = Column(String)
    path = Column(Text)
    root_node_id = Column(String, index=True)
    url = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 词根表（关键词挖掘 Unique Words sheet） ─────
class WordRoot(Base):
    """从卖家精灵关键词挖掘 Excel 的 Unique Words sheet 解析的词根频次。"""
    __tablename__ = "word_root"
    __table_args__ = (UniqueConstraint("word", "batch_label", "domain", name="uq_wr_word_batch_domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_label = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=False, default="US")
    word = Column(String, nullable=False, index=True)
    frequency = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── 赛道保存表 ──────────────────────────────────
class NicheTrack(Base):
    """用户保存的词根组合赛道，含统计快照。"""
    __tablename__ = "niche_track"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    keyword_batch_label = Column(String, nullable=False, index=True)
    product_batch_label = Column(String)
    domain = Column(String, nullable=False, default="US")
    root_words = Column(Text, nullable=False)       # JSON: ["root1","root2",...]
    keyword_count = Column(Integer, default=0)
    asin_count = Column(Integer, default=0)
    stats_snapshot = Column(Text)                   # JSON: 聚合指标快照
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        print(f"[init_db] WARNING: Could not create tables: {e}", file=sys.stderr)
        # Don't crash — app can still serve pages, DB operations will fail gracefully


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
