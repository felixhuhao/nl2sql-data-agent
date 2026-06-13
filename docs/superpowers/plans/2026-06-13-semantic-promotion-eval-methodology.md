# Semantic Promotion Eval Methodology (Phase 2B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the semantic-guard eval runner into a promotion-readiness instrument: a three-way verdict (pass / fail / inconclusive) that decouples verifier availability from semantic correctness, a per-pattern promotion gate that emits a machine-readable promoted-patterns artifact, availability reported as a separate SLO, pinned-SQL verifier fixtures, and a small non-gating live-smoke tier.

**Architecture:** All gating logic is pure functions over `list[SemanticEvalResult]` so it is unit-testable with synthetic results and never needs a live provider. `verifier_unavailable` becomes `inconclusive` (not `fail`); promotion is computed over *completed* observations only (`pass + fail`). A pattern is promotable only when it has enough completed observations, **zero** false-confirmed refutations, and all positive/negative schema fixtures pass. Promoted patterns are written to `evals/promoted_patterns.json`; runtime `enforce` consults that artifact so enforcement never goes broad before a pattern proves safe.

**Tech Stack:** Python 3.12, dataclasses, pytest, PyYAML (already used by the runner). Runner: `scripts/run_semantic_guard_eval.py`. Corpus: `evals/semantic_guard_cases.yaml`. Tests: `backend/tests/test_semantic_guard_eval_runner.py`.

**Depends on:** Phase 2A (`SemanticRefutationAuditor` structured refutation + DISTINCT probe) — landed (`91bc427`, `ef04796`).

**Test command (from repo root):** `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -q`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `scripts/run_semantic_guard_eval.py` | three-way verdict; promotion gate; availability/chronic aggregation; pinned-SQL fixture case path; smoke selection | **Modify** |
| `backend/app/agent/promoted_patterns.py` | load/read the promoted-patterns artifact for runtime enforce gating | **Create** |
| `backend/app/agent/semantic_grounding.py` | `enforce` block requires the issue's pattern to be promoted | **Modify** |
| `evals/semantic_guard_cases.yaml` | add pinned-SQL fixtures + `smoke` tier cases | **Modify** |
| `evals/promoted_patterns.json` | generated artifact (starts empty: no pattern promoted) | **Create** |
| `backend/tests/test_semantic_guard_eval_runner.py` | verdict, gate, availability, fixture validation tests | **Modify** |
| `backend/tests/test_promoted_patterns.py` | artifact load + runtime gate tests | **Create** |

---

## Task 1: Three-way verdict (pass / fail / inconclusive)

Today `SemanticEvalResult` is binary `passed: bool`, and `_validate_expected_semantic` *fails* a case when `verifier_unavailable` (line 357). Decouple them: unavailability becomes `inconclusive`, never a semantic `fail`. Correctness is computed over completed (`pass + fail`) only.

**Files:**
- Modify: `scripts/run_semantic_guard_eval.py` (`SemanticEvalResult`, `_validate_expected_semantic`, `_summary`, `main` exit)
- Test: `backend/tests/test_semantic_guard_eval_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# add to backend/tests/test_semantic_guard_eval_runner.py
def test_verifier_unavailable_is_inconclusive_not_fail():
    result = runner.SemanticEvalResult(case_id="x", question="q", tags=[])
    result.verifier_unavailable = True
    runner._validate_expected_semantic(result, {"warning": True})
    assert result.status == "inconclusive"
    assert result.passed is True          # not a semantic failure
    assert result.inconclusive is True


def test_semantic_mismatch_is_fail_even_when_completed():
    result = runner.SemanticEvalResult(case_id="x", question="q", tags=[], warnings=[])
    runner._validate_expected_semantic(result, {"warning": True})  # expected a warning, got none
    assert result.status == "fail"
    assert result.inconclusive is False


def test_summary_separates_completed_from_inconclusive():
    completed_pass = runner.SemanticEvalResult(case_id="a", question="q", tags=[])
    completed_fail = runner.SemanticEvalResult(case_id="b", question="q", tags=[])
    completed_fail.fail("mismatch")
    inconclusive = runner.SemanticEvalResult(case_id="c", question="q", tags=[])
    inconclusive.mark_inconclusive("verifier unavailable")
    summary = runner._summary([completed_pass, completed_fail, inconclusive])
    assert summary["completed_cases"] == 2
    assert summary["inconclusive_cases"] == 1
    assert summary["passed_cases"] == 1   # over completed only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k "inconclusive or completed" -q`
