# Semantic Refutation Hardening (Phase 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blunt substring corroboration in `SemanticRefutationAuditor` with structured, channel-by-channel, datasource-scoped refutation plus a `SELECT DISTINCT` probe for value-level concepts, so a confirmed refutation is trustworthy enough to gate a hard block.

**Architecture:** A new `SchemaEvidence` value object is built once per datasource from the existing datasource-scoped metadata accessors (`list_tables`, `list_columns`, `list_metrics`, `list_aliases`, `list_verified_queries`). The auditor confirms a refutation only when a requested concept is absent from **every** channel. For value-typed concepts that name a metadata-validated `(target_table, target_column, requested_value)`, an injected executor runs a bounded `SELECT DISTINCT` to confirm the value is genuinely absent before confirming. The auditor still *only refutes* — finding evidence makes it abstain, never assert support.

**Tech Stack:** Python 3.12, dataclasses, sqlglot (already used), pytest. Metadata via `backend.app.metadata.service`. Execution via `backend.app.execution.runner` / `backend.app.sql_guard.guard`.

**Scope note:** This is Phase **2A** (runtime block-grade evidence). The promotion eval methodology (three-way verdict, pinned-SQL fixtures, live smoke, per-pattern gate, availability SLO) is **Phase 2B**, a separate plan that consumes this one. Overlay→datasource binding remains an open question and is intentionally **not** used as evidence here; the datasource's own metadata is authoritative.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/agent/schema_evidence.py` | `SchemaEvidence` model + `build_schema_evidence(datasource_name)` channel builder + normalization | **Create** |
| `backend/app/agent/semantic_grounding.py` | `SemanticRefutationAuditor` uses structured evidence + DISTINCT probe; `RequiredConcept` gains `target_table`/`target_column`/`requested_value`; `audit()` takes evidence + concept | **Modify** |
| `backend/app/agent/prompts/semantic_grounding.py` | extraction prompt asks for `target_table`/`target_column`/`requested_value` on value-typed concepts | **Modify** |
| `backend/app/sql_guard/guard.py` | `guard_sql` accepts a narrow `max_result_rows` override for the internal 1000-row semantic probe; user SQL default stays 500 | **Modify** |
| `backend/tests/test_schema_evidence.py` | unit tests for evidence builder | **Create** |
| `backend/tests/test_semantic_grounding.py` | extend: structured refutation, DISTINCT probe, parse of new fields | **Modify** |

**Test command (always run from repo root):**
`PYTHONPATH=. backend/.venv/bin/python -m pytest <path> -q`

---

## Task 1: `SchemaEvidence` model + builder

**Files:**
- Create: `backend/app/agent/schema_evidence.py`
- Test: `backend/tests/test_schema_evidence.py`

The builder pulls every datasource-scoped channel into **entry-scoped evidence records**, not a single concatenated blob. Normalization stays consistent with the current guard (lowercased ASCII alnum runs + CJK runs), but matching is done per entry with exact phrase / token-set semantics so short strings such as `id` cannot match inside `paid`, and concepts cannot be assembled accidentally across unrelated metadata fields.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_schema_evidence.py
from backend.app.agent.schema_evidence import SchemaEvidence, build_schema_evidence


def _fake_sources():
    tables = [{"table_name": "fact_orders", "display_name": "订单", "description": "订单事实表", "domain": "sales"}]
    columns = {
        "fact_orders": [
            {"column_name": "order_status", "description": "订单状态", "sample_values": '["paid", "completed", "refunded"]'},
            {"column_name": "payment_amount", "description": "支付金额", "sample_values": []},
        ]
    }
    metrics = [{"name": "refund_rate", "label": "退款率", "expression": "x", "description": ""}]
    aliases = [{"alias": "金额", "column_name": "payment_amount", "table_name": "fact_orders"}]
    verified = [{"question": "查询每日销售额", "sql": "SELECT 1"}]
    return tables, columns, metrics, aliases, verified


