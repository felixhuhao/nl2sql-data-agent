- [BLOCKING] design.md §3 Signal contract vs §5 Altitude — The design commits to a top-level field named `coverage_match_strength` in the signal contract, but later defers "Field name / where on the result dict it sits" to implementation. Field name and placement are part of the data-shape/interface contract, so this is internally inconsistent above the altitude boundary. Suggested direction: either commit to the top-level `coverage_match_strength` shape everywhere, or revise §3 to leave only the semantic contract while naming/placement is explicitly non-contractual.
- [BLOCKING] design.md Digest / §7 Status — The design embeds live status fields in `design.md` (`Status: In-review` in the digest and a §7 status paragraph). The design contract says live status lives only in `roadmap.md`; `design.md` should carry the approval/sign-off line, not a live status line. Suggested direction: remove live status from `design.md` and leave only the approval record.

Verdict: **CHANGES NEEDED** — 2 blocking findings.

---

## Maker response — round 2 (Claude)

- [BLOCKING #1 interface/altitude inconsistency] **Resolved.** `coverage_match_strength` (top-level, `float ∈ [0,1]`) is now committed as contractual in §3; removed the conflicting §5 deferral and added an explicit note that only the value's *computation* (normalization curve + how the merge derives internal confidence) is deferred, not the name/placement.
- [BLOCKING #2 live status in design.md] **Resolved.** Removed `Status: In-review` from the digest and renamed §7 to "Approval" carrying only the sign-off record (Approval: not-yet) + a pointer that live status lives in `roadmap.md`. Synced `design.html` (removed the status pill, §7 → Approval).

Status → In-review (round 2) via roadmap.md. Re-check requested.

---

## Reviewer pass — round 2 (Codex)

- [BLOCKING #1 interface/altitude inconsistency] **Resolved.** §3 now commits to top-level `coverage_match_strength: float ∈ [0,1]`; §5 explicitly defers only the computation/normalization curve, not the field name or placement.
- [BLOCKING #2 live status in design.md] **Resolved.** The digest no longer carries `Status: In-review`; §7 is now an approval/sign-off record and points live status to `roadmap.md`.

Verdict: **REVIEWER-CLEAR** — zero remaining `BLOCKING` findings.
