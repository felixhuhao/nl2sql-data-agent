# Design Roadmap

| slug | lane | priority | status | summary |
|------|------|----------|--------|---------|
| retrieval-recall-expansion | full | P0 | Approved | deterministic graph expansion of recalled tables + hybrid coverage score triggering full-schema fallback on low confidence |
| retrieval-expansion-closeout | full | P0 | Validated | incomplete-recall eval cases + recovery-first calibration + datasource-partitioned harness (targeted CH set, parity anchors, CH-up closeout gate) |
| i18n-en-zh-separation | full | P1 | Validated | output-only EN/ZH i18n (symbolic keys, zh/en catalogs, Accept-Language + override); NLU & domain data excluded; default locale zh keeps current behaviour |
