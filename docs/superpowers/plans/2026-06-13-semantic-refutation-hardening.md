# Semantic Refutation Hardening (Phase 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blunt substring corroboration in `SemanticRefutationAuditor` with structured, channel-by-channel, datasource-scoped refutation plus a `SELECT DISTINCT` probe for value-level concepts, so a confirmed refutation is trustworthy enough to gate a hard block.

**Architecture:** A new `SchemaEvidence` value object is built once per datasource from the existing datasource-scoped metadata accessors (`list_tables`, `list_columns`, `list_metrics`, `list_aliases`, `list_verified_queries`). The auditor confirms a refutation only when a requested concept is absent from **every** channel. For value-typed concepts that name a `(target_column, requested_value)`, an injected executor runs a bounded `SELECT DISTINCT` to confirm the value is genuinely absent before confirming. The auditor still *only refutes* — finding evidence makes it abstain, never assert support.

**Tech Stack:** Python 3.12, dataclasses, sqlglot (already used), pytest. Metadata via `backend.app.metadata.service`. Execution via `backend.app.execution.runner` / `backend.app.sql_guard.guard`.

**Scope note:** This is Phase **2A** (runtime block-grade evidence). The promotion eval methodology (three-way verdict, pinned-SQL fixtures, live smoke, per-pattern gate, availability SLO) is **Phase 2B**, a separate plan that consumes this one. Overlay→datasource binding remains an open question and is intentionally **not** used as evidence here; the datasource's own metadata is authoritative.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/agent/schema_evidence.py` | `SchemaEvidence` model + `build_schema_evidence(datasource_name)` channel builder + normalization | **Create** |
| `backend/app/agent/semantic_grounding.py` | `SemanticRefutationAuditor` uses structured evidence + DISTINCT probe; `RequiredConcept` gains `target_column`/`requested_value`; `audit()` takes evidence + concept | **Modify** |
| `backend/app/agent/prompts/semantic_grounding.py` | extraction prompt asks for `target_column`/`requested_value` on value-typed concepts | **Modify** |
| `backend/tests/test_schema_evidence.py` | unit tests for evidence builder | **Create** |
| `backend/tests/test_semantic_grounding.py` | extend: structured refutation, DISTINCT probe, parse of new fields | **Modify** |

**Test command (always run from repo root):**
`PYTHONPATH=. backend/.venv/bin/python -m pytest <path> -q`

---

## Task 1: `SchemaEvidence` model + builder

**Files:**
- Create: `backend/app/agent/schema_evidence.py`
- Test: `backend/tests/test_schema_evidence.py`

The builder pulls every datasource-scoped channel and normalizes tokens with the **same** normalization already used in `semantic_grounding._normalize_evidence_text` (lowercased ASCII alnum runs + CJK runs), so concept matching is consistent across the codebase.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_schema_evidence.py
from backend.app.agent.schema_evidence import SchemaEvidence, build_schema_evidence


