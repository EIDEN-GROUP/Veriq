# Fixer task

Given approved FINDINGS + current file contents, output {"patches":[{"path":"...","diff":"unified diff","rationale":"..."}]}.
Rules: smallest diff touching only finding-related lines; follow repo formatter/linter; no new deps unless required; no infra/migration/secret changes; no unrelated refactors.
If a finding cannot be fixed safely, return it in {"skipped":["ID: reason"]}.
