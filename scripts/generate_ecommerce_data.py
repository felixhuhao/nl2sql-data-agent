from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DUCKDB_PATH = PROJECT_ROOT / "data" / "ecommerce.duckdb"
RANDOM_SEED = 20260529


def main() -> None:
    random.seed(RANDOM_SEED)
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DUCKDB_PATH)) as conn:
        _drop_tables(conn)
        _create_tables(conn)
        conn.execute("BEGIN TRANSACTION")
        dates = _insert_dates(conn)
        users = _insert_users(conn)
        products = _insert_products(conn)
        regions = _insert_regions(conn)
        channels = _insert_channels(conn)
        _insert_orders_and_items(conn, dates, users, products, regions, channels)
        conn.execute("COMMIT")
    print(f"Generated DuckDB dataset: {DUCKDB_PATH}")


def _drop_tables(conn: duckdb.DuckDBPyConnection) -> None:
    for table in [
        "fact_order_items",
        "fact_orders",
        "dim_channels",
        "dim_regions",
        "dim_products",
        "dim_users",
        "dim_date",
    ]:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE dim_date (
            date_key INTEGER PRIMARY KEY,
            date_value DATE,
            year INTEGER,
            quarter INTEGER,
            month INTEGER,
            week INTEGER,
            day_of_week INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_users (
            user_key INTEGER PRIMARY KEY,
            user_id VARCHAR,
            name VARCHAR,
            gender VARCHAR,
            age_group VARCHAR,
            register_date DATE,
            city VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_products (
            product_key INTEGER PRIMARY KEY,
            product_id VARCHAR,
            name VARCHAR,
            category VARCHAR,
            sub_category VARCHAR,
            brand VARCHAR,
            price DECIMAL(12, 2)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_regions (
            region_key INTEGER PRIMARY KEY,
            province VARCHAR,
            city VARCHAR,
            region_group VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_channels (
            channel_key INTEGER PRIMARY KEY,
            channel_name VARCHAR,
            channel_type VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_orders (
            order_id VARCHAR PRIMARY KEY,
            user_key INTEGER,
            region_key INTEGER,
            channel_key INTEGER,
            date_key INTEGER,
            total_amount DECIMAL(12, 2),
            discount_amount DECIMAL(12, 2),
            payment_amount DECIMAL(12, 2),
            order_status VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_order_items (
            item_id VARCHAR PRIMARY KEY,
            order_id VARCHAR,
            product_key INTEGER,
            date_key INTEGER,
            quantity INTEGER,
            unit_price DECIMAL(12, 2),
            item_amount DECIMAL(12, 2)
        )
        """
    )


def _insert_dates(conn: duckdb.DuckDBPyConnection) -> list[int]:
    rows = []
    current = date(2024, 1, 1)
    end = date(2025, 12, 31)
    while current <= end:
        rows.append(
            (
                int(current.strftime("%Y%m%d")),
                current,
                current.year,
                (current.month - 1) // 3 + 1,
                current.month,
                current.isocalendar().week,
                current.isoweekday(),
            )
        )
        current += timedelta(days=1)
    conn.executemany("INSERT INTO dim_date VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return [row[0] for row in rows]


def _insert_users(conn: duckdb.DuckDBPyConnection) -> list[int]:
    cities = ["上海", "北京", "广州", "深圳", "杭州", "成都", "武汉", "南京", "苏州", "重庆"]
    rows = []
    for user_key in range(1, 51):
        rows.append(
            (
                user_key,
                f"U{user_key:05d}",
                f"用户{user_key:03d}",
                random.choice(["女", "男"]),
                random.choice(["18-24", "25-34", "35-44", "45+"]),
                date(2023, random.randint(1, 12), random.randint(1, 28)),
                random.choice(cities),
            )
        )
    conn.executemany("INSERT INTO dim_users VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return [row[0] for row in rows]


def _insert_products(conn: duckdb.DuckDBPyConnection) -> list[tuple[int, float]]:
    categories = {
        "手机数码": ["手机", "电脑", "智能穿戴", "摄影摄像"],
        "家用电器": ["厨房电器", "生活电器", "大家电", "个护电器"],
        "服饰鞋包": ["男装", "女装", "运动鞋", "箱包"],
        "食品生鲜": ["休闲零食", "粮油调味", "水果", "乳品"],
        "美妆个护": ["护肤", "彩妆", "洗护", "香水"],
    }
    brands = ["Aurora", "Beacon", "Cloud", "Delta", "Echo", "Fusion", "Nova", "Pulse"]
    rows = []
    product_key = 1
    for category, sub_categories in categories.items():
        for index in range(20):
            sub_category = sub_categories[index % len(sub_categories)]
            price = round(random.uniform(29, 4999), 2)
            rows.append(
                (
                    product_key,
                    f"P{product_key:05d}",
                    f"{sub_category}商品{index + 1:02d}",
                    category,
                    sub_category,
                    random.choice(brands),
                    price,
                )
            )
            product_key += 1
    conn.executemany("INSERT INTO dim_products VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return [(row[0], float(row[6])) for row in rows]


def _insert_regions(conn: duckdb.DuckDBPyConnection) -> list[int]:
    regions = [
        ("上海", "上海", "华东"),
        ("江苏", "南京", "华东"),
        ("江苏", "苏州", "华东"),
        ("浙江", "杭州", "华东"),
        ("浙江", "宁波", "华东"),
        ("山东", "青岛", "华东"),
        ("北京", "北京", "华北"),
        ("天津", "天津", "华北"),
        ("河北", "石家庄", "华北"),
        ("山西", "太原", "华北"),
        ("内蒙古", "呼和浩特", "华北"),
        ("广东", "广州", "华南"),
        ("广东", "深圳", "华南"),
        ("福建", "厦门", "华南"),
        ("广西", "南宁", "华南"),
        ("海南", "海口", "华南"),
        ("四川", "成都", "西南"),
        ("重庆", "重庆", "西南"),
        ("云南", "昆明", "西南"),
        ("贵州", "贵阳", "西南"),
        ("西藏", "拉萨", "西南"),
        ("湖北", "武汉", "华中"),
        ("湖南", "长沙", "华中"),
        ("河南", "郑州", "华中"),
        ("江西", "南昌", "华中"),
        ("安徽", "合肥", "华中"),
        ("陕西", "西安", "西北"),
        ("甘肃", "兰州", "西北"),
        ("新疆", "乌鲁木齐", "西北"),
        ("辽宁", "沈阳", "东北"),
    ]
    rows = [(index + 1, province, city, group) for index, (province, city, group) in enumerate(regions)]
    conn.executemany("INSERT INTO dim_regions VALUES (?, ?, ?, ?)", rows)
    return [row[0] for row in rows]


def _insert_channels(conn: duckdb.DuckDBPyConnection) -> list[int]:
    rows = [
        (1, "官网", "自营"),
        (2, "天猫", "平台"),
        (3, "京东", "平台"),
        (4, "抖音", "内容电商"),
        (5, "小程序", "自营"),
    ]
    conn.executemany("INSERT INTO dim_channels VALUES (?, ?, ?)", rows)
    return [row[0] for row in rows]


def _insert_orders_and_items(
    conn: duckdb.DuckDBPyConnection,
    dates: list[int],
    users: list[int],
    products: list[tuple[int, float]],
    regions: list[int],
    channels: list[int],
) -> None:
    product_prices = dict(products)
    order_rows = []
    item_rows = []
    item_seq = 1
    for order_seq in range(1, 10001):
        order_id = f"O{order_seq:08d}"
        date_key = random.choice(dates)
        item_count = random.randint(1, 5)
        order_total = 0.0
        for _ in range(item_count):
            product_key, base_price = random.choice(products)
            quantity = random.randint(1, 3)
            unit_price = round(product_prices[product_key] * random.uniform(0.85, 1.05), 2)
            item_amount = round(unit_price * quantity, 2)
            order_total += item_amount
            item_rows.append(
                (
                    f"I{item_seq:09d}",
                    order_id,
                    product_key,
                    date_key,
                    quantity,
                    unit_price,
                    item_amount,
                )
            )
            item_seq += 1
        discount_amount = round(order_total * random.choice([0, 0.03, 0.05, 0.08, 0.1]), 2)
        payment_amount = round(order_total - discount_amount, 2)
        order_rows.append(
            (
                order_id,
                random.choice(users),
                random.choice(regions),
                random.choice(channels),
                date_key,
                round(order_total, 2),
                discount_amount,
                payment_amount,
                random.choices(["paid", "completed", "refunded"], weights=[55, 40, 5], k=1)[0],
            )
        )
    conn.executemany("INSERT INTO fact_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", order_rows)
    conn.executemany("INSERT INTO fact_order_items VALUES (?, ?, ?, ?, ?, ?, ?)", item_rows)


if __name__ == "__main__":
    main()
