# Tasks — i18n: EN/ZH Separation

Implementer checklist (design altitude). Design: [design.md](design.md).

## 1. Scope enumeration (pillar A)
- [x] Enumerate the Output-category strings to extract (backend: result summary, semantic-guard block, intent blocked, performance/plan hints, other API-surfaced messages; frontend: `App.vue`, `Admin.vue`, components).
- [x] Confirm the exclusion list is untouched: `agent/olap_intent.py`, `agent/nodes.py` intent/mutation patterns, `core/date_rules.py`, `metadata/retrieval.py` alias matching (NLU); `metadata/seed.py`, `dataspace/verified_queries.py` (domain data).

## 2. Backend catalog + resolver (pillar C)
- [x] Add `backend/app/i18n/{zh,en}.json` with symbolic dotted keys.
- [x] Implement `t(key, locale, **params)` — fallback locale → raw key (logged), never crash.
- [x] Add `DEFAULT_LOCALE = "zh"` and supported set `{zh, en}` to config.

## 3. Locale propagation (pillar B)
- [x] Resolve locale at the API boundary: `Accept-Language` + request `locale` override → supported set → default.
- [x] Add `AgentState.locale` (default `DEFAULT_LOCALE`); wire from the resolved value.
- [x] Convert output nodes to `t(...)`: `summarize_node`, semantic-guard block message, intent blocked message, performance hints.
- [x] Assert generation is locale-independent: schema context / prompts / SQL unchanged by locale.

## 4. Frontend (pillar C)
- [x] Add `vue-i18n`; create `zh`/`en` message files; extract `.vue` Chinese.
- [x] UI locale switcher; persist in `localStorage`; send locale to backend (request field / `Accept-Language`).

## 5. Tests
- [x] Backend: default-locale (`zh`) output byte-identical to today (regression); `en` returns English; missing key falls back.
- [x] Locale resolution: header, override precedence, unsupported → default.
- [x] Generation invariance: same SQL/schema context under `zh` vs `en`.
- [x] Frontend: switcher toggles catalogs; locale reaches the API.

## 6. Docs / hygiene
- [x] Note the i18n layer + scope taxonomy in a short doc (or `docs/DEVELOPMENT.md`).
- [x] Confirm no regression to `retrieval-expansion-closeout` validation (default `zh` `查询返回 N 行` preserved).
