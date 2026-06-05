# Smoke Eval Report

## Summary

- Cases: 18
- Passed: 18/18
- Normal cases: 18
- Safety cases: 0
- Provider: deepseek
- Skipped cases: 57
- Reference result matches: 14/14 checked
- Fallback used: 1/18
- Repair cases: 0/18
- Total repair attempts: 0
- Full schema context chars: 7345
- Avg focused context chars: 1885
- Avg focused context reduction: 74.3%
- Avg elapsed: 13.2s
- Chart recommendations: bar=9, line=2, table=7

## Datasource Summary

| Datasource | Dialect | Cases | Passed | Avg elapsed |
|------------|---------|-------|--------|-------------|
| DuckDB (本地) | duckdb | 18 | 18/18 | 13.2s |

## Error Distribution

| Category | Count | Cases |
|----------|-------|-------|
| n/a | 0 | - |

## Skipped Cases

- top_category_by_region (provider=mock)
- user_repeat_purchase_rate (provider=mock)
- recent_7d_vs_30d_sales (provider=mock)
- payment_distribution (provider=mock)
- phase4_value_region (provider=mock)
- phase4_value_channel (provider=mock)
- phase4_value_category (provider=mock)
- phase4_value_multi (provider=mock)
- phase4_semantic_alias (provider=mock)
- phase4_semantic_typo (provider=mock)
- phase5_scope_guard_repair (provider=mock)
- phase5_fanout_repair (provider=mock)
- phase5_execution_repair (provider=mock)
- phase5_max_repair_exhausted (provider=mock)
- phase5_operation_not_repairable (provider=mock)
- unsafe_fanout_order_amount_after_item_join (provider=mock)
- unsafe_delete_orders (provider=mock)
- unsafe_drop_table (provider=mock)
- unsafe_create_table (provider=mock)
- unsafe_non_whitelist_table (provider=mock)
- unsafe_external_read (provider=mock)
- unsafe_update_orders (provider=mock)
- unsafe_truncate_table (provider=mock)
- unsafe_read_parquet (provider=mock)
- phase65_duckdb_top_products_bar (provider=mock)
- phase65_duckdb_bare_top_products (provider=mock)
- phase65_duckdb_top_hyphen_channels (provider=mock)
- phase65_duckdb_channel_share_pie (provider=mock)
- phase65_duckdb_region_top_share (provider=mock)
- phase65_duckdb_monthly_yoy (provider=mock)
- phase65_duckdb_monthly_mom (provider=mock)
- phase65_duckdb_daily_moving_average (provider=mock)
- ch_monthly_sales (datasource unavailable: clickhouse_ecommerce)
- ch_daily_order_count (datasource unavailable: clickhouse_ecommerce)
- ch_channel_sales_30d (datasource unavailable: clickhouse_ecommerce)
- ch_region_sales_30d (datasource unavailable: clickhouse_ecommerce)
- ch_paid_order_count_if (datasource unavailable: clickhouse_ecommerce)
- ch_recent_7d_vs_30d_sales (datasource unavailable: clickhouse_ecommerce)
- ch_average_order_value (datasource unavailable: clickhouse_ecommerce)
- ch_top_products_30d (datasource unavailable: clickhouse_ecommerce)
- ch_category_sales_30d (datasource unavailable: clickhouse_ecommerce)
- ch_region_channel_cross (datasource unavailable: clickhouse_ecommerce)
- ch_weekly_user_count (datasource unavailable: clickhouse_ecommerce)
- ch_payment_bucket (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_top_channels_bar (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_top_regions_no_time_filter (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_channel_share_pie (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_monthly_yoy (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_monthly_mom (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_daily_moving_average (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_top_products_chinese_number (datasource unavailable: clickhouse_ecommerce)
- phase65_clickhouse_region_share_topn (datasource unavailable: clickhouse_ecommerce)
- ch_unsafe_system_command (datasource unavailable: clickhouse_ecommerce)
- ch_unsafe_kill_query (datasource unavailable: clickhouse_ecommerce)
- ch_unsafe_s3_function (datasource unavailable: clickhouse_ecommerce)
- ch_unsafe_url_function (datasource unavailable: clickhouse_ecommerce)
- ch_unsafe_insert_into_function (datasource unavailable: clickhouse_ecommerce)

## Retrieval Expected Hits

| Asset | Hit | Expected | Rate |
|-------|-----|----------|------|
| retrieval tables | 10 | 10 | 100.0% |
| retrieval columns | 10 | 10 | 100.0% |
| retrieval metrics | 5 | 5 | 100.0% |

## Case Results

### DuckDB (本地) - 18 cases

| Case | Status | Type | Category | Reference | Fallback | Repairs | Elapsed | Focused Chars | Reduction | Guard | Rows | Chart | SQL |
|------|--------|------|----------|-----------|----------|---------|---------|---------------|-----------|-------|------|-------|-----|
| recent_30d_daily_sales | PASS | normal | - | n/a | False | 0 | 8.0s | 1892 | 74.2% | passed | 30 | line | SELECT d.date_value, SUM(o.payment_amount) AS sales_amount, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ... |
| recent_30d_region_sales | PASS | normal | - | n/a | False | 0 | 8.2s | 2102 | 71.4% | passed | 7 | bar | SELECT r.region_group, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.region_key JOIN di... |
| recent_30d_channel_sales | PASS | normal | - | n/a | False | 0 | 4.8s | 2113 | 71.2% | passed | 5 | bar | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key JOIN... |
| recent_30d_top_products | PASS | normal | - | n/a | False | 0 | 6.7s | 2016 | 72.6% | passed | 10 | bar | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| recent_30d_category_sales | PASS | normal | - | yes | False | 0 | 20.0s | 1458 | 80.1% | passed | 5 | bar | SELECT dim_products.category, SUM(fact_order_items.item_amount) AS sales_amount FROM fact_orders JOIN fact_order_items ON fact_orders.ord... |
| phase2_aov_metric | PASS | normal | - | yes | False | 0 | 6.1s | 976 | 86.7% | passed | 1 | table | SELECT SUM(fact_orders.payment_amount) / COUNT(DISTINCT fact_orders.order_id) AS aov FROM fact_orders |
| phase2_sales_metric_alias | PASS | normal | - | yes | False | 0 | 8.5s | 2703 | 63.2% | passed | 1 | table | SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN DAT... |
| phase2_channel_alias | PASS | normal | - | yes | False | 0 | 11.4s | 1212 | 83.5% | passed | 5 | bar | SELECT dim_channels.channel_name, SUM(fact_orders.payment_amount) AS sales_amount FROM fact_orders JOIN dim_channels ON fact_orders.chann... |
| phase2_category_alias | PASS | normal | - | yes | False | 0 | 26.8s | 965 | 86.9% | passed | 5 | bar | SELECT dim_products.category, SUM(fact_order_items.item_amount) AS sales_amount FROM fact_order_items JOIN dim_products ON fact_order_ite... |
| phase2_retrieval_fallback | PASS | normal | - | yes | True | 0 | 9.4s | 7345 | 0.0% | passed | 20 | table | SELECT fact_orders.order_id, fact_orders.payment_amount FROM fact_orders LIMIT 20 |
| recent_30d_user_orders | PASS | normal | - | yes | False | 0 | 25.1s | 1524 | 79.3% | passed | 10 | bar | SELECT dim_users.user_id, dim_users.name AS user_name, COUNT(DISTINCT fact_orders.order_id) AS order_count FROM fact_orders JOIN dim_user... |
| recent_30d_channel_user_count | PASS | normal | - | yes | False | 0 | 33.6s | 1507 | 79.5% | passed | 5 | bar | SELECT dc.channel_name, COUNT(DISTINCT fo.user_key) AS active_users FROM fact_orders fo JOIN dim_channels dc ON fo.channel_key = dc.chann... |
| recent_30d_avg_order_amount | PASS | normal | - | yes | False | 0 | 14.4s | 1241 | 83.1% | passed | 1 | table | SELECT AVG(fo.payment_amount) AS aov FROM fact_orders fo JOIN dim_date dd ON fo.date_key = dd.date_key WHERE dd.date_value BETWEEN '2025-... |
| product_sales_rank | PASS | normal | - | yes | False | 0 | 19.7s | 1100 | 85.0% | passed | 10 | bar | SELECT dim_products.name AS product_name, SUM(fact_order_items.quantity) AS total_quantity FROM fact_order_items JOIN dim_products ON fac... |
| region_channel_cross | PASS | normal | - | yes | False | 0 | 8.3s | 2082 | 71.7% | passed | 35 | table | SELECT dim_regions.region_group AS region, dim_channels.channel_name AS channel, SUM(fact_orders.payment_amount) AS sales_amount FROM fac... |
| daily_order_trend | PASS | normal | - | yes | False | 0 | 8.2s | 1323 | 82.0% | passed | 30 | line | SELECT dim_date.date_value AS order_date, COUNT(DISTINCT fact_orders.order_id) AS order_count FROM fact_orders JOIN dim_date ON fact_orde... |
| phase2_date_alias | PASS | normal | - | yes | False | 0 | 10.3s | 1323 | 82.0% | passed | 1 | table | SELECT COUNT(DISTINCT fact_orders.order_id) AS order_count FROM fact_orders JOIN dim_date ON fact_orders.date_key = dim_date.date_key WHE... |
| phase2_product_name_alias | PASS | normal | - | yes | False | 0 | 7.1s | 1043 | 85.8% | passed | 20 | table | SELECT dim_products.name AS product_name FROM dim_products ORDER BY dim_products.name LIMIT 20 |


## Failure Details

No failures.

## Retrieval Details

### DuckDB (本地) - 18 cases

#### recent_30d_daily_sales

- Question: 查询最近30天每日销售额和订单数
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value, fact_orders.payment_amount
- Metrics: order_count, sales_amount
- Verified queries: recent_30d_daily_sales

#### recent_30d_region_sales

- Question: 按地区统计最近30天销售额
- Tables: fact_orders, dim_date, dim_regions
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_regions.region_group, fact_orders.region_key
- Metrics: sales_amount
- Verified queries: recent_30d_region_sales

#### recent_30d_channel_sales

- Question: 按渠道统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_channels.channel_name, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales

#### recent_30d_top_products

- Question: 最近30天销量最高的10个商品
- Tables: dim_date, dim_products, fact_order_items
- Columns: dim_date.date_value, dim_products.name, fact_order_items.quantity
- Metrics: -
- Verified queries: recent_30d_top_products

#### recent_30d_category_sales

- Question: 按品类统计最近30天销售额
- Tables: fact_orders, dim_date, dim_products
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_products.category
- Metrics: sales_amount
- Verified queries: -

#### phase2_aov_metric

- Question: 客单价
- Tables: fact_orders
- Columns: fact_orders.payment_amount, fact_orders.order_id
- Metrics: aov
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.order_id', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['aov']; missing=[]

#### phase2_sales_metric_alias

- Question: 统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions
- Columns: fact_orders.payment_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_region_sales
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

#### phase2_channel_alias

- Question: 渠道销售额
- Tables: fact_orders, dim_channels
- Columns: fact_orders.payment_amount, dim_channels.channel_name, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_channels', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_channels.channel_name', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

#### phase2_category_alias

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products
- Columns: fact_orders.payment_amount, dim_products.category
- Metrics: sales_amount
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_products', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_products.category', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

#### phase2_retrieval_fallback

- Question: 随便看一下数据
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -

#### recent_30d_user_orders

- Question: 最近30天下单最多的10个用户
- Tables: dim_date
- Columns: dim_date.date_value
- Metrics: -
- Verified queries: -

#### recent_30d_channel_user_count

- Question: 按渠道统计最近30天活跃用户数
- Tables: dim_channels, dim_date, fact_orders
- Columns: dim_channels.channel_name, dim_date.date_value, fact_orders.channel_key
- Metrics: -
- Verified queries: -

#### recent_30d_avg_order_amount

- Question: 最近30天平均订单金额
- Tables: dim_date, fact_orders
- Columns: dim_date.date_value, fact_orders.order_id
- Metrics: -
- Verified queries: -

#### product_sales_rank

- Question: 商品销量排行
- Tables: dim_products, fact_order_items
- Columns: dim_products.name, fact_order_items.quantity
- Metrics: -
- Verified queries: -

#### region_channel_cross

- Question: 按地区和渠道交叉统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_channels.channel_name, dim_regions.region_group, fact_orders.region_key, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: -

#### daily_order_trend

- Question: 最近30天每日订单数趋势
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -

#### phase2_date_alias

- Question: 最近30天订单数
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_date.date_value', 'fact_orders.order_id']; missing=[]
  - retrieval metrics: PASS; expected=['order_count']; missing=[]

#### phase2_product_name_alias

- Question: 商品名称列表
- Tables: dim_products
- Columns: dim_products.name
- Metrics: -
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_products']; missing=[]
  - retrieval columns: PASS; expected=['dim_products.name']; missing=[]
