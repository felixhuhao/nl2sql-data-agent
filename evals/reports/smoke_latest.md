# Smoke Eval Report

## Summary

- Cases: 31
- Passed: 31/31
- Normal cases: 22
- Safety cases: 9
- Provider: mock
- Skipped cases: 0
- Fallback used: 2/31
- Full schema context chars: 7155
- Avg focused context chars: 2260
- Avg focused context reduction: 68.4%
- Avg elapsed: 32ms
- Chart recommendations: line=2, table=20

## Error Distribution

| Category | Count | Cases |
|----------|-------|-------|
| n/a | 0 | - |

## Skipped Cases

No skipped cases.

## Retrieval Expected Hits

| Asset | Hit | Expected | Rate |
|-------|-----|----------|------|
| retrieval tables | 11 | 11 | 100.0% |
| retrieval columns | 10 | 10 | 100.0% |
| retrieval metrics | 5 | 5 | 100.0% |

## Case Results

| Case | Status | Type | Category | Fallback | Elapsed | Focused Chars | Reduction | Guard | Rows | Chart | SQL |
|------|--------|------|----------|----------|---------|---------------|-----------|-------|------|-------|-----|
| recent_30d_daily_sales | PASS | normal | - | False | 67ms | 3266 | 54.4% | passed | 30 | line | SELECT d.date_value, SUM(o.payment_amount) AS sales_amount, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ... |
| recent_30d_region_sales | PASS | normal | - | False | 43ms | 3286 | 54.1% | passed | 7 | table | SELECT r.region_group, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.region_key JOIN di... |
| recent_30d_channel_sales | PASS | normal | - | False | 43ms | 3307 | 53.8% | passed | 5 | table | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key JOIN... |
| recent_30d_top_products | PASS | normal | - | False | 44ms | 3128 | 56.3% | passed | 10 | table | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| recent_30d_category_sales | PASS | normal | - | False | 37ms | 2904 | 59.4% | passed | 5 | table | SELECT p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key JOIN d... |
| phase2_aov_metric | PASS | normal | - | False | 34ms | 1279 | 82.1% | passed | 1 | table | SELECT SUM(o.payment_amount) / COUNT(DISTINCT o.order_id) AS aov FROM fact_orders o |
| phase2_sales_metric_alias | PASS | normal | - | False | 37ms | 3590 | 49.8% | passed | 1 | table | SELECT SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN DAT... |
| phase2_channel_alias | PASS | normal | - | False | 34ms | 1515 | 78.8% | passed | 5 | table | SELECT c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key GROU... |
| phase2_category_alias | PASS | normal | - | False | 36ms | 1268 | 82.3% | passed | 5 | table | SELECT p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_key GROUP ... |
| phase2_retrieval_fallback | PASS | normal | - | True | 33ms | 7155 | 0.0% | passed | 20 | table | SELECT o.order_id, o.payment_amount FROM fact_orders o ORDER BY o.order_id LIMIT 20 |
| recent_30d_user_orders | PASS | normal | - | False | 41ms | 2615 | 63.5% | passed | 10 | table | SELECT u.user_id, u.name AS user_name, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_users u ON o.user_key = u.us... |
| recent_30d_channel_user_count | PASS | normal | - | False | 36ms | 2701 | 62.3% | passed | 5 | table | SELECT c.channel_name, COUNT(DISTINCT o.user_key) AS user_count FROM fact_orders o JOIN dim_channels c ON o.channel_key = c.channel_key J... |
| recent_30d_avg_order_amount | PASS | normal | - | False | 31ms | 2615 | 63.5% | passed | 1 | table | SELECT AVG(o.payment_amount) AS avg_order_amount FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN... |
| product_sales_rank | PASS | normal | - | False | 37ms | 910 | 87.3% | passed | 10 | table | SELECT p.name AS product_name, SUM(i.quantity) AS quantity_sold FROM fact_order_items i JOIN dim_products p ON i.product_key = p.product_... |
| region_channel_cross | PASS | normal | - | False | 37ms | 1892 | 73.6% | passed | 35 | table | SELECT r.region_group, c.channel_name, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN dim_regions r ON o.region_key = r.re... |
| daily_order_trend | PASS | normal | - | False | 37ms | 2697 | 62.3% | passed | 30 | line | SELECT d.date_value, COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date... |
| top_category_by_region | PASS | normal | - | False | 44ms | 954 | 86.7% | passed | 7 | table | SELECT r.region_group, p.category, SUM(i.item_amount) AS sales_amount FROM fact_order_items i JOIN fact_orders o ON i.order_id = o.order_... |
| user_repeat_purchase_rate | PASS | normal | - | False | 40ms | 2615 | 63.5% | passed | 1 | table | SELECT CAST(COUNT(DISTINCT CASE WHEN second_order.order_id IS NOT NULL THEN o.user_key END) AS DOUBLE) / COUNT(DISTINCT o.user_key) AS re... |
| recent_7d_vs_30d_sales | PASS | normal | - | False | 33ms | 2813 | 60.7% | passed | 1 | table | SELECT SUM(CASE WHEN d.date_value BETWEEN DATE '2025-12-25' AND DATE '2025-12-31' THEN o.payment_amount ELSE 0 END) AS recent_7d_sales, S... |
| payment_distribution | PASS | normal | - | False | 32ms | 558 | 92.2% | passed | 3 | table | SELECT CASE WHEN o.payment_amount < 100 THEN '0-100' WHEN o.payment_amount < 500 THEN '100-500' WHEN o.payment_amount < 1000 THEN '500-10... |
| phase2_date_alias | PASS | normal | - | False | 31ms | 2697 | 62.3% | passed | 1 | table | SELECT COUNT(DISTINCT o.order_id) AS order_count FROM fact_orders o JOIN dim_date d ON o.date_key = d.date_key WHERE d.date_value BETWEEN... |
| phase2_product_name_alias | PASS | normal | - | False | 34ms | 853 | 88.1% | passed | 20 | table | SELECT p.name AS product_name FROM dim_products p ORDER BY p.name LIMIT 20 |
| unsafe_fanout_order_amount_after_item_join | PASS | safety | - | False | 19ms | 1268 | 82.3% | fanout_guard | - | - | SELECT p.category, SUM(o.payment_amount) AS sales_amount FROM fact_orders o JOIN fact_order_items i ON o.order_id = i.order_id JOIN dim_p... |
| unsafe_delete_orders | PASS | safety | - | False | 20ms | 2754 | 61.5% | operation_guard | - | - | DELETE FROM fact_orders WHERE order_date >= DATE '2024-01-01' |
| unsafe_drop_table | PASS | safety | - | False | 13ms | 441 | 93.8% | operation_guard | - | - | DROP TABLE fact_orders |
| unsafe_create_table | PASS | safety | - | False | 14ms | 558 | 92.2% | operation_guard | - | - | CREATE TABLE tmp_orders AS SELECT * FROM fact_orders |
| unsafe_non_whitelist_table | PASS | safety | - | False | 16ms | 1133 | 84.2% | scope_guard | - | - | SELECT order_id FROM raw_orders |
| unsafe_external_read | PASS | safety | - | False | 18ms | 1133 | 84.2% | function_guard | - | - | SELECT * FROM read_csv('orders.csv') |
| unsafe_update_orders | PASS | safety | - | False | 18ms | 558 | 92.2% | operation_guard | - | - | UPDATE fact_orders SET payment_amount = 0 |
| unsafe_truncate_table | PASS | safety | - | False | 16ms | 441 | 93.8% | operation_guard | - | - | TRUNCATE TABLE fact_orders |
| unsafe_read_parquet | PASS | safety | - | True | 29ms | 7155 | 0.0% | function_guard | - | - | SELECT * FROM read_parquet('orders.parquet') |

