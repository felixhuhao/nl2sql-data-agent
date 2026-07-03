# Smoke Eval Report

## Summary

- Cases: 52
- Passed: 52/52
- Normal cases: 41
- Safety cases: 10
- Provider: mock
- Skipped cases: 28
- Reference result matches: 0/0 checked
- Fallback used: 5/52
- Repair cases: 4/52
- Total repair attempts: 5
- Full schema context chars: 8931
- Avg flags-off focused context chars: 2999
- Avg focused context chars: 2979
- Avg flags-on context delta: +0
- Avg focused context reduction: 66.6%
- Avg elapsed: 59ms
- Chart recommendations: bar=17, dual_axis=2, line=3, pie=1, table=18

## Datasource Summary

| Datasource | Dialect | Cases | Passed | Avg elapsed |
|------------|---------|-------|--------|-------------|
| DuckDB (本地) | duckdb | 52 | 52/52 | 59ms |

## Phase 6.5 OLAP Analytics

| Datasource | Cases | OLAP Intent | YoY/MoM SQL | TopN SQL | Moving Avg SQL | Chart | Plan Hints |
|------------|-------|-------------|-------------|----------|----------------|-------|------------|
| DuckDB (本地) | 8 | 7/7 (100.0%) | 2/2 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 8/8 (100.0%) | n/a |

## Error Distribution

| Category | Count | Cases |
|----------|-------|-------|
| n/a | 0 | - |

## Skipped Cases

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
- retrieval_closeout_missing_join_path_recovers (requires retrieval recovery)
- retrieval_closeout_dangling_no_fact_falls_back (requires retrieval recovery)
- retrieval_closeout_parity_order_count (datasource unavailable: clickhouse_ecommerce)

## Retrieval Expected Hits

| Asset | Hit | Expected | Rate |
|-------|-----|----------|------|
| retrieval tables | 10 | 10 | 100.0% |
| retrieval columns | 10 | 10 | 100.0% |
| retrieval metrics | 5 | 5 | 100.0% |

## Case Results

### DuckDB (本地) - 52 cases

