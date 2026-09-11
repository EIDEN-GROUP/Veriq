# Veriq — Engineering Verification Intelligence

Production-grade, reusable GitHub Actions AI engineering agent.
**NVIDIA NIM** reasons · **deterministic tools** decide · **Slack** gates every change · **Playwright** sees the real UI.

Principle: `DETECT → EVIDENCE → ANALYZE → ASK → APPROVE → FIX → VERIFY → AUDIT → NOTIFY`.
The agent never does `DETECT → AI thinks it's OK → MODIFY CODE`. Humans stay in control of mutations;
the AI handles investigation, reasoning, proposed fixes, verification, and reporting.

---

## 1. Architecture

```
push / PR
  └─> caller repo:  uses: EIDEN-GROUP/Veriq/.github/workflows/ai-audit.yml@v1, secrets: inherit
        └─> Veriq reusable workflow (.github/workflows/ai-audit.yml)
              ├─ DETECT    scanners/project_detector.py (+ browser/detector.py)
              ├─ EVIDENCE  code/security/dependency/test/build runners (deterministic, source of truth)
              ├─ BROWSER   server.py → crawler.py (3 viewports) → screenshots/console/network/accessibility
              ├─ REDACT    agent/redact.py (runs BEFORE NIM / Slack / logs / artifacts)
              ├─ ANALYZE   agent/agent_loop.py + nim_client.py (Nemotron Ultra default, fallback chain)
              ├─ ASK       Slack two-button approval  🟢 Allow AI to fix / 🔴 Do not allow AI to fix
              ├─ APPROVE   gateway/app.py (HMAC signature, replay window, expiry, single-use, authz)
              ├─ FIX       github/patches.py on ai-agent/fix-* bot branch, max 3 attempts
              ├─ VERIFY    re-run tools + NIM verifier (fixed=true only on fresh passing evidence)
              ├─ AUDIT     reports/generator.py → artifacts/audit.json + audit.md + screenshots
              └─ NOTIFY    developer gets their audit; ADMIN GETS EVERY AUDIT regardless of decision
```

NIM is the reasoning layer. Exit codes from lint/tests/build/scanners are the verdict layer.
NIM can add context findings but can never override a deterministic CRITICAL/HIGH.

## 2. Repository layout

```
Veriq/
├── agent/        orchestrator.py nim_client.py agent_loop.py tool_registry.py
│                 permissions.py redact.py idempotency.py schemas/*.json
├── scanners/     project_detector.py code_scanner.py security_scanner.py
│                 dependency_scanner.py test_runner.py build_runner.py
├── browser/      detector.py server.py crawler.py screenshots.py accessibility.py console.py network.py
├── github/       client.py comments.py checks.py patches.py
├── slack/        client.py approval.py notifications.py formatting.py
├── gateway/      app.py          # approval gateway (deploy once per org)
├── prompts/      system.md audit.md security.md frontend.md ux.md fixer.md verifier.md
├── reports/      generator.py markdown.py
├── config/defaults.yml   action.yml   Dockerfile   requirements.txt
├── .github/workflows/ai-audit.yml    # THE reusable workflow
├── tests/test_veriq.py + fixtures/   # 16 tests, all offline
└── scripts/demo.py                   # offline end-to-end simulation
```

## 3. Consuming Veriq from another repository