Expected: FAIL (`SemanticEvalResult` has no `status` / `inconclusive` / `mark_inconclusive`).

- [ ] **Step 3: Write minimal implementation**

In `SemanticEvalResult` add the field and methods:

```python
    inconclusive: bool = False

    def fail(self, message: str) -> None:
        self.passed = False
        self.messages.append(message)

    def mark_inconclusive(self, message: str) -> None:
        self.inconclusive = True
        self.messages.append(message)

    @property
    def status(self) -> str:
        if self.inconclusive:
            return "inconclusive"
        return "pass" if self.passed else "fail"
```

Rewrite the unavailability branch of `_validate_expected_semantic` (replace the `allow_verifier_unavailable` fail):

```python
def _validate_expected_semantic(result: SemanticEvalResult, expected: dict[str, Any]) -> None:
    expected_warning = expected.get("warning")
    result.expected_warning = bool(expected_warning) if expected_warning is not None else None
    if result.verifier_unavailable:
        result.mark_inconclusive("semantic verifier unavailable (inconclusive, not a semantic failure)")
        return
    if expected_warning is not None and bool(result.warnings) != bool(expected_warning):
        result.fail(f"expected warning={expected_warning}, got {bool(result.warnings)}")
```

Extend `_summary` (correctness is over completed only):

```python
    completed = [r for r in results if not r.inconclusive]
    return {
        "total_cases": len(results),
        "completed_cases": len(completed),
        "inconclusive_cases": sum(1 for r in results if r.inconclusive),
        "passed_cases": sum(1 for r in completed if r.passed),
        # ...existing keys unchanged...
    }
```

Change `main`'s exit so inconclusive never fails the run:

```python
    return 0 if all(result.passed for result in results if not result.inconclusive) else 1
```

- [ ] **Step 4: Run tests** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -q` — Expected: PASS (update `test_run_case_with_retries_returns_later_pass` only if it asserted on the old summary shape; it does not).

- [ ] **Step 5: Commit**

```bash
git add scripts/run_semantic_guard_eval.py backend/tests/test_semantic_guard_eval_runner.py
git commit -m "Add three-way eval verdict: pass/fail/inconclusive

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Per-pattern promotion gate + artifact

A pattern is **promotable** only when, over **completed** observations: it has at least `min_completed` of them, **zero** false-confirmed refutations, and every positive and negative schema fixture passed. Output a machine-readable artifact.

**Files:**
- Modify: `scripts/run_semantic_guard_eval.py` (add `evaluate_promotion_readiness`, `write_promoted_patterns`, wire into `main`)
- Create: `evals/promoted_patterns.json` (initial content `{"promoted": []}`)
- Test: `backend/tests/test_semantic_guard_eval_runner.py`

- [ ] **Step 1: Write the failing test**

