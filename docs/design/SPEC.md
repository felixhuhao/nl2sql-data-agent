# Design Spec

## I18n EN/ZH Separation

- Locale resolution is output-presentation only: explicit chat request `locale` overrides `Accept-Language`; unsupported or missing locale falls back to `zh`.
- Backend output strings use `backend/app/i18n/{zh,en}.json` through `t(key, locale, **params)`. Missing locale keys fall back to default `zh`; missing default keys return the raw key and log.
- `AgentState.locale` must not affect schema context, SQL generation requests, prompts, NLU patterns, date rules, alias matching, or metadata/domain strings.
- Frontend UI chrome uses `vue-i18n` from `frontend/src/i18n.ts`; the language switcher persists `nl2sql_locale` and sends both request `locale` and `Accept-Language`.
- Default `zh` output preserves the retrieval closeout summary shape, including `查询返回 N 行，字段：...。`.

## Evaluation And Retrieval Closeout

- Smoke eval cases can target one datasource with legacy `datasource: X` or multiple datasources with `datasources: [X, Y]`. Setting both keys is invalid; setting neither defaults to `duckdb_ecommerce`.
- Multi-datasource parity anchors run once per listed available datasource and compare final retrieval coverage bands when coverage is computed. All-missing coverage is treated as not comparable; partial missing coverage fails the anchor group.
- Retrieval closeout fixtures can stub recalled tables, columns, metrics, and verified queries, then assert coverage path fields: pre-expansion band, post-context band, `expanded`, and `fallback_used`.
- Retrieval recovery is enabled by default after vector/hybrid coverage recalibration: `RETRIEVAL_EXPANSION_ENABLED=true`, `RETRIEVAL_FALLBACK_MODE=on`. Last tested constants: threshold `0.7`, strength weight `0.5`, structural weight `0.5`, max expansion tables `3`, and full-schema budget `120000` chars.
- Retrieval calibration mode enables recovery in-process, sweeps supplied coverage thresholds, reports recovery cases, fallback-path cases, high-confidence regressions, fallback count, and focused-context delta. The high-confidence holdout is fixed at the production reference threshold before the sweep.
- The smoke runner has `--require-clickhouse`, which fails if any ClickHouse-listed case is skipped because `clickhouse_ecommerce` is unavailable.
- Current recovery evidence covers `missing_join_path`; fallback evidence covers `dangling_no_fact`. The `missing_dimension` archetype was not reproducible in the seeded corpus because realistic metric-by-dimension phrasing recalls the dimension through column names, descriptions, or values.