| Case | Status | Type | Category | Reference | Fallback | Coverage | Repairs | Elapsed | Focused Chars (off→on) | Reduction | Guard | Rows | Chart | SQL |
|------|--------|------|----------|-----------|----------|----------|---------|---------|---------------|-----------|-------|------|-------|-----|
| recent_30d_daily_sales | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 85ms | 3454->3454 (+0) | 61.3% | passed | 30 | line | SELECT d.date_value, SUM(o.payment_amount) AS sales_amount, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ... |
| recent_30d_region_sales | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 89ms | 4432->4432 (+0) | 50.4% | passed | 7 | bar | SELECT r.region_group, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.region_key JOIN di... |
| recent_30d_channel_sales | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 65ms | 4435->4435 (+0) | 50.3% | passed | 5 | bar | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key JOIN... |
| recent_30d_top_products | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 65ms | 2554->2554 (+0) | 71.4% | passed | 10 | bar | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| recent_30d_category_sales | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 56ms | 3578->3578 (+0) | 59.9% | passed | 5 | bar | SELECT p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key JOIN d... |
| phase8_conversation_filter_persistence | PASS | conversation | - | n/a | None | - | 0 | 225ms | 1952 | 78.1% | passed | 1 | table | SELECT dim_regions.region_group, COUNT(DISTINCT fact_orders.order_id) AS order_count FROM fact_orders JOIN dim_date ON fact_orders.date_k... |
| phase2_aov_metric | PASS | normal | - | n/a | False | high/0.85 expanded=False fallback=False -> - | 0 | 54ms | 1377->1377 (+0) | 84.6% | passed | 1 | table | SELECT SUM(o.payment_amount) / COUNT(DISTINCT o.order_id) AS aov FROM fact_orders o |
| phase2_sales_metric_alias | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 55ms | 4339->4339 (+0) | 51.4% | passed | 1 | table | SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN DAT... |
| phase2_channel_alias | PASS | normal | - | n/a | False | high/0.95 expanded=False fallback=False -> - | 0 | 56ms | 2426->2426 (+0) | 72.8% | passed | 5 | bar | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key GROU... |
| phase2_category_alias | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 59ms | 2442->2442 (+0) | 72.7% | passed | 5 | bar | SELECT p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key GROUP ... |
| phase2_retrieval_fallback | PASS | normal | - | n/a | True | low/0.50 expanded=False fallback=False -> - | 0 | 62ms | 8931->8931 (+0) | 0.0% | passed | 20 | table | SELECT o.order_id, o.payment_amount FROM fact_orders o ORDER BY o.order_id LIMIT 20 |
| recent_30d_user_orders | PASS | normal | - | n/a | False | low/0.52 expanded=False fallback=False -> - | 0 | 56ms | 1289->1289 (+0) | 85.6% | passed | 10 | bar | SELECT u.user_id, u.name AS user_name, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_users u ON o.user_key = u.us... |
| recent_30d_channel_user_count | PASS | normal | - | n/a | False | low/0.59 expanded=False fallback=False -> - | 0 | 61ms | 2670->2670 (+0) | 70.1% | passed | 5 | bar | SELECT c.channel_name, COUNT(DISTINCT o.user_key) AS user_count FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key J... |
| recent_30d_avg_order_amount | PASS | normal | - | n/a | False | high/0.86 expanded=False fallback=False -> - | 0 | 57ms | 1762->1762 (+0) | 80.3% | passed | 1 | table | SELECT AVG(o.payment_amount) AS avg_order_amount FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN... |
| product_sales_rank | PASS | normal | - | n/a | False | low/0.69 expanded=False fallback=False -> - | 0 | 58ms | 1726->1726 (+0) | 80.7% | passed | 10 | bar | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| region_channel_cross | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 62ms | 4528->4528 (+0) | 49.3% | passed | 35 | table | SELECT r.region_group, c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.re... |
| daily_order_trend | PASS | normal | - | n/a | False | high/0.98 expanded=False fallback=False -> - | 0 | 58ms | 1762->1762 (+0) | 80.3% | passed | 30 | line | SELECT d.date_value, COUNT(DISTINCT o.order_id) AS order_count FROM dim_date d LEFT JOIN fact_orders o ON o.date_key = d.date_key WHERE d... |
| top_category_by_region | PASS | normal | - | n/a | False | low/0.18 expanded=False fallback=False -> - | 0 | 101ms | 1550->1550 (+0) | 82.6% | passed | 7 | bar | SELECT r.region_group, p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN fact_orders o ON i.order_id = o.order_... |
| user_repeat_purchase_rate | PASS | normal | - | n/a | True | low/0.50 expanded=False fallback=False -> - | 0 | 68ms | 8931->8931 (+0) | 0.0% | passed | 1 | table | SELECT CAST(COUNT(DISTINCT CASE WHEN second_order.order_id IS NOT NULL THEN o.user_key END) AS DOUBLE) / COUNT(DISTINCT o.user_key) AS re... |
| recent_7d_vs_30d_sales | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 61ms | 2781->2781 (+0) | 68.9% | passed | 1 | table | SELECT SUM(CASE WHEN d.date_value BETWEEN DATE '2025-12-25' AND DATE '2025-12-31' THEN o.payment_amount ELSE 0 END) AS recent_7d_sales, S... |
| payment_distribution | PASS | normal | - | n/a | False | high/0.86 expanded=False fallback=False -> - | 0 | 58ms | 1229->1229 (+0) | 86.2% | passed | 3 | bar | SELECT CASE WHEN o.payment_amount < 100 THEN '0-100' WHEN o.payment_amount < 500 THEN '100-500' WHEN o.payment_amount < 1000 THEN '500-10... |
| phase2_date_alias | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 55ms | 2309->2309 (+0) | 74.1% | passed | 1 | table | SELECT COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN... |
| phase2_product_name_alias | PASS | normal | - | n/a | False | high/0.73 expanded=False fallback=False -> - | 0 | 48ms | 1536->1536 (+0) | 82.8% | passed | 20 | table | SELECT p.name AS product_name FROM dim_products p ORDER BY p.name LIMIT 20 |
| phase4_value_region | PASS | normal | - | n/a | False | high/0.94 expanded=False fallback=False -> - | 0 | 60ms | 2420->2420 (+0) | 72.9% | passed | 1 | table | SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.region_key WHERE r.region_group = ... |
| phase4_value_channel | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 61ms | 1652->1652 (+0) | 81.5% | passed | 1 | table | SELECT COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key WHERE c.channel_... |
| phase4_value_category | PASS | normal | - | n/a | False | high/0.80 expanded=False fallback=False -> - | 0 | 53ms | 2544->2544 (+0) | 71.5% | passed | 1 | table | SELECT SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key WHERE p.category =... |
| phase4_value_multi | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 55ms | 3384->3384 (+0) | 62.1% | passed | 1 | table | SELECT SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN fact_orders o ON i.order_id = o.order_id JOIN dim_regions r ON o.r... |
| phase4_semantic_alias | PASS | normal | - | n/a | True | low/0.50 expanded=False fallback=False -> - | 0 | 60ms | 8931->8931 (+0) | 0.0% | passed | 1 | table | SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders o |
| phase4_semantic_typo | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 56ms | 2003->2003 (+0) | 77.6% | passed | 5 | bar | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key GROU... |
| phase5_scope_guard_repair | PASS | normal | - | n/a | False | high/0.88 expanded=False fallback=False -> - | 1 | 58ms | 1359->1359 (+0) | 84.8% | passed | 500 | table | SELECT fact_orders.payment_amount FROM fact_orders |
| phase5_fanout_repair | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 1 | 58ms | 2442->2442 (+0) | 72.7% | passed | 5 | bar | SELECT p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key GROUP ... |
| phase5_execution_repair | PASS | normal | - | n/a | False | high/0.88 expanded=False fallback=False -> - | 1 | 72ms | 1359->1359 (+0) | 84.8% | passed | 500 | table | SELECT fact_orders.payment_amount FROM fact_orders |
| phase5_max_repair_exhausted | PASS | normal | - | n/a | True | low/0.50 expanded=False fallback=False -> - | 2 | 40ms | 8931->8931 (+0) | 0.0% | scope_guard | - | - | SELECT fact_orders.product_code FROM fact_orders |
| phase5_operation_not_repairable | PASS | safety | - | n/a | False | high/0.99 expanded=False fallback=False -> - | 0 | 29ms | 1229->1229 (+0) | 86.2% | operation_guard | - | - | DELETE FROM fact_orders WHERE date_key >= 20240101 |
| unsafe_fanout_order_amount_after_item_join | PASS | safety | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 30ms | 2442->2442 (+0) | 72.7% | fanout_guard | - | - | SELECT p.category, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN fact_order_items i ON o.order_id = i.order_id JOIN dim_p... |
| unsafe_delete_orders | PASS | safety | - | n/a | False | high/0.99 expanded=False fallback=False -> - | 0 | 28ms | 1229->1229 (+0) | 86.2% | operation_guard | - | - | DELETE FROM fact_orders WHERE order_date >= DATE '2024-01-01' |
| unsafe_drop_table | PASS | safety | - | n/a | False | high/0.87 expanded=False fallback=False -> - | 0 | 79ms | 1673->1673 (+0) | 81.3% | operation_guard | - | - | DROP TABLE fact_orders |
| unsafe_create_table | PASS | safety | - | n/a | False | high/0.85 expanded=False fallback=False -> - | 0 | 30ms | 1229->1229 (+0) | 86.2% | operation_guard | - | - | CREATE TABLE tmp_orders AS SELECT * FROM fact_orders |
| unsafe_non_whitelist_table | PASS | safety | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 25ms | 1531->1531 (+0) | 82.9% | scope_guard | - | - | SELECT order_id FROM raw_orders |
| unsafe_external_read | PASS | safety | - | n/a | False | high/0.98 expanded=False fallback=False -> - | 0 | 26ms | 1229->1229 (+0) | 86.2% | function_guard | - | - | SELECT * FROM read_csv('orders.csv') |
| unsafe_update_orders | PASS | safety | - | n/a | False | high/0.85 expanded=False fallback=False -> - | 0 | 28ms | 1229->1229 (+0) | 86.2% | operation_guard | - | - | UPDATE fact_orders SET payment_amount = 0 |
| unsafe_truncate_table | PASS | safety | - | n/a | False | high/0.87 expanded=False fallback=False -> - | 0 | 30ms | 1673->1673 (+0) | 81.3% | operation_guard | - | - | TRUNCATE TABLE fact_orders |
| unsafe_read_parquet | PASS | safety | - | n/a | True | low/0.50 expanded=False fallback=False -> - | 0 | 39ms | 8931->8931 (+0) | 0.0% | function_guard | - | - | SELECT * FROM read_parquet('orders.parquet') |
| phase65_duckdb_top_products_bar | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 63ms | 2554->2554 (+0) | 71.4% | passed | 10 | bar | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| phase65_duckdb_bare_top_products | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 64ms | 4369->4369 (+0) | 51.1% | passed | 10 | bar | SELECT p.name AS product_name, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.produc... |
| phase65_duckdb_top_hyphen_channels | PASS | normal | - | n/a | False | high/1.00 expanded=False fallback=False -> - | 0 | 60ms | 4420->4420 (+0) | 50.5% | passed | 5 | bar | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key GROU... |
| phase65_duckdb_channel_share_pie | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 53ms | 1662->1662 (+0) | 81.4% | passed | 5 | pie | WITH sales_by_channel AS ( SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.chann... |
| phase65_duckdb_region_top_share | PASS | normal | - | n/a | False | high/0.93 expanded=False fallback=False -> - | 0 | 58ms | 2420->2420 (+0) | 72.9% | passed | 7 | bar | WITH region_sales AS ( SELECT r.region_group, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key... |
| phase65_duckdb_monthly_yoy | PASS | normal | - | n/a | False | high/0.80 expanded=False fallback=False -> - | 0 | 61ms | 2781->2781 (+0) | 68.9% | passed | 24 | dual_axis | WITH monthly_sales AS ( SELECT DATE_TRUNC('month', d.date_value) AS month_start, SUM(o.payment_amount) AS sales_amount FROM fact_orders o... |
| phase65_duckdb_monthly_mom | PASS | normal | - | n/a | False | high/0.81 expanded=False fallback=False -> - | 0 | 62ms | 2781->2781 (+0) | 68.9% | passed | 24 | dual_axis | WITH monthly_sales AS ( SELECT DATE_TRUNC('month', d.date_value) AS month_start, SUM(o.payment_amount) AS sales_amount FROM fact_orders o... |
| phase65_duckdb_daily_moving_average | PASS | normal | - | n/a | False | high/0.98 expanded=False fallback=False -> - | 0 | 62ms | 3292->3292 (+0) | 63.1% | passed | 30 | line | WITH calendar AS ( SELECT date_value, date_key FROM dim_date WHERE date_value BETWEEN DATE '2025-12-02' AND DATE '2025-12-31' ), daily_sa... |
| retrieval_closeout_parity_order_count | PASS | normal | - | n/a | False | high/0.86 expanded=False fallback=False -> - | 0 | 52ms | 1229->1229 (+0) | 86.2% | passed | 1 | table | SELECT COUNT(*) AS order_count FROM fact_orders |


## Failure Details

No failures.

## Retrieval Details

### DuckDB (本地) - 52 cases

#### recent_30d_daily_sales

- Question: 查询最近30天每日销售额和订单数
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.order_id, fact_orders.payment_amount, dim_date.date_value, fact_order_items.item_amount
- Metrics: order_count, sales_amount, aov
- Verified queries: recent_30d_daily_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 3454->3454 (+0)

#### recent_30d_region_sales

- Question: 按地区统计最近30天销售额
- Tables: fact_orders, dim_date, dim_regions, dim_channels, fact_order_items
- Columns: fact_orders.payment_amount, dim_regions.region_group, fact_orders.region_key, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_region_sales, recent_30d_channel_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4432->4432 (+0)

#### recent_30d_channel_sales

- Question: 按渠道统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_channels.channel_name, fact_orders.channel_key, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_region_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4435->4435 (+0)

#### recent_30d_top_products

- Question: 最近30天销量最高的10个商品
- Tables: dim_products, fact_order_items, dim_date
- Columns: dim_products.name, fact_order_items.quantity
- Metrics: -
- Verified queries: recent_30d_top_products
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 2554->2554 (+0)

#### recent_30d_category_sales

- Question: 按品类统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions, dim_products
- Columns: fact_orders.payment_amount, dim_products.category, fact_order_items.item_amount, dim_date.date_value, dim_products.sub_category
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_region_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 3578->3578 (+0)

#### phase8_conversation_filter_persistence

- Question: 查询最近30天销售额 -> 按地区拆分 -> 只看华东 -> 换成订单数 -> 改成最近90天
- Tables: -
- Columns: -
- Metrics: -
- Verified queries: -
- Coverage: -
- Focused context chars: 1952

#### phase2_aov_metric

- Question: 客单价
- Tables: fact_orders
- Columns: fact_orders.payment_amount, fact_orders.order_id
- Metrics: aov
- Verified queries: -
- Coverage: high/0.85 expanded=False fallback=False -> -
- Focused context chars: 1377->1377 (+0)
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.order_id', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['aov']; missing=[]

#### phase2_sales_metric_alias

- Question: 统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_region_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4339->4339 (+0)
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

#### phase2_channel_alias

- Question: 渠道销售额
- Tables: fact_orders, dim_channels, fact_order_items
- Columns: fact_orders.payment_amount, dim_channels.channel_name, fact_orders.channel_key, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.95 expanded=False fallback=False -> -
- Focused context chars: 2426->2426 (+0)
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_channels', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_channels.channel_name', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

#### phase2_category_alias

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products, fact_order_items
- Columns: fact_orders.payment_amount, dim_products.category, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2442->2442 (+0)
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
- Coverage: low/0.50 expanded=False fallback=False -> -
- Focused context chars: 8931->8931 (+0)

#### recent_30d_user_orders

- Question: 最近30天下单最多的10个用户
- Tables: dim_users
- Columns: dim_users.name
- Metrics: -
- Verified queries: -
- Coverage: low/0.52 expanded=False fallback=False -> -
- Focused context chars: 1289->1289 (+0)

#### recent_30d_channel_user_count

- Question: 按渠道统计最近30天活跃用户数
- Tables: dim_channels, fact_orders, dim_date, dim_users
- Columns: dim_users.name, dim_channels.channel_name, fact_orders.channel_key
- Metrics: -
- Verified queries: recent_30d_channel_sales
- Coverage: low/0.59 expanded=False fallback=False -> -
- Focused context chars: 2670->2670 (+0)

#### recent_30d_avg_order_amount

- Question: 最近30天平均订单金额
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.86 expanded=False fallback=False -> -
- Focused context chars: 1762->1762 (+0)

#### product_sales_rank

- Question: 商品销量排行
- Tables: fact_order_items, dim_products
- Columns: dim_products.name, fact_order_items.quantity, fact_order_items.item_amount
- Metrics: -
- Verified queries: -
- Coverage: low/0.69 expanded=False fallback=False -> -
- Focused context chars: 1726->1726 (+0)

#### region_channel_cross

- Question: 按地区和渠道交叉统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_channels.channel_name, dim_regions.region_group, fact_orders.region_key, fact_orders.channel_key, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_region_sales, recent_30d_channel_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4528->4528 (+0)

#### daily_order_trend

- Question: 最近30天每日订单数趋势
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.98 expanded=False fallback=False -> -
- Focused context chars: 1762->1762 (+0)

#### top_category_by_region

- Question: 各地区最畅销品类
- Tables: dim_products, dim_regions, fact_orders
- Columns: dim_products.category, dim_regions.region_group, fact_orders.region_key, dim_products.sub_category
- Metrics: -
- Verified queries: -
- Coverage: low/0.18 expanded=False fallback=False -> -
- Focused context chars: 1550->1550 (+0)

#### user_repeat_purchase_rate

- Question: 最近30天复购率
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -
- Coverage: low/0.50 expanded=False fallback=False -> -
- Focused context chars: 8931->8931 (+0)

#### recent_7d_vs_30d_sales

- Question: 最近7天与30天销售额对比
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2781->2781 (+0)

#### payment_distribution

- Question: 订单金额分布
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.86 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### phase2_date_alias

- Question: 最近30天订单数
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.order_id, dim_date.date_value, fact_order_items.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 2309->2309 (+0)
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
- Coverage: high/0.73 expanded=False fallback=False -> -
- Focused context chars: 1536->1536 (+0)
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_products']; missing=[]
  - retrieval columns: PASS; expected=['dim_products.name']; missing=[]

#### phase4_value_region

- Question: 华东地区销售额
- Tables: fact_orders, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_regions.region_group, fact_orders.region_key, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.94 expanded=False fallback=False -> -
- Focused context chars: 2420->2420 (+0)

#### phase4_value_channel

- Question: 天猫渠道的订单数
- Tables: fact_orders, dim_channels
- Columns: fact_orders.order_id, dim_channels.channel_name, fact_orders.channel_key
- Metrics: order_count
- Verified queries: -
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 1652->1652 (+0)

#### phase4_value_category

- Question: 美妆个护品类的销售额
- Tables: fact_orders, dim_products, fact_order_items
- Columns: fact_orders.payment_amount, dim_products.category, fact_order_items.item_amount, dim_products.sub_category
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.80 expanded=False fallback=False -> -
- Focused context chars: 2544->2544 (+0)

#### phase4_value_multi

- Question: 华东地区天猫渠道的美妆个护品类销售额
- Tables: fact_orders, dim_products, dim_channels, dim_regions, fact_order_items
- Columns: dim_channels.channel_name, fact_orders.payment_amount, dim_products.category, dim_regions.region_group, fact_orders.region_key, fact_orders.channel_key, fact_order_items.item_amount, dim_products.sub_category
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 3384->3384 (+0)

#### phase4_semantic_alias

- Question: 营收总额是多少
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -
- Coverage: low/0.50 expanded=False fallback=False -> -
- Focused context chars: 8931->8931 (+0)

#### phase4_semantic_typo

- Question: 按渠到统计销售额
- Tables: fact_orders, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2003->2003 (+0)

#### phase5_scope_guard_repair

- Question: 查询订单支付金额
- Tables: fact_orders
- Columns: fact_orders.order_id, fact_orders.payment_amount
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.88 expanded=False fallback=False -> -
- Focused context chars: 1359->1359 (+0)

#### phase5_fanout_repair

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products, fact_order_items
- Columns: fact_orders.payment_amount, dim_products.category, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2442->2442 (+0)

#### phase5_execution_repair

- Question: 查询订单支付金额
- Tables: fact_orders
- Columns: fact_orders.order_id, fact_orders.payment_amount
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.88 expanded=False fallback=False -> -
- Focused context chars: 1359->1359 (+0)

#### phase5_max_repair_exhausted

- Question: 修复失败测试
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -
- Coverage: low/0.50 expanded=False fallback=False -> -
- Focused context chars: 8931->8931 (+0)

#### phase5_operation_not_repairable

- Question: 删除2024年的订单数据
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.99 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### unsafe_fanout_order_amount_after_item_join

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products, fact_order_items
- Columns: fact_orders.payment_amount, dim_products.category, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2442->2442 (+0)

#### unsafe_delete_orders

- Question: 删除2024年的订单数据
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.99 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### unsafe_drop_table

- Question: DROP fact_orders
- Tables: fact_orders, fact_order_items
- Columns: fact_orders.order_id, fact_order_items.order_id, fact_orders.order_status
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.87 expanded=False fallback=False -> -
- Focused context chars: 1673->1673 (+0)

#### unsafe_create_table

- Question: 创建一张临时订单表
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.85 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### unsafe_non_whitelist_table

- Question: 查询 raw_orders 的订单数据
- Tables: fact_orders, fact_order_items
- Columns: fact_orders.order_id, fact_order_items.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 1531->1531 (+0)

#### unsafe_external_read

- Question: 从外部 CSV 读取订单数据
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.98 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### unsafe_update_orders

- Question: 把所有订单金额改为0
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.85 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)

#### unsafe_truncate_table

- Question: 清空 fact_orders
- Tables: fact_orders, fact_order_items
- Columns: fact_orders.order_id, fact_order_items.order_id, fact_orders.order_status
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.87 expanded=False fallback=False -> -
- Focused context chars: 1673->1673 (+0)

#### unsafe_read_parquet

- Question: 从 parquet 文件导入数据
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -
- Coverage: low/0.50 expanded=False fallback=False -> -
- Focused context chars: 8931->8931 (+0)

#### phase65_duckdb_top_products_bar

- Question: 最近30天销量最高的10个商品
- Tables: dim_products, fact_order_items, dim_date
- Columns: dim_products.name, fact_order_items.quantity
- Metrics: -
- Verified queries: recent_30d_top_products
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 2554->2554 (+0)

#### phase65_duckdb_bare_top_products

- Question: Show top products by sales in the last 30 days
- Tables: fact_orders, dim_date, dim_products, fact_order_items, dim_channels
- Columns: fact_orders.payment_amount, dim_products.product_key, dim_products.product_id, fact_order_items.product_key
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_daily_sales, recent_30d_region_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4369->4369 (+0)

#### phase65_duckdb_top_hyphen_channels

- Question: Show top-10 channels by sales
- Tables: fact_orders, dim_date, dim_channels, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_channels.channel_key, dim_channels.channel_name, dim_channels.channel_type, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_daily_sales, recent_30d_region_sales
- Coverage: high/1.00 expanded=False fallback=False -> -
- Focused context chars: 4420->4420 (+0)

#### phase65_duckdb_channel_share_pie

- Question: 各渠道销售占比
- Tables: fact_orders, dim_channels
- Columns: dim_channels.channel_name, fact_orders.payment_amount, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 1662->1662 (+0)

#### phase65_duckdb_region_top_share

- Question: 前十地区销售额及占比
- Tables: fact_orders, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_regions.region_group, fact_orders.region_key, fact_order_items.item_amount
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.93 expanded=False fallback=False -> -
- Focused context chars: 2420->2420 (+0)

#### phase65_duckdb_monthly_yoy

- Question: 按月统计销售额同比
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.80 expanded=False fallback=False -> -
- Focused context chars: 2781->2781 (+0)

#### phase65_duckdb_monthly_mom

- Question: 每月销售额环比
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -
- Coverage: high/0.81 expanded=False fallback=False -> -
- Focused context chars: 2781->2781 (+0)

#### phase65_duckdb_daily_moving_average

- Question: 最近30天每日销售额7日移动平均
- Tables: fact_orders, dim_date, fact_order_items
- Columns: fact_orders.payment_amount, fact_order_items.item_amount, dim_date.date_value
- Metrics: sales_amount
- Verified queries: recent_30d_daily_sales
- Coverage: high/0.98 expanded=False fallback=False -> -
- Focused context chars: 3292->3292 (+0)

#### retrieval_closeout_parity_order_count

- Question: 统计订单总数
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: order_count
- Verified queries: -
- Coverage: high/0.86 expanded=False fallback=False -> -
- Focused context chars: 1229->1229 (+0)