```python
def _pattern_case(case_id, *, pattern="p", passed=True, inconclusive=False,
                  expected_warning=None, warnings=None, case_type="workflow", tags=None):
    r = runner.SemanticEvalResult(case_id=case_id, question="q", tags=tags or [],
                                  case_type=case_type, promotion_pattern=pattern)
    r.expected_warning = expected_warning
    r.warnings = warnings or []
    r.passed = passed
    r.inconclusive = inconclusive
    return r


def test_promotion_blocks_pattern_with_false_confirmed_refutation():
    results = [
        _pattern_case("ok", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        # a supported case wrongly given a confirmed refutation = false positive block
        _pattern_case("bad", expected_warning=False, warnings=[{"refutation_confirmed": True}], passed=False),
    ]
    readiness = runner.evaluate_promotion_readiness(results, min_completed=2)
    assert readiness["p"]["promotable"] is False
    assert "false_confirmed" in readiness["p"]["reason"]


def test_promotion_requires_min_completed_observations():
    results = [_pattern_case("a", expected_warning=True, warnings=[{"refutation_confirmed": True}])]
    readiness = runner.evaluate_promotion_readiness(results, min_completed=5)
    assert readiness["p"]["promotable"] is False
    assert "insufficient" in readiness["p"]["reason"]


def test_promotion_ignores_inconclusive_in_denominator():
    results = [
        _pattern_case("a", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("b", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        _pattern_case("c", inconclusive=True),  # excluded from completed
    ]
    # fixture-coverage gate disabled here; this test isolates the inconclusive denominator.
    readiness = runner.evaluate_promotion_readiness(results, min_completed=2, min_positive=0, min_negative=0)
    assert readiness["p"]["completed"] == 2
    assert readiness["p"]["promotable"] is True


def test_promotion_blocks_when_a_completed_case_failed():
    results = [
        _pattern_case("ok", expected_warning=True, warnings=[{"refutation_confirmed": True}]),
        # completed semantic failure (expected a warning, produced none) — not a fixture
        _pattern_case("miss", expected_warning=True, warnings=[], passed=False),
    ]
    readiness = runner.evaluate_promotion_readiness(results, min_completed=2, min_positive=0, min_negative=0)
    assert readiness["p"]["promotable"] is False
    assert "failed" in readiness["p"]["reason"]


def test_promotion_requires_minimum_fixture_coverage():
    # 20 passing workflow cases, but zero positive/negative fixtures
    results = [
        _pattern_case(f"c{i}", expected_warning=True, warnings=[{"refutation_confirmed": True}])
        for i in range(20)
    ]
    readiness = runner.evaluate_promotion_readiness(results, min_completed=20)
    assert readiness["p"]["promotable"] is False
    assert "fixture coverage" in readiness["p"]["reason"]


def test_promotion_passes_with_completed_failures_absent_and_fixtures_present():
    positive = _pattern_case("pos", case_type="verifier_only", tags=["positive_schema"],
                             expected_warning=True, warnings=[{"refutation_confirmed": True}])
    negative = _pattern_case("neg", case_type="verifier_only", tags=["negative_schema"], expected_warning=False)
    workflow = [_pattern_case(f"w{i}", expected_warning=True, warnings=[{"refutation_confirmed": True}]) for i in range(18)]
    readiness = runner.evaluate_promotion_readiness([positive, negative, *workflow], min_completed=20)
    assert readiness["p"]["promotable"] is True


def test_write_promoted_patterns_lists_only_promotable(tmp_path):
    readiness = {"p": {"promotable": True, "reason": "ok"}, "q": {"promotable": False, "reason": "x"}}
    path = tmp_path / "promoted_patterns.json"
    runner.write_promoted_patterns(readiness, path=path)
    import json
    assert json.loads(path.read_text())["promoted"] == ["p"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k promotion -q`
Expected: FAIL (`evaluate_promotion_readiness` / `write_promoted_patterns` undefined).

- [ ] **Step 3: Write minimal implementation**

