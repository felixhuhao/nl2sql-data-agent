# Design Review — i18n EN/ZH Separation

- [defer] §3(B) / implementation — current chat API request shape has no `locale`; implementation must add the request override at the API/SSE boundary and ensure direct runner/test entry points keep default `zh`.
- [defer] §3(A) / tasks — "other API-surfaced user message" is intentionally broad; implementation should enumerate SSE error/fallback strings and frontend fallback text during extraction, while leaving NLU/domain strings untouched.

Verdict: **REVIEWER-CLEAR** — zero BLOCKING findings.