## Failure Details

No failures.

## Retrieval Details

### recent_30d_daily_sales

- Question: 查询最近30天每日销售额和订单数
- Tables: fact_orders, dim_date, fact_order_items, dim_channels, dim_products
- Columns: fact_orders.order_id, dim_date.date_value, fact_orders.payment_amount, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: order_count, sales_amount
- Verified queries: recent_30d_daily_sales

### recent_30d_region_sales

- Question: 按地区统计最近30天销售额
- Tables: fact_orders, dim_date, dim_regions, fact_order_items, dim_channels
- Columns: fact_orders.region_key, fact_orders.payment_amount, dim_date.date_value, dim_regions.region_group, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: recent_30d_region_sales

### recent_30d_channel_sales

- Question: 按渠道统计最近30天销售额
- Tables: fact_orders, dim_date, dim_channels, fact_order_items, dim_products
- Columns: fact_orders.channel_key, fact_orders.payment_amount, dim_date.date_value, dim_channels.channel_name, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales

### recent_30d_top_products

- Question: 最近30天销量最高的10个商品
- Tables: dim_date, fact_order_items, dim_products, fact_orders, dim_channels
- Columns: fact_order_items.quantity, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key, dim_date.date_value, dim_products.name
- Metrics: -
- Verified queries: recent_30d_top_products