```python
import json

DEFAULT_MIN_COMPLETED = 20
DEFAULT_MIN_POSITIVE_FIXTURES = 1
DEFAULT_MIN_NEGATIVE_FIXTURES = 1


def evaluate_promotion_readiness(
    results: list[SemanticEvalResult],
    *,
    min_completed: int = DEFAULT_MIN_COMPLETED,
    min_positive: int = DEFAULT_MIN_POSITIVE_FIXTURES,
    min_negative: int = DEFAULT_MIN_NEGATIVE_FIXTURES,
) -> dict[str, dict[str, Any]]:
    by_pattern: dict[str, list[SemanticEvalResult]] = {}
    for result in results:
        # smoke is a non-gating tier; it never contributes to promotion.
        if result.promotion_pattern and "smoke" not in result.tags:
            by_pattern.setdefault(result.promotion_pattern, []).append(result)

    readiness: dict[str, dict[str, Any]] = {}
    for pattern, pattern_results in by_pattern.items():
        completed = [r for r in pattern_results if not r.inconclusive]
        failed_completed = [r for r in completed if not r.passed]
        false_confirmed = [
            r for r in completed
            if r.expected_warning is False and any(w.get("refutation_confirmed") for w in r.warnings)
        ]
        positive = [r for r in completed if _is_verifier_positive_case(r)]
        negative = [r for r in completed if _is_verifier_negative_case(r)]

        if len(completed) < min_completed:
            promotable, reason = False, f"insufficient completed observations ({len(completed)}/{min_completed})"
        elif false_confirmed:
            promotable, reason = False, f"false_confirmed refutation on {len(false_confirmed)} case(s)"
        elif failed_completed:
            # any completed case that did not pass blocks promotion, fixture or not.
            promotable, reason = False, f"{len(failed_completed)} completed case(s) failed"
        elif len(positive) < min_positive or len(negative) < min_negative:
            promotable, reason = False, (
                f"insufficient fixture coverage (+{len(positive)}/{min_positive}, -{len(negative)}/{min_negative})"
            )
        else:
            promotable, reason = True, "all completed checks passed"

        readiness[pattern] = {
            "promotable": promotable,
            "reason": reason,
            "completed": len(completed),
            "failed_completed": len(failed_completed),
            "inconclusive": len(pattern_results) - len(completed),
            "false_confirmed": len(false_confirmed),
            "positive_fixtures": len(positive),
            "negative_fixtures": len(negative),
        }
    return readiness


def write_promoted_patterns(readiness: dict[str, dict[str, Any]], *, path: Path) -> None:
    promoted = sorted(pattern for pattern, info in readiness.items() if info["promotable"])
    path.write_text(json.dumps({"promoted": promoted}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

Wire into `main` after `results` are collected (write artifact, print readiness), gated behind a `--write-promoted` flag so an ad-hoc run never silently overwrites the artifact:

```python
    parser.add_argument("--write-promoted", action="store_true",
                        help="Recompute and overwrite evals/promoted_patterns.json from this run.")
    parser.add_argument("--min-completed", type=int, default=DEFAULT_MIN_COMPLETED)
    # ... after results:
    readiness = evaluate_promotion_readiness(results, min_completed=args.min_completed)
    if args.write_promoted:
        write_promoted_patterns(readiness, path=Path("evals/promoted_patterns.json"))
```

- [ ] **Step 4: Run tests** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k promotion -q` — Expected: PASS.

- [ ] **Step 5: Create the initial artifact and commit**

```bash
printf '{\n  "promoted": []\n}\n' > evals/promoted_patterns.json
git add scripts/run_semantic_guard_eval.py evals/promoted_patterns.json backend/tests/test_semantic_guard_eval_runner.py
git commit -m "Add per-pattern promotion gate and promoted-patterns artifact

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Availability SLO + chronically-unavailable case ids

Availability is reported separately and never gates. A case whose every repeated observation is inconclusive is **chronically unavailable** and surfaced explicitly (it must not silently vanish from the denominator).

**Files:**
- Modify: `scripts/run_semantic_guard_eval.py` (add `availability_report`, include in `_render_report`)
- Test: `backend/tests/test_semantic_guard_eval_runner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_availability_report_separates_and_lists_chronic_case_ids():
    results = [
        _pattern_case("a"),                       # completed
        _pattern_case("a", inconclusive=True),    # same case id, one inconclusive obs
        _pattern_case("b", inconclusive=True),    # only ever inconclusive -> chronic
        _pattern_case("b", inconclusive=True),
    ]
    report = runner.availability_report(results)
    assert report["completed_observations"] == 1
    assert report["inconclusive_observations"] == 3
    assert report["chronically_unavailable_case_ids"] == ["b"]   # "a" had >=1 completed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k availability -q`
