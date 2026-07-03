# Design Roadmap

| slug | lane | priority | status | summary |
|------|------|----------|--------|---------|
| retrieval-recall-expansion | full | P0 | Validated, default-off | deterministic graph expansion of recalled tables + hybrid coverage score triggering full-schema fallback on low confidence; broad default pending vector/hybrid recalibration |
| retrieval-expansion-closeout | full | P0 | Validated, default-off | incomplete-recall eval cases + recovery-first calibration + datasource-partitioned harness; broad default pending vector/hybrid recalibration |
| i18n-en-zh-separation | full | P1 | Validated | output-only EN/ZH i18n (symbolic keys, zh/en catalogs, Accept-Language + override); NLU & domain data excluded; default locale zh keeps current behaviour |
| coverage-strength-recalibration | full | P0 | In-progress | fix coverage over-firing under vector retrieval: emit scale-faithful coverage_match_strength from the merge, recalibrate threshold vector-ON before re-enabling default-on |
