# Design Spec

## Evaluation And Retrieval Closeout

- Smoke eval cases can target one datasource with legacy `datasource: X` or multiple datasources with `datasources: [X, Y]`. Setting both keys is invalid; setting neither defaults to `duckdb_ecommerce`.
- Multi-datasource parity anchors run once per listed available datasource and compare final retrieval coverage bands when coverage is computed. All-missing coverage is treated as not comparable; partial missing coverage fails the anchor group.
- Retrieval closeout fixtures can stub recalled tables, columns, metrics, and verified queries, then assert coverage path fields: pre-expansion band, post-context band, `expanded`, and `fallback_used`.
- Retrieval calibration mode enables recovery in-process, sweeps supplied coverage thresholds, reports recovery cases, fallback-path cases, high-confidence regressions, fallback count, and focused-context delta. The high-confidence holdout is fixed at the production reference threshold before the sweep.
- The smoke runner has `--require-clickhouse`, which fails if any ClickHouse-listed case is skipped because `clickhouse_ecommerce` is unavailable.
- Current recovery evidence covers `missing_join_path`; fallback evidence covers `dangling_no_fact`. The `missing_dimension` archetype remains a tracked follow-up because current scorer behavior classifies fact-only metric intent as structurally high before expansion.