Expected: FAIL (`availability_report` undefined).

- [ ] **Step 3: Write minimal implementation**

```python
def availability_report(results: list[SemanticEvalResult]) -> dict[str, Any]:
    completed = sum(1 for r in results if not r.inconclusive)
    inconclusive = sum(1 for r in results if r.inconclusive)
    by_case: dict[str, list[SemanticEvalResult]] = {}
    for result in results:
        by_case.setdefault(result.case_id, []).append(result)
    chronic = sorted(
        case_id for case_id, observations in by_case.items()
        if observations and all(o.inconclusive for o in observations)
    )
    total = completed + inconclusive
    return {
        "completed_observations": completed,
        "inconclusive_observations": inconclusive,
        "availability_rate": round(completed / total, 4) if total else None,
        "chronically_unavailable_case_ids": chronic,
    }
```

Add an "## Availability (SLO — non-gating)" section to `_render_report` printing these fields. Keep it visually separate from the promotion section so no one mistakes availability for a semantic verdict.

- [ ] **Step 4: Run tests** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k availability -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_semantic_guard_eval.py backend/tests/test_semantic_guard_eval_runner.py
git commit -m "Report verifier availability as a separate SLO

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Pinned-SQL verifier fixtures

The existing `verifier_only` case type tests extraction only. Add a `fixture` case type that pins `{question, full_schema_context, sql}` and runs the grounding check + refutation audit on the **fixed** SQL, isolating the guard from generator nondeterminism. This is the deterministic promotion-evidence tier.

**Files:**
- Modify: `scripts/run_semantic_guard_eval.py` (`_run_case` dispatch + `_run_fixture_case`)
- Modify: `evals/semantic_guard_cases.yaml` (add `type: fixture` cases for the promoted pattern)
- Test: `backend/tests/test_semantic_guard_eval_runner.py`

- [ ] **Step 1: Write the failing test** (uses a fake verifier + fake auditor, no live provider)

```python
def test_run_fixture_case_runs_grounding_and_refutation_on_pinned_sql():
    class _FakeVerifier:
        def extract_required_concepts(self, request):
            from backend.app.agent.semantic_grounding import ConceptExtractionResult, RequiredConcept
            return ConceptExtractionResult(concepts=(RequiredConcept(concept="删除率", concept_id="c1", supported=False),))
        def check_grounding(self, request):
            from backend.app.agent.semantic_grounding import GroundingCheckResult, SemanticGroundingIssue
            return GroundingCheckResult(ok=False, issues=(SemanticGroundingIssue(concept="删除率", failure_kind="substituted", concept_id="c1"),))

    class _FakeAuditor:
        def evidence(self, *, datasource_name):
            return object()
        def audit(self, issue, *, evidence, concept=None):
            from backend.app.agent.semantic_grounding import RefutationAuditResult
            return RefutationAuditResult(confirmed=True, reason="absent")

    result = runner.SemanticEvalResult(case_id="f1", question="查看删除率趋势", tags=["fixture"], case_type="fixture")
    case = {
        "id": "f1", "question": "查看删除率趋势",
        "full_schema_context": "# Tables\n- fact_orders",
        "sql": "SELECT countIf(order_status='refunded')/count(*) FROM fact_orders",
        "expected": {"warning": True, "refutation_confirmed": True},
    }
    runner._run_fixture_case(result, case, verifier=_FakeVerifier(), auditor=_FakeAuditor())
    assert result.warning_count == 1
    assert result.warnings[0]["refutation_confirmed"] is True
    assert result.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k fixture -q`
Expected: FAIL (`_run_fixture_case` undefined).

- [ ] **Step 3: Write minimal implementation**

