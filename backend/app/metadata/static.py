import json

TABLE_METADATA = {
    "dim_date": ("日期维表", "日期、周、月、季度、年份维度", "time"),
    "dim_users": ("用户维表", "用户基础属性和注册信息", "user"),
    "dim_products": ("商品维表", "商品品类、品牌和价格信息", "product"),
    "dim_regions": ("区域维表", "省份、城市和大区信息", "region"),
    "dim_channels": ("渠道维表", "订单来源渠道信息", "channel"),
    "fact_orders": ("订单事实表", "订单支付金额、折扣和状态", "sales"),
    "fact_order_items": ("订单明细事实表", "订单商品明细、数量和明细金额", "sales"),
}

COLUMN_DESCRIPTIONS = {
    "date_key": "日期键，格式 YYYYMMDD",
    "date_value": "日期",
    "year": "年份",
    "quarter": "季度",
    "month": "月份",
    "week": "周序号",
    "day_of_week": "星期",
    "user_key": "用户代理键",
    "user_id": "用户业务 ID",
    "name": "名称",
    "gender": "性别",
    "age_group": "年龄段",
    "register_date": "注册日期",
    "city": "城市",
    "product_key": "商品代理键",
    "product_id": "商品业务 ID",
    "category": "一级品类",
    "sub_category": "二级品类",
    "brand": "品牌",
    "price": "标价",
    "region_key": "区域代理键",
    "province": "省份",
    "region_group": "大区",
    "channel_key": "渠道代理键",
    "channel_name": "渠道名称",
    "channel_type": "渠道类型",
    "order_id": "订单 ID",
    "total_amount": "订单原始金额",
    "discount_amount": "订单优惠金额",
    "payment_amount": "订单实付金额，销售额口径字段",
    "order_status": "订单状态",
    "item_id": "订单明细 ID",
    "quantity": "商品数量",
    "unit_price": "成交单价",
    "item_amount": "明细金额",
}

# 按 (table_name, column_name) 覆盖全局描述，解决同名字段歧义
TABLE_COLUMN_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("dim_users", "name"): "用户姓名",
    ("dim_users", "city"): "用户所在城市",
    ("dim_products", "name"): "商品名称",
    ("dim_regions", "city"): "城市",
}

DIMENSION_COLUMNS = {
    "date_key",
    "date_value",
    "year",
    "quarter",
    "month",
    "week",
    "day_of_week",
    "user_key",
    "user_id",
    "gender",
    "age_group",
    "register_date",
    "city",
    "product_key",
    "product_id",
    "category",
    "sub_category",
    "brand",
    "region_key",
    "province",
    "region_group",
    "channel_key",
    "channel_name",
    "channel_type",
    "order_id",
    "order_status",
    "item_id",
}

METRIC_COLUMNS = {
    "price",
    "total_amount",
    "discount_amount",
    "payment_amount",
    "quantity",
    "unit_price",
    "item_amount",
}

SAMPLE_VALUES = {
    "gender": ["女", "男"],
    "age_group": ["18-24", "25-34", "35-44", "45+"],
    "category": ["手机数码", "家用电器", "服饰鞋包", "食品生鲜", "美妆个护"],
    "region_group": ["华东", "华北", "华南", "西南", "华中"],
    "channel_name": ["官网", "天猫", "京东", "抖音", "小程序"],
    "channel_type": ["自营", "平台", "内容电商"],
    "order_status": ["paid", "completed", "refunded"],
}

FIXED_RELATIONSHIPS = [
    ("fact_orders", "user_key", "dim_users", "user_key", "many_to_one", "订单关联用户"),
    ("fact_orders", "region_key", "dim_regions", "region_key", "many_to_one", "订单关联区域"),
    ("fact_orders", "channel_key", "dim_channels", "channel_key", "many_to_one", "订单关联渠道"),
    ("fact_orders", "date_key", "dim_date", "date_key", "many_to_one", "订单关联日期"),
    ("fact_order_items", "order_id", "fact_orders", "order_id", "many_to_one", "明细关联订单"),
    ("fact_order_items", "product_key", "dim_products", "product_key", "many_to_one", "明细关联商品"),
    ("fact_order_items", "date_key", "dim_date", "date_key", "many_to_one", "明细关联日期"),
]


def sample_values_json(column_name: str) -> str | None:
    values = SAMPLE_VALUES.get(column_name)
    return json.dumps(values, ensure_ascii=False) if values else None
