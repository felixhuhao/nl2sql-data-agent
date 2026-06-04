CREATE DATABASE IF NOT EXISTS ecommerce;

DROP TABLE IF EXISTS ecommerce.fact_order_items;
DROP TABLE IF EXISTS ecommerce.fact_orders;
DROP TABLE IF EXISTS ecommerce.dim_channels;
DROP TABLE IF EXISTS ecommerce.dim_regions;
DROP TABLE IF EXISTS ecommerce.dim_products;
DROP TABLE IF EXISTS ecommerce.dim_users;
DROP TABLE IF EXISTS ecommerce.dim_date;

CREATE TABLE ecommerce.dim_date (
    date_key    UInt32,
    date_value  Date,
    year        UInt16,
    quarter     UInt8,
    month       UInt8,
    week        UInt8,
    day_of_week UInt8
)
ENGINE = MergeTree()
ORDER BY date_key;

CREATE TABLE ecommerce.dim_users (
    user_key      UInt32,
    user_id       String,
    name          String,
    gender        LowCardinality(String),
    age_group     LowCardinality(String),
    register_date Date,
    city          LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY user_key;

CREATE TABLE ecommerce.dim_products (
    product_key  UInt32,
    product_id   String,
    name         String,
    category     LowCardinality(String),
    sub_category LowCardinality(String),
    brand        LowCardinality(String),
    price        Decimal(12, 2)
)
ENGINE = MergeTree()
ORDER BY product_key;

CREATE TABLE ecommerce.dim_regions (
    region_key   UInt32,
    province     LowCardinality(String),
    city         LowCardinality(String),
    region_group LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY region_key;

CREATE TABLE ecommerce.dim_channels (
    channel_key  UInt32,
    channel_name LowCardinality(String),
    channel_type LowCardinality(String)
)
ENGINE = MergeTree()
ORDER BY channel_key;

CREATE TABLE ecommerce.fact_orders (
    order_id        String,
    user_key        UInt32,
    region_key      UInt32,
    channel_key     UInt32,
    date_key        UInt32,
    total_amount    Decimal(12, 2),
    discount_amount Decimal(12, 2),
    payment_amount  Decimal(12, 2),
    order_status    LowCardinality(String)
)
ENGINE = MergeTree()
PARTITION BY intDiv(date_key, 100)
ORDER BY (date_key, region_key, channel_key, order_id)
PRIMARY KEY (date_key, region_key, channel_key);

CREATE TABLE ecommerce.fact_order_items (
    item_id     String,
    order_id    String,
    product_key UInt32,
    date_key    UInt32,
    quantity    UInt32,
    unit_price  Decimal(12, 2),
    item_amount Decimal(12, 2)
)
ENGINE = MergeTree()
PARTITION BY intDiv(date_key, 100)
ORDER BY (date_key, product_key, order_id, item_id)
PRIMARY KEY (date_key, product_key);