```python
def _run_fixture_case(result, case, *, verifier, auditor) -> None:
    from backend.app.agent.semantic_grounding import (
        ConceptExtractionRequest, GroundingCheckRequest, analyze_sql_semantic_facts,
        _concept_for_issue,   # reuse runtime id+name matching, not id-only
        _warning_from_issue,  # reuse the same warning shape as runtime
    )
    full_context = case.get("full_schema_context")
    sql = case.get("sql")
    if not isinstance(full_context, str) or not full_context.strip() or not isinstance(sql, str) or not sql.strip():
        result.fail("fixture case requires full_schema_context and sql")
        return
    try:
        extraction = verifier.extract_required_concepts(ConceptExtractionRequest(
            question=result.question, full_schema_context=full_context,
            datasource_name=result.datasource_name, datasource_dialect=result.datasource_dialect))
        unsupported = tuple(c for c in extraction.concepts if not c.supported)
        if not unsupported:
            result.semantic_ok = True
        else:
            facts = analyze_sql_semantic_facts(sql, datasource_dialect=result.datasource_dialect)
            grounding = verifier.check_grounding(GroundingCheckRequest(
                question=result.question, sql=sql, concepts=unsupported, sql_facts=facts,
                datasource_name=result.datasource_name, datasource_dialect=result.datasource_dialect))
            evidence = auditor.evidence(datasource_name=result.datasource_name)
            for issue in grounding.issues:
                concept = _concept_for_issue(issue, unsupported)  # same id+name fallback as runtime
                refutation = auditor.audit(issue, evidence=evidence, concept=concept)
                result.warnings.append(_warning_from_issue(issue, refutation))
            result.semantic_ok = grounding.ok
    except Exception as exc:
        # Availability is not a semantic verdict: an outage is inconclusive, never a fail.
        result.verifier_unavailable = True
        result.mark_inconclusive(f"semantic verifier unavailable: {exc}")
        return
    result.warning_count = len(result.warnings)
    _validate_expected_fixture(result, case.get("expected") or {})


def _validate_expected_fixture(result, expected) -> None:
    if result.verifier_unavailable:
        result.mark_inconclusive("semantic verifier unavailable")
        return
    expected_warning = expected.get("warning")
    result.expected_warning = bool(expected_warning) if expected_warning is not None else None
    if expected_warning is not None and bool(result.warnings) != bool(expected_warning):
        result.fail(f"expected warning={expected_warning}, got {bool(result.warnings)}")
    if "refutation_confirmed" in expected:
        actual = bool(result.warnings) and all(w.get("refutation_confirmed") for w in result.warnings)
        if actual != bool(expected["refutation_confirmed"]):
            result.fail(f"expected refutation_confirmed={expected['refutation_confirmed']}, got {actual}")
```

Dispatch in `_run_case`: after the `verifier_only` branch, add `if result.case_type == "fixture": _run_fixture_case(result, case, verifier=semantic_verifier, auditor=auditor); return result`.

Two consistency edits to existing code:

1. **`_run_verifier_only_case` outage is also inconclusive.** Its except handler currently does `result.verifier_unavailable = True; result.fail(...)`. Change the `fail(...)` to `mark_inconclusive(...)` for the same reason (an outage must not count as a semantic failure). Add a regression test asserting a raising fake verifier yields `status == "inconclusive"` for a `verifier_only` case.
2. **Pinned-SQL fixtures count as schema coverage.** Broaden the predicates so the fixture tier satisfies the Task 2 coverage gate:

```python
def _is_verifier_positive_case(result: SemanticEvalResult) -> bool:
    return result.case_type in {"verifier_only", "fixture"} and "positive_schema" in result.tags


def _is_verifier_negative_case(result: SemanticEvalResult) -> bool:
    return result.case_type in {"verifier_only", "fixture"} and "negative_schema" in result.tags
```