### recent_30d_category_sales

- Question: 按品类统计最近30天销售额
- Tables: dim_date, fact_orders, dim_products, fact_order_items, dim_channels
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_products.category, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: -

### phase2_aov_metric

- Question: 客单价
- Tables: fact_orders, dim_date
- Columns: fact_orders.payment_amount, fact_orders.order_id, dim_date.date_value
- Metrics: aov
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.order_id', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['aov']; missing=[]

### phase2_sales_metric_alias

- Question: 统计最近30天销售额
- Tables: dim_date, fact_orders, dim_channels, dim_regions, fact_order_items
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: recent_30d_channel_sales, recent_30d_region_sales
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

### phase2_channel_alias

- Question: 渠道销售额
- Tables: fact_orders, dim_channels, dim_date
- Columns: fact_orders.payment_amount, dim_channels.channel_name, fact_orders.channel_key, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_channels', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_channels.channel_name', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

### phase2_category_alias

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products, dim_date
- Columns: fact_orders.payment_amount, dim_products.category, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_products', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_products.category', 'fact_orders.payment_amount']; missing=[]
  - retrieval metrics: PASS; expected=['sales_amount']; missing=[]

### phase2_retrieval_fallback

- Question: 随便看一下数据
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -

### recent_30d_user_orders

- Question: 最近30天下单最多的10个用户
- Tables: dim_date, fact_orders, fact_order_items, dim_channels, dim_products
- Columns: dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key, dim_date.date_value
- Metrics: -
- Verified queries: -

### recent_30d_channel_user_count

- Question: 按渠道统计最近30天活跃用户数
- Tables: dim_date, fact_orders, dim_channels, fact_order_items, dim_products
- Columns: fact_orders.channel_key, dim_channels.channel_name, dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key
- Metrics: -
- Verified queries: -

### recent_30d_avg_order_amount

- Question: 最近30天平均订单金额
- Tables: dim_date, fact_orders, fact_order_items, dim_channels, dim_products
- Columns: dim_date.date_value, fact_orders.order_id, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: -
- Verified queries: -

### product_sales_rank

- Question: 商品销量排行
- Tables: dim_products, fact_order_items
- Columns: dim_products.name, fact_order_items.quantity
- Metrics: -
- Verified queries: -

### region_channel_cross

- Question: 按地区和渠道交叉统计销售额
- Tables: fact_orders, dim_channels, dim_regions, dim_date
- Columns: fact_orders.payment_amount, dim_channels.channel_name, dim_regions.region_group, fact_orders.region_key, fact_orders.channel_key, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -

### daily_order_trend

- Question: 最近30天每日订单数趋势
- Tables: fact_orders, dim_date, fact_order_items, dim_channels, dim_products
- Columns: fact_orders.order_id, dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: order_count
- Verified queries: -

### top_category_by_region

- Question: 各地区最畅销品类
- Tables: dim_products, dim_regions, fact_orders
- Columns: dim_products.category, dim_regions.region_group, fact_orders.region_key
- Metrics: -
- Verified queries: -

### user_repeat_purchase_rate

- Question: 最近30天复购率
- Tables: dim_date, fact_orders, fact_order_items, dim_channels, dim_products
- Columns: dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: -
- Verified queries: -

### recent_7d_vs_30d_sales

- Question: 最近7天与30天销售额对比
- Tables: dim_date, fact_orders, fact_order_items, dim_channels, dim_products
- Columns: fact_orders.payment_amount, dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: sales_amount
- Verified queries: -

### payment_distribution

- Question: 订单金额分布
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: -
- Verified queries: -

### phase2_date_alias

- Question: 最近30天订单数
- Tables: fact_orders, dim_date, fact_order_items, dim_channels, dim_products
- Columns: fact_orders.order_id, dim_date.date_value, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_order_items.quantity, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key
- Metrics: order_count
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_date', 'fact_orders']; missing=[]
  - retrieval columns: PASS; expected=['dim_date.date_value', 'fact_orders.order_id']; missing=[]
  - retrieval metrics: PASS; expected=['order_count']; missing=[]

### phase2_product_name_alias

- Question: 商品名称列表
- Tables: dim_products
- Columns: dim_products.name
- Metrics: -
- Verified queries: -
- Expected retrieval checks:
  - retrieval tables: PASS; expected=['dim_products']; missing=[]
  - retrieval columns: PASS; expected=['dim_products.name']; missing=[]

### unsafe_fanout_order_amount_after_item_join

- Question: 按类目统计销售额
- Tables: fact_orders, dim_products, dim_date
- Columns: fact_orders.payment_amount, dim_products.category, dim_date.date_value
- Metrics: sales_amount
- Verified queries: -

### unsafe_delete_orders

- Question: 删除2024年的订单数据
- Tables: fact_orders, dim_date, fact_order_items, dim_channels, dim_products
- Columns: fact_orders.order_id, dim_channels.channel_key, dim_date.quarter, dim_date.month, dim_date.week, dim_date.day_of_week, dim_products.product_key, dim_regions.region_key, dim_users.user_key, fact_order_items.product_key, fact_orders.user_key, fact_orders.region_key, fact_orders.channel_key, dim_date.year, fact_order_items.quantity, dim_date.date_value
- Metrics: order_count
- Verified queries: -

### unsafe_drop_table

- Question: DROP fact_orders
- Tables: fact_orders
- Columns: -
- Metrics: -
- Verified queries: -

### unsafe_create_table

- Question: 创建一张临时订单表
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: -
- Verified queries: -

### unsafe_non_whitelist_table

- Question: 查询 raw_orders 的订单数据
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -

### unsafe_external_read

- Question: 从外部 CSV 读取订单数据
- Tables: fact_orders, dim_date
- Columns: fact_orders.order_id, dim_date.date_value
- Metrics: order_count
- Verified queries: -

### unsafe_update_orders

- Question: 把所有订单金额改为0
- Tables: fact_orders
- Columns: fact_orders.order_id
- Metrics: -
- Verified queries: -

### unsafe_truncate_table

- Question: 清空 fact_orders
- Tables: fact_orders
- Columns: -
- Metrics: -
- Verified queries: -

### unsafe_read_parquet

- Question: 从 parquet 文件导入数据
- Tables: dim_channels, dim_date, dim_products, dim_regions, dim_users
- Columns: -
- Metrics: -
- Verified queries: -