def test_build_schema_evidence_indexes_all_channels():
    tables, columns, metrics, aliases, verified = _fake_sources()
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: tables,
        list_columns=lambda table_name, datasource_name: columns.get(table_name, []),
        list_metrics=lambda datasource_name: metrics,
        list_aliases=lambda datasource_name: aliases,
        list_verified_queries=lambda datasource_name: verified,
    )
    assert evidence.has_concept_evidence("退款率") is True       # metric label
    assert evidence.has_concept_evidence("订单状态") is True      # column description
    assert evidence.has_concept_evidence("删除率") is False       # absent everywhere
    assert evidence.has_concept_evidence("id") is False          # does not match inside "paid"
    assert evidence.column_values("order_status") == ("paid", "completed", "refunded")
    assert evidence.columns_with_value("refunded") == ("order_status",)
    assert evidence.columns_with_value("cancelled") == ()
    assert evidence.has_column("fact_orders", "order_status") is True
    assert evidence.unique_table_for_column("order_status") == "fact_orders"


def test_has_concept_evidence_is_normalization_insensitive():
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [{"table_name": "fact_orders", "display_name": "Refund Rate", "description": "", "domain": ""}],
        list_columns=lambda table_name, datasource_name: [],
        list_metrics=lambda datasource_name: [],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )
    assert evidence.has_concept_evidence("refund   rate") is True   # spacing/case ignored


def test_has_concept_evidence_does_not_match_across_entries():
    evidence = build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [{"table_name": "refund", "display_name": "", "description": "", "domain": ""}],
        list_columns=lambda table_name, datasource_name: [
            {"column_name": "rate", "description": "", "sample_values": []},
        ],
        list_metrics=lambda datasource_name: [],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )
    assert evidence.has_concept_evidence("refund rate") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_schema_evidence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.agent.schema_evidence'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/agent/schema_evidence.py
from __future__ import annotations

import re
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata import service


def normalize_evidence_text(text: object) -> str:
    return "".join(re.findall(r"[a-z0-9]+|[㐀-鿿]+", str(text).casefold()))


def _terms(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+|[㐀-鿿]+", str(text).casefold()))


@dataclass(frozen=True)
class EvidenceEntry:
    channel: str
    text: str
    table_name: str = ""
    column_name: str = ""
    normalized: str = ""
    terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SchemaEvidence:
    datasource_name: str
    # Individual evidence entries; never concatenate unrelated metadata fields.
    _entries: tuple[EvidenceEntry, ...] = ()
    # column_name -> ordered distinct sample values (original casing)
    _column_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # normalized value -> column names that enumerate it
    _value_index: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # column_name -> table names that own it, for probe target validation
    _column_tables: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def has_concept_evidence(self, concept: str) -> bool:
        token = normalize_evidence_text(concept)
        if not token:
            return False
        concept_terms = _terms(concept)
        return any(_entry_supports_concept(token, concept_terms, entry) for entry in self._entries)

    def column_values(self, column_name: str) -> tuple[str, ...]:
        return self._column_values.get(column_name, ())

    def columns_with_value(self, value: str) -> tuple[str, ...]:
        return self._value_index.get(normalize_evidence_text(value), ())

    def has_sample_value(self, column_name: str, value: str) -> bool:
        return self.value_matches(value, self.column_values(column_name))

    def value_matches(self, value: str, candidates: Iterable[object]) -> bool:
        normalized = normalize_evidence_text(value)
        if not normalized:
            return False
        return normalized in {normalize_evidence_text(candidate) for candidate in candidates}

    def has_column(self, table_name: str, column_name: str) -> bool:
        return table_name in self._column_tables.get(column_name, ())

    def unique_table_for_column(self, column_name: str) -> str | None:
        tables = self._column_tables.get(column_name, ())
        return tables[0] if len(tables) == 1 else None


ListTables = Callable[..., list[dict]]
ListColumns = Callable[..., list[dict]]
ListMetrics = Callable[..., list[dict]]
ListAliases = Callable[..., list[dict]]
ListVerified = Callable[..., list[dict]]


def _entry_supports_concept(normalized_concept: str, concept_terms: frozenset[str], entry: EvidenceEntry) -> bool:
    if normalized_concept == entry.normalized:
        return True
    if concept_terms and concept_terms.issubset(entry.terms):
        return True
    # CJK business terms are often unsegmented and commonly two characters
    # long. Keep containment CJK-only and entry-scoped so ASCII short tokens
    # such as "id" cannot match inside values such as "paid".
    if re.fullmatch(r"[\u3400-\u9fff]+", normalized_concept) and len(normalized_concept) >= 2:
        return normalized_concept in entry.normalized
    return False


def _add_entry(
    entries: list[EvidenceEntry],
    channel: str,
    text: object,
    *,
    table_name: str = "",
    column_name: str = "",
) -> None:
    raw = str(text or "").strip()
    normalized = normalize_evidence_text(raw)
    if not normalized:
        return
    entries.append(
        EvidenceEntry(
            channel=channel,
            text=raw,
            table_name=table_name,
            column_name=column_name,
            normalized=normalized,
            terms=_terms(raw),
        )
    )


def _sample_values(raw_values: object) -> tuple[str, ...]:
    if isinstance(raw_values, str):
        try:
            parsed = json.loads(raw_values)
        except json.JSONDecodeError:
            parsed = []
    else:
        parsed = raw_values
    if not isinstance(parsed, list):
        return ()
    return tuple(dict.fromkeys(text for value in parsed if (text := str(value).strip())))


def _merge_values(existing: tuple[str, ...], new_values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *new_values)))


