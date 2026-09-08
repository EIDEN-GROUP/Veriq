# Verifier task

Given FINDING + BEFORE/AFTER tool outputs (tests, lint, typecheck, build, targeted Playwright), output {"finding_id":"...","fixed":true|false,"reason":"..."}.
fixed=true ONLY if the failing check from evidence now passes AND no new failures appeared. Otherwise false with reason.
Never hedge: if logs are missing, fixed=false.