- [ ] **Step 4: Run tests** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_semantic_guard_eval_runner.py -k fixture -q` — Expected: PASS.

- [ ] **Step 5: Add corpus fixtures + commit.** Add 2-3 `type: fixture` cases to `evals/semantic_guard_cases.yaml` under `promotion_pattern: concept_absent_full_metadata` (e.g. 删除率 substitution, 删除的订单 omission, a valid 退款率 negative). Each carries `full_schema_context`, `sql`, and `expected`.

```bash
git add scripts/run_semantic_guard_eval.py evals/semantic_guard_cases.yaml backend/tests/test_semantic_guard_eval_runner.py
git commit -m "Add pinned-SQL verifier fixtures for promotion evidence

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Non-gating live-smoke tier + runtime promoted-pattern gate

A small live-generator smoke set (the known risky shapes) confirms the guard still fires in the real pipeline, but never gates promotion. Then close the loop: runtime `enforce` only blocks for a **promoted** pattern, so enforcement cannot go broad before evidence earns it.

**Files:**
- Modify: `evals/semantic_guard_cases.yaml` (tag 5 workflow cases `smoke`)
- Modify: `scripts/run_semantic_guard_eval.py` (`--smoke-only` selection; smoke results excluded from `evaluate_promotion_readiness`; report smoke separately)
- Create: `backend/app/agent/promoted_patterns.py`
- Modify: `backend/app/agent/semantic_grounding.py` (enforce requires promoted pattern)
- Test: `backend/tests/test_promoted_patterns.py`, `backend/tests/test_semantic_grounding.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_promoted_patterns.py
from backend.app.agent.promoted_patterns import is_pattern_promoted, load_promoted_patterns


def test_load_promoted_patterns_reads_artifact(tmp_path):
    path = tmp_path / "promoted_patterns.json"
    path.write_text('{"promoted": ["concept_absent_full_metadata"]}', encoding="utf-8")
    assert load_promoted_patterns(path=path) == frozenset({"concept_absent_full_metadata"})


def test_is_pattern_promoted_defaults_false_when_missing(tmp_path):
    assert is_pattern_promoted("anything", path=tmp_path / "nope.json") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_promoted_patterns.py -q`
Expected: FAIL (`ModuleNotFoundError: backend.app.agent.promoted_patterns`).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/agent/promoted_patterns.py
from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import PROJECT_ROOT

_DEFAULT_PATH = PROJECT_ROOT / "evals" / "promoted_patterns.json"


