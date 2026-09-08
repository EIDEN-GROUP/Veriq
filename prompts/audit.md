# Audit task

Given PROJECT summary + deterministic tool EVIDENCE (lint, typecheck, tests, build, scanners), produce:
{"overall_score": 0-100, "findings": [Finding...]}

Scoring: start 100, -25 CRITICAL, -10 HIGH, -5 MEDIUM, -2 LOW, -0 INFO (floor 0).
Every finding needs id (CAT-001...), severity, category, title, description, file, line, evidence (verbatim), recommendation, auto_fixable, confidence.
auto_fixable=true ONLY if: confidence>=0.8, localized single-file change, verifiable by rerunning a tool, no auth/payment/infra/migration/secret impact.
Otherwise needs_human_review=true.