Create the repo on GitHub first (you haven't yet — see §10), push this directory, tag `v1`.
Then in any target repo, add `.github/workflows/ai-agent.yml`:

```yaml
name: AI Engineering Agent
on:
  push:
    branches: [main, master, develop]
  pull_request:
  workflow_dispatch:

jobs:
  ai-agent:
    permissions:            # REQUIRED — must grant the scopes Veriq requests,
      contents: write       # otherwise GitHub rejects the call with
      pull-requests: write  # "...is only allowed actions: none, checks: none..."
      checks: write
      actions: read
    uses: EIDEN-GROUP/Veriq/.github/workflows/ai-audit.yml@v1
    secrets: inherit
```

Veriq's audit runs from the published container image
`ghcr.io/eiden-group/veriq:latest` (auto-built by
`.github/workflows/docker-publish.yml`); the caller's checkout is mounted in,
and a verified fix is pushed by the workflow to the `ai-agent/fix-*` bot branch
never to your base branch (read-only automatically for forks).

Full ready-to-copy template: `examples/ai-audit-caller.yml`.

Configurable inputs: `approval-timeout-minutes` (default `30`), `max-repair-attempts`
(default `3`), `enable-repair` (`true`), `frontend-mode` (`auto`), `gateway-url`
(your deployed `gateway/app.py` URL — required for clickable 🟢/🔴 buttons).

Optional per-repo tuning via `.ai-agent.yml` (copy from `.ai-agent.yml.example`):
enable/disable audit areas, repair policy, `deny_paths`, viewport sizes,
`security.block_on_critical`.

## 4. GitHub Secrets (all configuration lives here — nothing hardcoded)

| Secret | Value |
|---|---|
| `NIM_API_KEY` | NVIDIA build API key |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` |
| `NIM_MODEL` | `nvidia/llama-3.1-nemotron-ultra-253b-v1` (fallback chain in `NIM_FALLBACK_MODELS`) |
| `SLACK_BOT_TOKEN` | Slack app bot token |
| `SLACK_SIGNING_SECRET` | Slack app signing secret (gateway verifies every callback) |
| `SLACK_ADMIN_USER_ID` | `U0AQWT35TP0` |
| `SLACK_USER_MAP` | `{"anynonenom":"U09D383NDSM","essafar-basma":"U0ASH084QKE","marouaneakrich":"U0AQWT35TP0"}` |
| `GATEWAY_REGISTRATION_TOKEN` | optional — any random string (e.g. `openssl rand -hex 16`). When set, it's deployed to the gateway AND passed to audits; every `POST /audits` must present it as `X-Veriq-Token`. |

Approval nag bar: only findings at/above `repair.approval_min_severity` (default
`MEDIUM`) with `auto_fixable=true` trigger the developer 🟢/🔴 request. LOW/INFO
suggestions are listed in the **admin** audit (`decision: advisory-only`) and the
developer is not pinged. Admin receives every audit regardless.

For local dev copy `.env.example` → `.env` (gitignored). The user map is never logged.

## 5. NVIDIA NIM setup

1. Create a key at build.nvidia.com, store as `NIM_API_KEY`.
2. Default model `nvidia/llama-3.1-nemotron-ultra-253b-v1` (reasoning ON for audit/fix,
   OFF for verifier) was chosen because Nemotron pairs DeepSeek-R1-class reasoning with
   first-class tool-calling/instruction-following and a reasoning on/off switch — the exact
   mix this agent needs. Fallbacks: `nvidia/llama-3.3-nemotron-super-49b-v1`,
   `meta/llama-3.3-70b-instruct`. Override with `NIM_MODEL` / `NIM_FALLBACK_MODELS`.
3. All calls request `response_format: {type: json_object}`, validate against
   `agent/schemas/`, retry malformed output, then fail over to the next model.
   If every model fails, the audit still completes from deterministic evidence,
   clearly labelled “AI unavailable”.

## 6. Slack setup (bot + interactive approval)

1. Create a Slack app: `chat:write` bot scope, install to workspace, save token/secret.
2. Deploy the gateway **once per org** (any host with a public URL):
   `pip install -r requirements.txt && uvicorn gateway.app:app --port 8080`.
   Set its env: `SLACK_SIGNING_SECRET`, `SLACK_ADMIN_USER_ID`, `SLACK_USER_MAP`.
3. In Slack app settings → Interactivity → Request URL: `https://<gateway>/slack/actions`.
4. Pass the gateway URL as `gateway-url` input (or `APPROVAL_GATEWAY_URL` env).
5. How approval works: the Action first registers the audit binding with the gateway
   (`POST /audits`), then posts Block Kit with
   `approve:<audit_id>` / `reject:<audit_id>` buttons whose `value` binds
   `repository|commit|pr`. The gateway verifies HMAC + 5-min timestamp window,
   rejects unknown/expired/duplicate approvals and repository mismatches
   (an old approval can never authorize a later change), and authorizes only the
   mapped developer for that audit **or** the admin. The Action polls
   `GET /approvals/<audit_id>` until approved/rejected/expired.
   No gateway configured → approval resolves to `expired` (notify-only, zero mutations).

Approval lifecycle — **one message, edited in place; never a wall of DMs**:

```
👾  Veriq — AI Engineering Audit          (pending)
    o/repo · main · abc1234 · PR #12 · by you · AUDIT-…
    🧠 score 82/100 · 🔴0 🟠2 🟡3 🔵4 · 🧪✅ 🏗️✅ 🛡️⚠️ 🎨✅
    🔧 proposed fixes: • CODE-004 … • UI-002 …
    [🟢 Allow AI to fix] [🔴 Do not allow]        ⏱ window 30 min
        │ click 🟢  (gateway validates HMAC/authz, records decision,
        ▼   and Slack's synchronous replace_original rewrites THIS message)
🟢  Approved — AI is on it
🔧  AI applying fixes… fmt→lint→typecheck→tests→security→build→Playwright
        ▼
✅/❌  Veriq audit complete — 🟩 84/100 · CODE-004 verified · UI-002 failed
```

Clicking twice or after expiry never acts: second click answers ephemerally
(“Already recorded”), a late click flips the message to ⏱️ *window closed*.
Rejected/expired audits likewise rewrite the same message. The **admin** audit
is always a separate DM/record (independent of the developer's choice).

Legacy example kept for reference:

```
👾 AI Engineering Audit
Repository: owner/repository   PR: #123   Triggered by: github-user
Overall score: 82/100
Findings: 🔴 Critical: 0  🟠 High: 2  🟡 Medium: 3  🔵 Low: 4
Frontend: 🎨 UI/UX score: 87/100
Tests: ✅ 148 passed  ❌ 2 failed
The AI identified 3 issues that it believes can be safely fixed automatically.
Do you want the AI agent to fix them?
[🟢 Allow AI to fix]  [🔴 Do not allow AI to fix]
```

## 6b. Chat with 👾 (slash commands + optional live chat)

Talk to Veriq straight from Slack — the **gateway serves it**, no extra deploy.

| Command | What it does | Needs |
|---|---|---|
| `/scan owner/repo [branch] [workflow.yml]` | triggers a full Veriq audit on that repo (detect → scans → tests → Playwright → 👾 analysis → approval DM) | `GITHUB_API_TOKEN` + target repo has a caller workflow |
| `/audit` | alias of `/scan` | |
| `/ask <question> [owner/repo]` | NIM chat grounded in memory + the repo's stored audits (a repo token adds its audit history) | `NIM_API_KEY` |
| `/status` | your recent audits: score, severities, decision, fixes | — |
| `/remember <note>` | store a durable fact about you/project — passed through the **secret redactor** first | — |
| `/memory` | transparency: show everything 👾 stores for you (transcript stats, summary, facts) | — |
| `/clear` | erase chat **transcript** only (summary + facts survive for continuity) | — |
| `/forget` | purge *all* memory of you — transcript, summary, facts | — |
| `/help` | usage | — |
| `/veriq <anything>` | catch-all: routes by first word (`veriq scan org/r`, `veriq status`, anything else → ask) | ask needs NIM |
| DM the bot / `@veriq …` in a channel | live thread replies with full memory, until `/clear`/`/forget` | bot token + Events |
| `/remember` `/memory` `/forget` | explicit memory control (facts redacted at rest) | — |

**Memory model:** 👾 keeps a rolling transcript (60 turns, capped), and once it grows
it background-compresses the older half into a durable **summary + extracted facts**
(one NIM pass, JSON; fails safe by trimming without the LLM). Everything persists per
user for 90 days — so "what did we decide about staging?" works tomorrow. `/ask`
context = persona + your memory + the target repo's latest audit **and history**
(every CI report is stored per repo, last 30). Stored facts pass through the
redactor; `/forget` deletes the memory key entirely (on Upstash too). Slack events
are acked immediately (background answering) and deduped by `event_id` — Slack's
own retries can never double-reply.

**Privacy by design:** every `/command` reply is `response_type: ephemeral` —
visible only to the person who typed it, even in #general. Free-text chat replies
happen in-thread (DM or your own mention), never broadcasting into channels.
Per-user rate limit: 20 interactions/min, then a polite slowdown.
Lock `/scan` down with gateway env `VERIQ_ALLOWED_REPOS=owner/a,owner/b` if needed.

**Slack app wiring (5 min):**
1. *Slash Commands* → create each command (`/scan`, `/audit`, `/ask`, `/status`,
   `/help`, `/clear`, `/remember`, `/memory`, `/forget`, `/veriq`), Request URL:
   `https://veriq.eiden-group.com/slack/slash`.
2. *Event Subscriptions* → Enable → Request URL `https://veriq.eiden-group.com/slack/events`
   (already verified) → Subscribe bot events: `message.im`, `app_mentions:read`
   (+ `message.channels` only if you want @mention replies in channels).
3. Add optional gateway secrets (deploy ships them automatically):
   `NIM_API_KEY`, `SLACK_BOT_TOKEN`, `GITHUB_API_TOKEN` (fine-grained PAT:
   *Actions: read&write* + *Contents: read&write* on the repos you'll scan).
4. Missing pieces degrade with in-message instructions, never errors.

## 7. Supported frameworks & deterministic checks

Phase 1 (full): Node (npm/yarn/pnpm/bun via lockfiles + `package.json` script detection),
Python (pip/poetry/uv), Docker marker, frontend (Next/React/Vite/Vue/Nuxt/Angular/
Svelte/SvelteKit/Astro/Remix via deps + config markers). Phase 2 (interface-stable stubs
reporting “marker present”): Go, Rust, Java/Maven/Gradle, PHP, Ruby, .NET.

Checks run only what the repo declares (no phantom failures): lint/format/typecheck from
real scripts, `pytest`/npm test, `npm run build`, `npm audit`/`pip-audit`, bandit,
gitleaks, plus built-in secret/injection heuristics (SQLi, XSS, command injection, SSRF,
insecure deserialization, hardcoded secrets). NIM reasons over the outputs; it never
clears a deterministic failure.

## 8. Security model

Least-privilege workflow permissions (no `write-all`); fork PRs get read-only audits with
repair force-disabled. `agent/permissions.py` allowlists 17 tools and blocks privileged/
destructive commands (`terraform apply`, `kubectl`, `git reset --hard`, `push --force`,
cloud CLIs…) plus exfil shapes (`curl --data`, ` -d @`, `printenv`, `| bash`, `nc`…).
Tool subprocesses run with a **scrubbed environment** (`ToolRegistry._child_env`) — any
var matching `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*` plus `GITHUB_TOKEN`/`AWS_*`/
`SLACK_*`/`NIM_*`/`UPSTASH_*` is stripped, so even a policy-escaped command sees no
credentials; output is additionally redacted. The fix loop refuses to run if the
checkout is dirty (§22), and every audit publishes a commit status check.
`deny_paths` (migrations, IaC, keys, `.env*`) can never be patched even
with approval; `auth/payments/iam/infrastructure` findings require admin approval.
Optional `GATEWAY_REGISTRATION_TOKEN` secret gates gateway `/audits` registration.
`agent/redact.py` strips credentials (API keys, Slack/GitHub tokens, bearer headers,
private keys, secret env values) before NIM/Slack/logs/artifacts; `.env*`/key files are
never read. Findings carry `confidence`; weak-evidence items become `needs_human_review`
instead of auto-fixes. Below the `approval_min_severity` bar (default MEDIUM) findings
are advisory-only for the admin — the developer is never pinged. Auto-fix allowlist:
high-confidence, localized, verifiable, non-security-architecture changes (lint, types,
null checks, imports, a11y labels, CSS overflow, safe dep updates).

## 9. Frontend / UI / a11y pipeline

Detected frontends are installed, built, and served on an ephemeral localhost port.
Playwright discovers routes (App Router, `pages/`, sitemap), then visits each route at
Desktop `1440×900`, Tablet `768×1024`, Mobile `375×812`, capturing screenshots, DOM
overflow signals, console errors, failed requests, and axe-level accessibility notes.
NIM scores UI/UX and accessibility and files viewport-specific findings (subjective taste
is INFO-only; only concrete breakage reaches MEDIUM+). Playwright failure → frontend
reported `unavailable`, never fake-PASS. Screenshots + reports upload as workflow artifacts.

## 10. Creating the Veriq repo & releasing

```powershell
# Repo https://github.com/EIDEN-GROUP/Veriq already exists — push this directory once:
git init; git add -A; git commit -m "feat: Veriq v1 engineering verification agent"
git branch -M main; git remote add origin https://github.com/EIDEN-GROUP/Veriq.git
git push -u origin main; git tag v1; git push origin v1
```

Add the §4 secrets at org level (preferred — one place for all repos) or per repo.
Deploy the gateway (§6 / §14) and set `gateway-url` in callers. Version with tags
(`v1`, `v1.1`); callers pin `@v1`.

## 14. Deploying the approval gateway (do this once per org)

The gateway needs a **public `https://` URL** (Slack only delivers button clicks to public
HTTPS). Two free-friendly options. Env vars for both are listed in `.gateway.env.example`.

### Option A — VPS with auto-deploy (recommended: persistent, no sleep, no extra state)

Best free choice: **Oracle Cloud Always-Free** Ampere VM (4 CPU / 24 GB, free forever),
or any VPS. MemoryStore is used — no Redis needed. Deploys happen automatically via
`.github/workflows/deploy-gateway.yml` (push to `main`, or manual Run workflow).

**What you do (one time, ~10 minutes):**

1. **VPS access.** Note the IP + SSH username. If your key pair was generated for the
   cloud console, make sure the *private* key is at hand.
2. **Secrets in `EIDEN-GROUP/Veriq`** (Settings → Secrets and variables → Actions → New
   repository secret). All 8:
   `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (whole private key incl.
   `-----BEGIN/END OPENSSH PRIVATE KEY-----`), `VPS_DOMAIN` (see step 3),
   `SLACK_SIGNING_SECRET`, `SLACK_ADMIN_USER_ID` (`U0AQWT35TP0`), `SLACK_USER_MAP`
   (the JSON map from §4). Optional: `VPS_PORT` (default 22).
   `VPS_USER` must be `root` or have **passwordless sudo** (`echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER`).
3. **Domain.** Either an A record `veriq-gateway.<your-domain>` → VPS IP, or with no
   domain at all use `<ip-with-dashes>.nip.io` (e.g. `130-61-22-10.nip.io`) — Caddy
   still gets a real TLS cert for it. Put the result in `VPS_DOMAIN`.
4. **Open ports TCP 80 + 443** (22 for SSH is presumably already open):
   ```bash
   # Generic Ubuntu with UFW:
   sudo ufw allow 80,443/tcp && sudo ufw reload
   # Oracle Cloud Ubuntu image (iptables + cloud security list):
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo apt install -y iptables-persistent && sudo netfilter-persistent save
   ```
   Plus in the cloud console (Oracle: Networking → VCN → Security List → Add Ingress
   Rules) allow `0.0.0.0/0` on TCP 80 and 443. No ingress rule = Caddy can never be
   reached, even with the VM firewall open.
5. **Push.** `git push origin main` (or Actions → Deploy gateway to VPS → Run workflow).
   The workflow copies the files, installs Docker if missing, writes `.gateway.env`
   from secrets, and starts `gateway + Caddy`. It ends with a container health check
   and a public `https://<domain>/health` check.
6. **Wire Slack.** App → Interactivity → Request URL `https://<VPS_DOMAIN>/slack/actions`.
   Caller workflows get `gateway-url: https://<VPS_DOMAIN>`. Press 🟢 on a test audit.

**Manual fallback** (same result, no GitHub): copy `gateway/`, `docker-compose.yml`,
`Caddyfile`, `requirements-gateway.txt` to `~/veriq-gateway` on the VPS, create
`.gateway.env` from `.gateway.env.example`, set your domain in `Caddyfile`, then
`sudo docker compose up -d --build`. Update later with `git pull`-style re-copy +
`sudo docker compose up -d --build`.

### Option B — Vercel + Upstash Redis (easiest, serverless free tiers)

Vercel functions are stateless, so the gateway **must** use shared state there:
create a free Redis at upstash.com (or Vercel Marketplace → Upstash) and set its
REST credentials — `gateway/store.py` picks `UpstashStore` automatically, no code change.

```bash
npm i -g vercel
vercel link   # or: import EIDEN-GROUP/Veriq in the Vercel dashboard
vercel env add SLACK_SIGNING_SECRET
vercel env add SLACK_ADMIN_USER_ID            # U0AQWT35TP0
vercel env add SLACK_USER_MAP          # the JSON map from §4
vercel env add UPSTASH_REDIS_REST_URL         # from Upstash console
vercel env add UPSTASH_REDIS_REST_TOKEN
vercel --prod
```

Your gateway is then `https://veriq-<you>.vercel.app` → health at `/health`.
Set the Slack app's Interactivity Request URL to `https://<that>/slack/actions`
and pass `gateway-url: https://<that>` in caller workflows.

> Render/Railway free tiers sleep and lose in-memory state — usable for a demo only
> (set the Upstash vars there too). For production pick A or B.

### Wiring checklist (both options)

1. Slack app → Interactivity → Request URL = `https://<gateway>/slack/actions` → Save.
2. Caller workflow gets `gateway-url: https://<gateway>` (`APPROVAL_GATEWAY_URL` in Actions).
3. Press 🟢 on a test audit; gateway logs show 200 and the Action proceeds to FIX.
4. Without a reachable gateway, approvals safely resolve to `expired` (notify-only).

## 11. Local development & testing

```powershell
Copy-Item .env.example .env   # fill secrets, never commit
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python -m pytest tests/ -q          # 16 offline tests
python scripts/demo.py              # full simulated flow (approve → patch → verify → messages)
uvicorn gateway.app:app --port 8080 # approval gateway
python -m agent.orchestrator --target ../some-project --artifacts artifacts
```

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `NIM_API_KEY not configured` | Secret missing → deterministic-only audit still completes |
| Approval always expires | `gateway-url` unset or Slack Request URL wrong; check gateway logs |
| `401 bad slack signature` | `SLACK_SIGNING_SECRET` mismatch or clock skew > 5 min |
| Slack "didn't respond with the challenge" | gateway too old — redeploy; `/slack/actions` must echo `url_verification` challenges (fixed in `fix(gateway): echo Slack url_verification challenge...`) |
| `403 unauthorized approver` | Clicker isn't the mapped dev or admin; extend `SLACK_USER_MAP` |
| `unauthorized` pulling the image manually | It's **private by design** — CI logs in with `GITHUB_TOKEN` (`packages: read`). For a local/VPS pull: `echo <PAT-with-read:packages> \| docker login ghcr.io -u <user> --password-stdin`, or publish the package if you prefer open pulls |
| Frontend `unavailable` | No start script or port timeout; check `start_app` logs in artifacts |
| Fork PR does nothing but audit | Intended: read-only on untrusted code |
| Duplicate Slack messages | Same commit re-audited → idempotency key skips; check `_state/` |

## 13. Caller example & finding schema

See `.github/workflows/ai-audit.yml` (reusable) and `agent/schemas/finding.schema.json`.
Every finding: `id, severity (CRITICAL|HIGH|MEDIUM|LOW|INFO), category, title, description,
file, line, evidence, recommendation, auto_fixable, confidence`.