def _fake_sources():
    tables = [{"table_name": "fact_orders", "display_name": "订单", "description": "订单事实表", "domain": "sales"}]
    columns = {
        "fact_orders": [
            {"column_name": "order_status", "description": "订单状态", "sample_values": ["paid", "completed", "refunded"]},
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
    assert evidence.column_values("order_status") == ("paid", "completed", "refunded")
    assert evidence.columns_with_value("refunded") == ("order_status",)
    assert evidence.columns_with_value("cancelled") == ()


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_schema_evidence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.agent.schema_evidence'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/agent/schema_evidence.py
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata import service


def _normalize(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+|[㐀-鿿]+", str(text).casefold()))


@dataclass(frozen=True)
class SchemaEvidence:
    datasource_name: str
    # one normalized blob per channel for concept-presence checks
    _channels: tuple[str, ...] = ()
    # column_name -> ordered distinct sample values (original casing)
    _column_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # normalized value -> column names that enumerate it
    _value_index: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def has_concept_evidence(self, concept: str) -> bool:
        token = _normalize(concept)
        if not token:
            return False
        return any(token in channel for channel in self._channels)

    def column_values(self, column_name: str) -> tuple[str, ...]:
        return self._column_values.get(column_name, ())

    def columns_with_value(self, value: str) -> tuple[str, ...]:
        return self._value_index.get(_normalize(value), ())


ListTables = Callable[..., list[dict]]
ListColumns = Callable[..., list[dict]]
ListMetrics = Callable[..., list[dict]]
ListAliases = Callable[..., list[dict]]
ListVerified = Callable[..., list[dict]]


def build_schema_evidence(
    datasource_name: str = DEFAULT_DATASOURCE,
    *,
    list_tables: ListTables = service.list_tables,
    list_columns: ListColumns = service.list_columns,
    list_metrics: ListMetrics = service.list_metrics,
    list_aliases: ListAliases = service.list_aliases,
    list_verified_queries: ListVerified = service.list_verified_queries,
) -> SchemaEvidence:
    channels: list[str] = []
    column_values: dict[str, tuple[str, ...]] = {}
    value_index: dict[str, list[str]] = {}

    tables = list_tables(datasource_name=datasource_name)
    channels.append(_normalize(" ".join(
        f"{t.get('table_name','')} {t.get('display_name','')} {t.get('description','')} {t.get('domain','')}"
        for t in tables
    )))

    column_blob_parts: list[str] = []
    for table in tables:
        for column in list_columns(table_name=table["table_name"], datasource_name=datasource_name):
            name = column.get("column_name", "")
            column_blob_parts.append(f"{name} {column.get('description','')}")
            samples = tuple(str(v) for v in (column.get("sample_values") or []))
            if samples:
                column_values[name] = samples
                for value in samples:
                    value_index.setdefault(_normalize(value), [])
                    if name not in value_index[_normalize(value)]:
                        value_index[_normalize(value)].append(name)
    channels.append(_normalize(" ".join(column_blob_parts)))

    channels.append(_normalize(" ".join(
        f"{m.get('name','')} {m.get('label','')} {m.get('description','')}"
        for m in list_metrics(datasource_name=datasource_name)
    )))
    channels.append(_normalize(" ".join(
        a.get("alias", "") for a in list_aliases(datasource_name=datasource_name)
    )))
    channels.append(_normalize(" ".join(
        f"{q.get('question','')}" for q in list_verified_queries(datasource_name=datasource_name)
    )))

    return SchemaEvidence(
        datasource_name=datasource_name,
        _channels=tuple(channel for channel in channels if channel),
        _column_values=column_values,
        _value_index={key: tuple(value) for key, value in value_index.items()},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_schema_evidence.py -q`
Expected: PASS (2 passed)

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

Delete the now-unused `_concept_has_schema_evidence`, `_normalize_evidence_text`, `_full_schema_context`, the `FullSchemaContextBuilder` alias, and the `build_schema_context` import. Update `semantic_guard_node` to build/cache evidence instead of the context string:

```python
# in semantic_guard_node, replace the `full_context = _full_schema_context(...)` block and audit loop:
    evidence = _evidence(state, auditor)
    concepts = _required_concepts(state, verifier, evidence)
    ...
    for issue in result.issues:
        concept = _concept_for_issue(issue, unsupported_concepts)
        refutation = auditor.audit(issue, evidence=evidence, concept=concept)
        state.grounding_warnings.append(_warning_from_issue(issue, refutation))
```

Add helpers and adjust `_required_concepts` to accept evidence instead of a string (it only needs the datasource-derived context for the LLM call — pass `state.full_schema_context` built lazily for the *verifier prompt*, but the audit uses `evidence`):

```python
def _evidence(state: AgentState, auditor: SemanticRefutationAuditor) -> SchemaEvidence:
    if state.schema_evidence is None:
        state.schema_evidence = auditor.evidence(datasource_name=state.datasource_name)
    return state.schema_evidence


def _concept_for_issue(issue, concepts):
    by_id = {c.concept_id: c for c in concepts}
    by_name = {c.concept: c for c in concepts}
    return by_id.get(issue.concept_id) or by_name.get(issue.concept)
```

Add `schema_evidence: "SchemaEvidence | None" = None` to `AgentState` (and reset it in `repair.reset_failure_state`). For the verifier prompt's `full_schema_context`, keep building the rendered string via `build_schema_context` in `_required_concepts` only when calling `verifier.extract_required_concepts` (cached on `state.full_schema_context`).

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

When a requested concept is a **value** (e.g. "status = cancelled") that is absent from enumerated `sample_values`, run a bounded `SELECT DISTINCT <column>` to confirm it is genuinely absent before confirming the refutation. The probe runs **only** when the concept carries `(target_column, requested_value)`; otherwise the concept-level path (Task 2) applies. The probe never tests a substituted proxy value.

**Files:**
- Modify: `backend/app/agent/semantic_grounding.py` (`RequiredConcept`, parse, `audit`)
- Modify: `backend/app/agent/prompts/semantic_grounding.py` (extraction prompt)
- Test: `backend/tests/test_semantic_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
def _value_concept(column: str, value: str) -> RequiredConcept:
    return RequiredConcept(
        concept=f"{column}={value}", concept_id="c1", concept_type="value",
        supported=False, target_column=column, requested_value=value,
    )


def test_distinct_probe_confirms_when_requested_value_absent_in_data():
    probed = {}
    def fake_executor(column, *, datasource_name):
        probed["column"] = column
        return ("paid", "completed", "refunded")  # 'cancelled' absent
    auditor = SemanticRefutationAuditor(
        evidence_builder=lambda datasource_name: build_schema_evidence(
            "d",
            list_tables=lambda datasource_name: [],
            list_columns=lambda table_name, datasource_name: [],
            list_metrics=lambda datasource_name: [],
            list_aliases=lambda datasource_name: [],
            list_verified_queries=lambda datasource_name: [],
        ),
        distinct_executor=fake_executor,
    )
    issue = SemanticGroundingIssue(concept="order_status=cancelled", failure_kind="substituted", concept_id="c1")
    result = auditor.audit(issue, evidence=auditor.evidence(datasource_name="d"), concept=_value_concept("order_status", "cancelled"))
    assert result.confirmed is True
    assert probed["column"] == "order_status"


def test_distinct_probe_abstains_when_requested_value_present_in_data():
    auditor = SemanticRefutationAuditor(
        evidence_builder=lambda datasource_name: build_schema_evidence(
            "d",
            list_tables=lambda datasource_name: [],
            list_columns=lambda table_name, datasource_name: [],
            list_metrics=lambda datasource_name: [],
            list_aliases=lambda datasource_name: [],
            list_verified_queries=lambda datasource_name: [],
        ),
        distinct_executor=lambda column, *, datasource_name: ("paid", "cancelled"),
    )
    issue = SemanticGroundingIssue(concept="order_status=cancelled", failure_kind="substituted", concept_id="c1")
    result = auditor.audit(issue, evidence=auditor.evidence(datasource_name="d"), concept=_value_concept("order_status", "cancelled"))
    assert result.confirmed is False
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
    target_column: str = ""
    requested_value: str = ""
```

Parse them in `_parse_required_concept` (both optional):

```python
        target_column=_optional_string(value.get("target_column"), ""),
        requested_value=_optional_string(value.get("requested_value"), ""),
```

Default distinct executor (guarded read, bounded), injectable:

```python
def _default_distinct_executor(column: str, *, datasource_name: str) -> tuple[str, ...]:
    from backend.app.sql_guard.guard import guard_sql
    from backend.app.sql_guard.scope import build_default_guard_scope
    from backend.app.execution.runner import execute_guarded_sql
    # column is a bare identifier from metadata; resolve its table via metadata, not the LLM.
    table = _table_for_column(column, datasource_name)
    if table is None:
        raise SemanticVerifierUnavailable(f"No table found for column {column!r}.")
    sql = f"SELECT DISTINCT {column} FROM {table} LIMIT 1000"
    guard_result = guard_sql(sql, build_default_guard_scope(datasource_name=datasource_name), datasource_name=datasource_name)
    if not guard_result.allowed:
        raise SemanticVerifierUnavailable(f"DISTINCT probe rejected by guard: {guard_result.reason}")
    query_result = execute_guarded_sql(guard_result, datasource_name=datasource_name)
    return tuple(str(row[0]) for row in query_result.rows)
```

Add `_table_for_column` using `service.list_tables` + `service.list_columns` (first table that has the column). Extend `audit` to probe value concepts:

```python
    def __init__(self, evidence_builder=build_schema_evidence, distinct_executor=_default_distinct_executor):
        self._evidence_builder = evidence_builder
        self._distinct_executor = distinct_executor

    def audit(self, issue, *, evidence, concept=None):
        name = issue.concept.strip()
        if not name:
            return RefutationAuditResult(confirmed=False, reason="No requested concept was provided by the verifier.")
        # value-level path: the question named a specific value to filter on
        if concept is not None and concept.concept_type == "value" and concept.target_column and concept.requested_value:
            return self._audit_value(concept, evidence)
        if evidence.has_concept_evidence(name):
            return RefutationAuditResult(confirmed=False, reason=f"Full datasource metadata contains evidence for {name!r}; deterministic audit abstained.")
        return RefutationAuditResult(confirmed=True, reason=f"Full datasource metadata contains no evidence for {name!r} across any channel.")

    def _audit_value(self, concept, evidence):
        col, value = concept.target_column, concept.requested_value
        # cheap path: value enumerated in sample metadata -> evidence exists -> abstain
        if _normalize(value) in {_normalize(v) for v in evidence.column_values(col)}:
            return RefutationAuditResult(confirmed=False, reason=f"{value!r} is an enumerated sample value of {col!r}; abstained.")
        try:
            actual = self._distinct_executor(col, datasource_name=evidence.datasource_name)
        except SemanticVerifierUnavailable as exc:
            return RefutationAuditResult(confirmed=False, reason=f"DISTINCT probe unavailable for {col!r}: {exc}; abstained.")
        if _normalize(value) in {_normalize(v) for v in actual}:
            return RefutationAuditResult(confirmed=False, reason=f"{value!r} exists in {col!r}; abstained.")
        return RefutationAuditResult(confirmed=True, reason=f"{value!r} is absent from {col!r} (DISTINCT probe).")
```

Import `_normalize` from `schema_evidence` (or re-export). Add a one-line note to the extraction prompt in `prompts/semantic_grounding.py`: for a value-typed concept (a specific status/enum the question filters on), include `"target_column"` and `"requested_value"`.

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