def build_schema_evidence(
    datasource_name: str = DEFAULT_DATASOURCE,
    *,
    list_tables: ListTables = service.list_tables,
    list_columns: ListColumns = service.list_columns,
    list_metrics: ListMetrics = service.list_metrics,
    list_aliases: ListAliases = service.list_aliases,
    list_verified_queries: ListVerified = service.list_verified_queries,
) -> SchemaEvidence:
    entries: list[EvidenceEntry] = []
    column_values: dict[str, tuple[str, ...]] = {}
    value_index: dict[str, list[str]] = {}
    column_tables: dict[str, list[str]] = {}

    tables = list_tables(datasource_name=datasource_name)
    for table in tables:
        table_name = str(table.get("table_name", ""))
        for attr in ("table_name", "display_name", "description", "domain"):
            _add_entry(entries, f"table.{attr}", table.get(attr), table_name=table_name)

    for table in tables:
        table_name = str(table.get("table_name", ""))
        for column in list_columns(table_name=table_name, datasource_name=datasource_name):
            name = column.get("column_name", "")
            column_tables.setdefault(name, [])
            if table_name not in column_tables[name]:
                column_tables[name].append(table_name)
            _add_entry(entries, "column.name", name, table_name=table_name, column_name=name)
            _add_entry(entries, "column.description", column.get("description"), table_name=table_name, column_name=name)
            samples = _sample_values(column.get("sample_values"))
            if samples:
                column_values[name] = _merge_values(column_values.get(name, ()), samples)
                for value in samples:
                    _add_entry(entries, "column.sample_value", value, table_name=table_name, column_name=name)
                    normalized_value = normalize_evidence_text(value)
                    value_index.setdefault(normalized_value, [])
                    if name not in value_index[normalized_value]:
                        value_index[normalized_value].append(name)

    for metric in list_metrics(datasource_name=datasource_name):
        for attr in ("name", "label", "description", "expression"):
            _add_entry(entries, f"metric.{attr}", metric.get(attr))
    for alias in list_aliases(datasource_name=datasource_name):
        _add_entry(
            entries,
            "alias",
            alias.get("alias"),
            table_name=str(alias.get("table_name", "")),
            column_name=str(alias.get("column_name", "")),
        )
    for query in list_verified_queries(datasource_name=datasource_name):
        for attr in ("question", "sql"):
            _add_entry(entries, f"verified_query.{attr}", query.get(attr))

    return SchemaEvidence(
        datasource_name=datasource_name,
        _entries=tuple(entries),
        _column_values=column_values,
        _value_index={key: tuple(value) for key, value in value_index.items()},
        _column_tables={key: tuple(value) for key, value in column_tables.items()},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_schema_evidence.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/schema_evidence.py backend/tests/test_schema_evidence.py
git commit -m "Add structured schema evidence for refutation audit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Concept-level structured refutation in the auditor

Replace `_concept_has_schema_evidence` (substring over the rendered context string) with `SchemaEvidence`. The auditor builds evidence once per datasource (cached) and confirms only when absent from every channel.

**Files:**
- Modify: `backend/app/agent/semantic_grounding.py:122-157` (`SemanticRefutationAuditor`)
- Modify: `backend/app/agent/semantic_grounding.py:296-299` (`_full_schema_context`) and `:218-220` (audit call site in `semantic_guard_node`)
- Test: `backend/tests/test_semantic_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to backend/tests/test_semantic_grounding.py
from backend.app.agent.schema_evidence import SchemaEvidence, build_schema_evidence


def _evidence_with(metric_label: str = "", column_desc: str = "") -> SchemaEvidence:
    return build_schema_evidence(
        "duckdb_ecommerce",
        list_tables=lambda datasource_name: [{"table_name": "fact_orders", "display_name": "", "description": column_desc, "domain": ""}],
        list_columns=lambda table_name, datasource_name: [],
        list_metrics=lambda datasource_name: [{"name": "m", "label": metric_label, "expression": "", "description": ""}],
        list_aliases=lambda datasource_name: [],
        list_verified_queries=lambda datasource_name: [],
    )


def test_refutation_confirms_when_absent_from_every_channel():
    auditor = SemanticRefutationAuditor(evidence_builder=lambda datasource_name: _evidence_with(metric_label="退款率"))
    result = auditor.audit(
        SemanticGroundingIssue(concept="删除率", failure_kind="substituted"),
        evidence=auditor.evidence(datasource_name="duckdb_ecommerce"),
    )
    assert result.confirmed is True


def test_refutation_abstains_when_any_channel_has_evidence():
    auditor = SemanticRefutationAuditor(evidence_builder=lambda datasource_name: _evidence_with(metric_label="退款率"))
    result = auditor.audit(
        SemanticGroundingIssue(concept="退款率", failure_kind="substituted"),
        evidence=auditor.evidence(datasource_name="duckdb_ecommerce"),
    )
    assert result.confirmed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_grounding.py -k refutation_confirms_when_absent -q`
Expected: FAIL with `TypeError` (no `evidence_builder` kwarg / no `evidence` method)

- [ ] **Step 3: Write minimal implementation**

Replace the `SemanticRefutationAuditor` class body:

```python
# backend/app/agent/semantic_grounding.py
from backend.app.agent.schema_evidence import SchemaEvidence, build_schema_evidence

EvidenceBuilder = Callable[..., SchemaEvidence]


class SemanticRefutationAuditor:
    """Corroborates verifier findings from full schema evidence; it never interprets the question."""

    def __init__(self, evidence_builder: EvidenceBuilder = build_schema_evidence) -> None:
        self._evidence_builder = evidence_builder

    def evidence(self, *, datasource_name: str) -> SchemaEvidence:
        return self._evidence_builder(datasource_name=datasource_name)

    def audit(
        self,
        issue: SemanticGroundingIssue,
        *,
        evidence: SchemaEvidence,
        concept: "RequiredConcept | None" = None,
    ) -> RefutationAuditResult:
        name = issue.concept.strip()
        if not name:
            return RefutationAuditResult(confirmed=False, reason="No requested concept was provided by the verifier.")
        if evidence.has_concept_evidence(name):
            return RefutationAuditResult(
                confirmed=False,
                reason=f"Full datasource metadata contains evidence for {name!r}; deterministic audit abstained.",
            )
        return RefutationAuditResult(
            confirmed=True,
            reason=f"Full datasource metadata contains no evidence for {name!r} across any channel.",
        )
```

Delete the now-unused `_concept_has_schema_evidence` and `_normalize_evidence_text`. Keep the rendered `full_schema_context` path for the verifier prompt; the LLM still needs the human-readable context. Update `semantic_guard_node` to build/cache structured evidence separately for the auditor:

```python
# in semantic_guard_node, keep `full_context = _full_schema_context(state)` for the verifier prompt,
# and add structured evidence for the deterministic audit:
    full_context = _full_schema_context(state)
    evidence = _schema_evidence(state, auditor)
    concepts = _required_concepts(state, verifier, full_context)
    ...
    for issue in result.issues:
        concept = _concept_for_issue(issue, unsupported_concepts)
        refutation = auditor.audit(issue, evidence=evidence, concept=concept)
        state.grounding_warnings.append(_warning_from_issue(issue, refutation))
```

Add helpers. `_required_concepts` continues to accept the rendered context string; only the auditor uses `SchemaEvidence`:

```python
def _full_schema_context(state: AgentState) -> str:
    if state.full_schema_context is None:
        state.full_schema_context = build_schema_context(datasource_name=state.datasource_name)
    return state.full_schema_context


def _schema_evidence(state: AgentState, auditor: SemanticRefutationAuditor) -> SchemaEvidence:
    if state.schema_evidence is None:
        state.schema_evidence = auditor.evidence(datasource_name=state.datasource_name)
    return state.schema_evidence


def _concept_for_issue(issue, concepts):
    by_id = {c.concept_id: c for c in concepts}
    by_name = {c.concept: c for c in concepts}
    return by_id.get(issue.concept_id) or by_name.get(issue.concept)
```

Add `schema_evidence: "SchemaEvidence | None" = None` to `AgentState`. **Do not reset it in `repair.reset_failure_state`**; the repair loop intentionally preserves question-invariant context such as `required_concepts` and `full_schema_context`, and structured evidence should be built once per query/datasource, not once per repair candidate.

- [ ] **Step 4: Run the full semantic suite**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_grounding.py -q`
Expected: PASS. Update the two pre-existing auditor tests (`test_refutation_auditor_confirms_absent_requested_concept`, `test_refutation_auditor_abstains_when_full_metadata_has_evidence`) to use `evidence_builder=` / `evidence(...)` instead of `full_schema_context_builder=` / `full_schema_context(...)`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/semantic_grounding.py backend/app/agent/state.py backend/app/agent/repair.py backend/tests/test_semantic_grounding.py
git commit -m "Refute concepts by structured channel evidence, not substring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `SELECT DISTINCT` probe for value-level concepts

When a requested concept is a **value** (e.g. "status = cancelled") that is absent from metadata evidence, run a bounded `SELECT DISTINCT <table>.<column>` to confirm it is genuinely absent before confirming the refutation. The probe runs **only** when the concept carries a metadata-validated `(target_table, target_column, requested_value)` (or a unique metadata-resolvable column); otherwise the auditor abstains. The probe never tests a substituted proxy value.

**Files:**
- Modify: `backend/app/agent/semantic_grounding.py` (`RequiredConcept`, parse, `audit`)
- Modify: `backend/app/agent/prompts/semantic_grounding.py` (extraction prompt)
- Test: `backend/tests/test_semantic_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
def _value_concept(table: str, column: str, value: str) -> RequiredConcept:
    return RequiredConcept(
        concept=f"{column}={value}", concept_id="c1", concept_type="value",
        supported=False, target_table=table, target_column=column, requested_value=value,
    )


def test_distinct_probe_confirms_when_requested_value_absent_in_data():
    probed = {}
    def fake_executor(table, column, *, datasource_name):
        probed["table"] = table
        probed["column"] = column
        return ("paid", "completed", "refunded")  # 'cancelled' absent
    auditor = SemanticRefutationAuditor(
        evidence_builder=lambda datasource_name: build_schema_evidence(
            "d",
            list_tables=lambda datasource_name: [{"table_name": "fact_orders", "display_name": "", "description": "", "domain": ""}],
            list_columns=lambda table_name, datasource_name: [
                {"column_name": "order_status", "description": "订单状态", "sample_values": []},
            ],
            list_metrics=lambda datasource_name: [],
            list_aliases=lambda datasource_name: [],
            list_verified_queries=lambda datasource_name: [],
        ),
        distinct_executor=fake_executor,
    )
    issue = SemanticGroundingIssue(concept="order_status=cancelled", failure_kind="substituted", concept_id="c1")
    result = auditor.audit(issue, evidence=auditor.evidence(datasource_name="d"), concept=_value_concept("fact_orders", "order_status", "cancelled"))
    assert result.confirmed is True
    assert probed["table"] == "fact_orders"
    assert probed["column"] == "order_status"


def test_distinct_probe_abstains_when_requested_value_present_in_data():
    auditor = SemanticRefutationAuditor(
        evidence_builder=lambda datasource_name: build_schema_evidence(
            "d",
            list_tables=lambda datasource_name: [{"table_name": "fact_orders", "display_name": "", "description": "", "domain": ""}],
            list_columns=lambda table_name, datasource_name: [
                {"column_name": "order_status", "description": "订单状态", "sample_values": []},
            ],
            list_metrics=lambda datasource_name: [],
            list_aliases=lambda datasource_name: [],
            list_verified_queries=lambda datasource_name: [],
        ),
        distinct_executor=lambda table, column, *, datasource_name: ("paid", "cancelled"),
    )
    issue = SemanticGroundingIssue(concept="order_status=cancelled", failure_kind="substituted", concept_id="c1")
    result = auditor.audit(issue, evidence=auditor.evidence(datasource_name="d"), concept=_value_concept("fact_orders", "order_status", "cancelled"))
    assert result.confirmed is False


def test_value_audit_abstains_when_metadata_describes_requested_value_meaning():
    called = False
    def fake_executor(table, column, *, datasource_name):
        nonlocal called
        called = True
        return ()
    auditor = SemanticRefutationAuditor(
        evidence_builder=lambda datasource_name: build_schema_evidence(
            "d",
            list_tables=lambda datasource_name: [{"table_name": "fact_orders", "display_name": "", "description": "", "domain": ""}],
            list_columns=lambda table_name, datasource_name: [
                {"column_name": "order_status", "description": "订单状态；cancelled=已取消/取消", "sample_values": []},
            ],
            list_metrics=lambda datasource_name: [],
            list_aliases=lambda datasource_name: [],
            list_verified_queries=lambda datasource_name: [],
        ),
        distinct_executor=fake_executor,
    )
    issue = SemanticGroundingIssue(concept="取消", failure_kind="omitted", concept_id="c1")
    result = auditor.audit(issue, evidence=auditor.evidence(datasource_name="d"), concept=_value_concept("fact_orders", "order_status", "取消"))
    assert result.confirmed is False
    assert called is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_grounding.py -k distinct_probe -q`
Expected: FAIL (`RequiredConcept` has no `target_column`; `SemanticRefutationAuditor` has no `distinct_executor`).

- [ ] **Step 3: Write minimal implementation**

Add fields to `RequiredConcept`:

```python
@dataclass(frozen=True)
class RequiredConcept:
    concept: str
    concept_id: str = ""
    concept_type: str = "other"
    supported: bool = False
    evidence: tuple[str, ...] = ()
    explanation: str = ""
    target_table: str = ""
    target_column: str = ""
    requested_value: str = ""
```

Parse them in `_parse_required_concept` (both optional):

```python
        target_table=_optional_string(value.get("target_table"), ""),
        target_column=_optional_string(value.get("target_column"), ""),
        requested_value=_optional_string(value.get("requested_value"), ""),
```

Default distinct executor (guarded read, bounded), injectable:

```python
def _default_distinct_executor(table_name: str, column_name: str, *, datasource_name: str) -> tuple[str, ...]:
    import sqlglot
    from backend.app.sql_guard.guard import guard_sql
    from backend.app.sql_guard.scope import build_default_guard_scope
    from backend.app.execution.runner import execute_guarded_sql
    from backend.app.connectors.registry import get_datasource_dialect
    # table/column have already been validated against metadata.
    dialect = get_datasource_dialect(datasource_name)
    table_sql = sqlglot.exp.to_identifier(table_name, quoted=True).sql(dialect=dialect)
    column_sql = sqlglot.exp.to_identifier(column_name, quoted=True).sql(dialect=dialect)
    sql = f"SELECT DISTINCT {column_sql} FROM {table_sql} LIMIT 1000"
    guard_result = guard_sql(
        sql,
        build_default_guard_scope(datasource_name=datasource_name),
        datasource_name=datasource_name,
        max_result_rows=1000,
    )
    if not guard_result.allowed:
        raise SemanticVerifierUnavailable(f"DISTINCT probe rejected by guard: {guard_result.reason}")
    query_result = execute_guarded_sql(guard_result, datasource_name=datasource_name)
    return tuple(str(row[0]) for row in query_result.rows)
```

Add metadata target validation before probing:

- If `target_table` is provided, confirm that table exists and owns `target_column`; otherwise abstain.
- If only `target_column` is provided, resolve it only when exactly one enabled table owns that column; duplicate column names such as `date_key` must abstain.
- Never use an LLM-supplied table/column that is not present in datasource metadata.

If a value concept's target cannot be validated, abstain instead of falling back to concept-level confirmed refutation. That is intentional conservatism: a hallucinated or ambiguous Stage A target should never create block-grade evidence. A non-value concept can still use the concept-level path independently.

Extend `audit` to probe value concepts:

```python
def _validated_target(concept: RequiredConcept, evidence: SchemaEvidence) -> tuple[str | None, str, str]:
    table = concept.target_table.strip()
    column = concept.target_column.strip()
    value = concept.requested_value.strip()
    if not column or not value:
        return None, column, value
    if table:
        return (table, column, value) if evidence.has_column(table, column) else (None, column, value)
    unique_table = evidence.unique_table_for_column(column)
    return (unique_table, column, value) if unique_table else (None, column, value)


class SemanticRefutationAuditor:
    def __init__(self, evidence_builder=build_schema_evidence, distinct_executor=_default_distinct_executor):
        self._evidence_builder = evidence_builder
        self._distinct_executor = distinct_executor

    def audit(self, issue, *, evidence, concept=None):
        name = issue.concept.strip()
        if not name:
            return RefutationAuditResult(confirmed=False, reason="No requested concept was provided by the verifier.")
        # value-level path: the question named a specific value to filter on
        if concept is not None and concept.concept_type == "value" and concept.target_column and concept.requested_value:
            return self._audit_value(name, concept, evidence)
        if evidence.has_concept_evidence(name):
            return RefutationAuditResult(confirmed=False, reason=f"Full datasource metadata contains evidence for {name!r}; deterministic audit abstained.")
        return RefutationAuditResult(confirmed=True, reason=f"Full datasource metadata contains no evidence for {name!r} across any channel.")

    def _audit_value(self, issue_concept, concept, evidence):
        table, col, value = _validated_target(concept, evidence)
        if table is None:
            return RefutationAuditResult(confirmed=False, reason="Value target was not uniquely validated in metadata; abstained.")
        # Metadata evidence/aliases/descriptions beat raw distinct values. If the
        # requested value meaning is documented, the deterministic layer abstains.
        if evidence.has_concept_evidence(value) or evidence.has_concept_evidence(issue_concept):
            return RefutationAuditResult(confirmed=False, reason=f"Metadata contains evidence for {value!r}; abstained.")
        # Cheap path: value enumerated in sample metadata -> evidence exists -> abstain.
        if evidence.has_sample_value(col, value):
            return RefutationAuditResult(confirmed=False, reason=f"{value!r} is an enumerated sample value of {col!r}; abstained.")
        try:
            actual = self._distinct_executor(table, col, datasource_name=evidence.datasource_name)
        except Exception as exc:
            return RefutationAuditResult(confirmed=False, reason=f"DISTINCT probe unavailable for {col!r}: {exc}; abstained.")
        if evidence.value_matches(value, actual):
            return RefutationAuditResult(confirmed=False, reason=f"{value!r} exists in {col!r}; abstained.")
        return RefutationAuditResult(confirmed=True, reason=f"{value!r} is absent from {col!r} (DISTINCT probe).")
```

Use `SchemaEvidence.has_sample_value()` and `SchemaEvidence.value_matches()` rather than importing private normalization helpers across modules. Add a one-line note to the extraction prompt in `prompts/semantic_grounding.py`: for a value-typed concept (a specific status/enum the question filters on), include `"target_table"`, `"target_column"`, and `"requested_value"` only when the target comes from metadata evidence; otherwise leave target fields blank so the auditor abstains.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_grounding.py -q`
Expected: PASS (all, including the two new probe tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/semantic_grounding.py backend/app/agent/prompts/semantic_grounding.py backend/tests/test_semantic_grounding.py
git commit -m "Add SELECT DISTINCT probe for absent requested values

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Remove Phase-2 TODOs, run full suite, update spec status

**Files:**
- Modify: `backend/app/agent/semantic_grounding.py` (delete `TODO(phase-2)` comment blocks now satisfied)
- Modify: `docs/superpowers/specs/2026-06-12-semantic-grounding-guard-design.md` (note Phase 2A landed)

- [ ] **Step 1: Delete the satisfied `TODO(phase-2)` blocks** in `audit()` and at the old `_concept_has_schema_evidence` site (the latter is already removed in Task 2; confirm no stale references remain via `grep -n "TODO(phase-2)" backend/app/agent/semantic_grounding.py`).

- [ ] **Step 2: Run the entire backend suite**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS (no regressions; previously 22 semantic tests + others).

- [ ] **Step 3: Update the spec** — under "Component 2", change the distinct/structured paragraphs from "TODO/phase-2" framing to "implemented (Phase 2A)", and set the value-level + channel refutation as done.

- [ ] **Step 4: Commit**

```bash
git add backend/app/agent/semantic_grounding.py docs/superpowers/specs/2026-06-12-semantic-grounding-guard-design.md
git commit -m "Mark semantic refutation Phase 2A complete

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1-2 cover "structured channel-by-channel refutation" (round-3 HIGH#1); Task 3 covers "SELECT DISTINCT for the requested value, never the proxy" (round-3 HIGH#2); the "abstain on any evidence, only refute" invariant is enforced in `audit`/`_audit_value`. Datasource scoping is satisfied because every `list_*` accessor is datasource-scoped (no global overlay used).
- **Deferred (Phase 2B):** three-way eval verdict, pinned-SQL fixtures, live smoke, per-pattern promotion gate, availability SLO — separate plan.
- **Deferred (open question):** overlay→datasource binding; until then the overlay is not an evidence source here.
- **Risk:** `_default_distinct_executor` runs in the request hot path only for value-typed unsupported concepts in `warn`/`enforce` mode; it is bounded (`LIMIT 1000`) and guarded. It is injected, so tests never hit a real DB.
