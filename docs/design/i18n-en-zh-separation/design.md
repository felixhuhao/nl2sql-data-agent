# Design — i18n: EN/ZH Separation

## Digest
- **Bottom line:** Approve if you agree that i18n is **output-presentation only** — Chinese NLU (intent/OLAP/date/alias matching) and business metadata (metric labels, aliases) stay bilingual/as-is and are explicitly *not* localized — with locale resolved once at the API boundary and **default locale = `zh`** so all current behaviour (and the closeout's validation) is byte-for-byte preserved while EN is added.  ·  **Lane:** full (lightweight)  ·  **Status:** Implemented
- **Change:** Separate mixed EN/ZH into a proper i18n layer: symbolic keys with parallel `zh`/`en` catalogs, locale from `Accept-Language` (+ request override), threaded to output nodes only.
- **Decisions:**
  - **(A) Scope = output/presentation strings only; NLU + domain data explicitly OUT** — why: the mixed Chinese is three different concerns; localizing the NLU patterns would break Chinese *input* handling regardless of UI locale — rejected: "extract all Chinese" (breaks intent/OLAP/date understanding), including domain-data localization (conflates business content with UI strings).
  - **(B) Locale resolved once at the API boundary → `AgentState.locale` → consumed by output nodes** — why: single resolution point; generation stays locale-independent; no locale threaded through every signature — rejected: per-call param plumbing, global/context-var (implicit, test-hostile).
  - **(C) Symbolic keys + parallel `zh`/`en` catalogs; backend lightweight JSON + resolver, frontend `vue-i18n`** — why: greenfield (no existing i18n), small string count; JSON needs no compile step and shares a key convention with the frontend — rejected: gettext/Babel `.po` (overkill), inline conditionals (the status quo).
  - **(D) `DEFAULT_LOCALE = "zh"`** — why: preserves every existing ZH output exactly (existing tests + the closeout's `查询返回 5 行` validation still hold); EN is purely additive — rejected: default `en` (churns all current output + breaks the sibling validation).
- **Risks / watch:** the scope boundary is correctness-critical — an NLU string mistakenly moved to a catalog silently breaks Chinese input; locale must not leak into schema context / generated SQL (presentation only); missing key must fall back, never crash.
- **Open questions:** whether MCP surfaces a locale (default `zh` for now); supported set stays `{zh, en}`.
- **Drill down:** full design below · pros/cons in `design.html`.

---

## 1. Problem / context

Chinese and English are mixed across backend and frontend with no i18n layer (frontend is Vue 3 without `vue-i18n`; backend has no gettext/Babel). A scan shows the CJK strings are **three different concerns** that must be handled *oppositely*:

| Category | Examples | Treatment |
|---|---|---|
| **Output / presentation** | result summary `查询返回 N 行` ([nodes.py:457](../../../backend/app/agent/nodes.py#L457)); semantic block `当前 schema 中没有…` ([semantic_grounding.py:459](../../../backend/app/agent/semantic_grounding.py#L459)); perf hints; frontend UI (`App.vue`, `Admin.vue`) | **i18n** (this change) |
| **Input / NLU** | OLAP phrase detection ([olap_intent.py](../../../backend/app/agent/olap_intent.py)); intent verbs `删除/清空` ([nodes.py:102](../../../backend/app/agent/nodes.py#L102)); Chinese date rules ([date_rules.py](../../../backend/app/core/date_rules.py)); alias matching ([retrieval.py](../../../backend/app/metadata/retrieval.py)) | **NOT i18n** — stays bilingual always |
| **Domain data** | metric labels `销售额/客单价`, aliases, table display names ([seed.py](../../../backend/app/metadata/seed.py)); verified queries | **Metadata content** — not catalog |

The trap: a naive "extract all Chinese to a locale file" would move the NLU patterns and break Chinese input handling. The scope taxonomy is this design's core value.

## 2. Goals

**In scope:**
- (A) A scope taxonomy that commits which strings are localized and which are explicitly excluded.
- (B) Locale resolution + propagation to output-producing nodes.
- (C) Catalog mechanism (backend + frontend) with symbolic keys and parallel `zh`/`en` values.
- (D) Default-locale choice that keeps existing behaviour intact.

**Out of scope (explicitly):**
- Localizing NLU patterns (intent/OLAP/date/alias) — they remain bilingual input understanding.
- Localizing domain data (metric labels, aliases, table display names, verified queries) — metadata content; any per-locale metadata localization is a separate future change.
- Changing generated SQL, schema context, or column names — generation stays locale-independent.
- New languages beyond `zh`/`en`.

## 3. Key decisions

### (A) Scope taxonomy — *the crux*

**Localized (moved to catalogs):** the result summary, semantic-guard block message, intent-guard blocked message, performance/plan hint strings, any other API-surfaced user message, and all frontend UI chrome. Commit: enumerate these during implementation from the "Output" category; each becomes a symbolic key.

**Explicitly excluded (must NOT be touched):** every string in the Input/NLU and Domain-data categories above. Rationale: these are not presentation — the system must accept Chinese input and carry Chinese business terms regardless of the user's output locale. A finding that an excluded string "should also be localized" is out of scope by design.

### (B) Locale resolution + propagation

- Resolve **once at the API boundary**: parse `Accept-Language`; an explicit `locale` field on the chat/query request body overrides it; resolve to the supported set `{zh, en}`, else `DEFAULT_LOCALE`.
- Carry as **`AgentState.locale`** (new field, defaulted to `DEFAULT_LOCALE`); output nodes read it. No locale in function signatures beyond state.
- **Generation is locale-independent**: `AgentState.locale` influences only presentation nodes (`summarize_node`, guard/error message construction, performance hints). Schema context, prompts, and generated SQL are unchanged by locale. (Committed failure-mode boundary.)

### (C) Catalog mechanism

- **Backend:** `backend/app/i18n/{zh,en}.json` keyed by symbolic dotted IDs (e.g. `agent.result_summary`, `guard.semantic_block`, `intent.blocked`); a small resolver `t(key, locale, **params) -> str` with `str.format`-style params. Missing key → fallback-locale value → raw key (logged). No compile step.
- **Frontend:** add `vue-i18n`; extract `.vue` Chinese into `zh`/`en` message files; locale from a UI switcher (persisted in `localStorage`) and sent to the backend via the request `locale` field / `Accept-Language`.
- **Independent per-layer catalogs, shared key-naming convention** — no shared build artifact.

### (D) Default locale = `zh`

`DEFAULT_LOCALE = "zh"` (config). Every existing Chinese output stays identical when no locale is supplied, so current backend tests and the `retrieval-expansion-closeout` e2e validation (which waits on `查询返回 5 行`) remain valid. **EN is purely additive.** This dissolves the sibling-coupling concern: i18n does not change default-locale output.

## 4. Alternatives & rationale (full lane)

### (A) Scope boundary
| Option | Pros | Cons |
|---|---|---|
| **Output-only; NLU + data out (chosen)** | Safe; matches the three-concern reality; no input-handling risk. | Leaves Chinese in NLU/data (correct — it's not UI). |
| Output + domain-data localization | "Fully localized" labels. | Touches semantic layer/metadata; conflates business data with UI; bigger, riskier. |

### (B) Propagation
| Option | Pros | Cons |
|---|---|---|
| **Resolve-once → `AgentState.locale` (chosen)** | Single point; explicit; test-friendly; generation stays clean. | One new state field. |
| Thread locale through signatures | Explicit at each call. | Churns many signatures for a presentation concern. |
| Global / context-var | No signature changes. | Implicit, hostile to tests + concurrency. |

### (C) Catalog mechanism
| Option | Pros | Cons |
|---|---|---|
| **JSON + resolver / vue-i18n (chosen)** | No compile step; tiny; shared key convention; frontend-standard. | Hand-rolled backend resolver (trivial). |
| gettext / Babel `.po` | Industry standard; pluralization tooling. | Compilation + tooling overhead, disproportionate here. |
| Inline conditionals | No new files. | This *is* the status quo — unmaintainable. |

### (D) Default locale
Chosen `zh` — preserves existing output + sibling validation; rejected `en` (churns all current output and breaks the closeout assertion).

## 5. Altitude — deferred to implementation

- Exact symbolic key names and JSON file layout.
- `vue-i18n` wiring, the UI switcher's placement, `Accept-Language` parsing helper.
- The concrete enumeration of Output-category strings to extract (from the committed taxonomy).

## 6. Open questions

- MCP locale surfacing → default `zh` for now; add a locale arg later if needed.
- Supported set stays `{zh, en}`; more languages are a later additive change.

## 7. Status

`Implemented` — maker: Claude. Reviewer: Codex, REVIEWER-CLEAR (zero BLOCKING; two implementation defers logged). Operator approved; implementation complete. Separate slice from `retrieval-expansion-closeout` (coupling dissolved by decision D).
Approval: felixhuhao — `approved`.

## 8. Implementation Notes

- Backend locale support lives in `backend/app/i18n/` and `backend/app/config.py`; `resolve_locale()` accepts explicit request locale first, then `Accept-Language`, then default `zh`.
- `AgentState.locale` is presentation-only. SQL generation request fields, schema context, prompts, NLU patterns, date rules, alias matching, and metadata/domain strings remain locale-independent.
- Frontend locale support lives in `frontend/src/i18n.ts`; the UI switcher persists `nl2sql_locale`, updates visible chrome, and sends both request `locale` and `Accept-Language`.
- Verification performed during implementation: `pytest backend/tests`, `ruff`, `pyright`, and `npm --prefix frontend run build`.