def load_promoted_patterns(*, path: Path = _DEFAULT_PATH) -> frozenset[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    promoted = data.get("promoted") if isinstance(data, dict) else None
    return frozenset(str(p) for p in promoted) if isinstance(promoted, list) else frozenset()


def is_pattern_promoted(pattern: str | None, *, path: Path = _DEFAULT_PATH) -> bool:
    return bool(pattern) and pattern in load_promoted_patterns(path=path)
```

**The pattern must come from the refutation rule, not a corpus label.** Runtime has no eval corpus, so a confirmed refutation must self-identify *which deterministic rule* confirmed it. Add `pattern: str = ""` to `RefutationAuditResult` and set it in each confirm branch of `SemanticRefutationAuditor`:

- concept absent from every channel → `pattern="concept_absent_full_metadata"`
- value absent after the DISTINCT probe → `pattern="value_absent_distinct_probe"`

The eval corpus's `promotion_pattern` labels are defined to **match these rule names**, which closes the eval→runtime loop: evidence collected under `concept_absent_full_metadata` promotes exactly the runtime rule that emits it. Thread `pattern` from the refutation onto the warning in `_warning_from_issue` (pass the `RefutationAuditResult`, which it already receives), then gate enforce on it:

The block must be triggered by — and its message must name — **only** the promoted+confirmed warnings, never an unrelated confirmed-but-unpromoted warning that happens to ride along:

```python
    blocking = [
        w for w in state.grounding_warnings
        if w.get("refutation_confirmed") and is_pattern_promoted(w.get("refutation_pattern"))
    ]
    if mode == "enforce" and blocking:
        state.stopped_at = "semantic_guard"
        state.error = _semantic_block_message(blocking)   # only the warnings that caused the block
```

`_semantic_block_message` already filters by `refutation_confirmed`; passing it `blocking` (not all `grounding_warnings`) ensures an unpromoted confirmed warning is never named in the block. `_warning_from_issue` adds `"refutation_pattern": refutation.pattern`. Add `test_semantic_grounding.py` tests: (a) enforce + refutation_confirmed but **unpromoted** rule → warns, does NOT block; (b) promoted rule → blocks; (c) one promoted + one unpromoted confirmed warning → blocks, and the error message names only the promoted concept (with `promoted_patterns` path monkeypatched to a tmp file).

- [ ] **Step 4: Run tests** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests/test_promoted_patterns.py backend/tests/test_semantic_grounding.py -q` — Expected: PASS.

- [ ] **Step 5: Smoke tier + commit.** Tag five workflow cases `smoke` (deletion-rate substitution, deleted-orders omission, refund adjacent status, valid refund rate, rank correlation), exclude `smoke`-tagged results from `evaluate_promotion_readiness`, and add a `--smoke-only` selector. Print smoke results in their own non-gating report section.

```bash
git add scripts/run_semantic_guard_eval.py backend/app/agent/promoted_patterns.py backend/app/agent/semantic_grounding.py evals/semantic_guard_cases.yaml backend/tests/test_promoted_patterns.py backend/tests/test_semantic_grounding.py
git commit -m "Gate enforce on promoted patterns; add non-gating smoke tier

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Full suite + deployment doc

- [ ] **Step 1: Run the entire backend suite** — `PYTHONPATH=. backend/.venv/bin/python -m pytest backend/tests -q` — Expected: PASS (455+ plus new tests).
- [ ] **Step 2: Update the spec** rollout section: production `semantic_guard_mode` stays `off` until a pattern is promoted; `warn` for evidence collection; `enforce` only with a non-empty `promoted_patterns.json`; availability is a separate SLO and `enforce` still fails open on outage.
- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-12-semantic-grounding-guard-design.md
git commit -m "Document promotion-gated enforce rollout

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Requirement coverage:** three-way verdict (Task 1), per-pattern gate + artifact (Task 2), availability-as-separate-SLO + chronic ids (Task 3), pinned-SQL fixtures (Task 4), non-gating smoke tier + promoted-pattern-gated enforce / "no broad enforce rollout" (Task 5).
- **Decoupling invariant:** `inconclusive` is never in the correctness denominator (Tasks 1-3); the gate computes over completed only; availability never gates.
- **Promotion gates (all must pass, Task 2):** ≥ `min_completed` completed observations; `false_confirmed == 0` (airtight deterministic refutation, the double-gate philosophy); **zero failed completed cases** (fixture or not); and **minimum positive/negative fixture coverage present** (not merely "no failures") — so a pattern cannot promote on absence of evidence.
- **Availability never pollutes the verdict:** every verifier-outage path (`_run_fixture_case`, `_run_verifier_only_case`) calls `mark_inconclusive`, never `fail`, so an outage is excluded from the correctness denominator rather than counted as a semantic failure.
- **Block scoping:** runtime `enforce` blocks on, and names, only promoted+confirmed warnings (Task 5).
- **Pure-function testability:** Tasks 1-3 and the gate are unit-tested with synthetic `SemanticEvalResult` lists; Task 4 uses fake verifier/auditor — no task needs a live provider to pass.
- **Risk:** `_run_fixture_case` reuses `_warning_from_issue` from `semantic_grounding` to keep warning shape identical to runtime; if that helper's signature changes, the fixture path must follow.
- **Open dependency:** Task 5 adds a self-identifying `pattern` onto `RefutationAuditResult` (the deterministic rule that confirmed) and threads it to the warning so runtime enforce can consult the artifact; the corpus `promotion_pattern` labels are defined to match those rule names. This is the one runtime change in an otherwise eval-tooling plan.
