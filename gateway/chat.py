"""Slack chat layer for 👾 Veriq: slash commands + optional free-text chat.

Privacy rule (product requirement): every `/command` answer is RESPONSE_TYPE=EPHEMERAL —
visible only to the person who typed it, even in a public channel. Free-text chat
(DMs to the app, or @-mentions in channels) replies in-thread publicly, which is
the natural chat etiquette.

Commands:
  /scan owner/repo [branch] [workflow.yml]   trigger a Veriq audit via GitHub API
  /audit   -> alias of /scan
  /ask <question> [about owner/repo]          NIM chat, remembering per-user context
  /status                                     audits you requested + latest repo results
  /clear                                      forget the assistant's memory of this chat
  /help                                       usage
  /veriq <sub|free text>                      routes to any of the above (default: ask)

All handlers take the shared store; Slack/GitHub I/O go through small functions
that tests monkeypatch — the routing logic itself is pure.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from gateway import llm

REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+)$")

_RATE: dict[str, list[float]] = {}
RATE_MAX = 20          # interactions per user per RATE_WINDOW_S
RATE_WINDOW_S = 60
_BOT_ID: str | None = None

HELP_TXT = (
    "*👾 Veriq commands*  (every /command answer is private — only you see it)\n"
    "• `/scan <owner/repo> [branch] [workflow.yml]` — run a full audit (detect → scan → tests → UI → 👾 analysis; approval via Slack)\n"
    "• `/audit` — alias of `/scan`\n"
    "• `/ask <question>` — chat with the Veriq engineer; add `<owner/repo>` to use its audit history as context\n"
    "• `/status` — the latest audits this Veriq has run for you\n"
    "• `/remember <note>` — ask me to keep a durable fact (secrets auto-redacted)\n"
    "• `/memory` — show everything I have stored about you (transcript stats, summary, facts)\n"
    "• `/clear` — erase chat *transcript* only (summary+facts kept)\n"
    "• `/forget` — erase ALL memory of me about you, completely\n"
    "• `/help` — this message\n"
    "DM the bot anytime, or ping @veriq in a channel for chat."
)


def _throttle(user_id: str) -> bool:
    now = time.time()
    hits = [t for t in _RATE.get(user_id, []) if now - t < RATE_WINDOW_S]
    hits.append(now)
    _RATE[user_id] = hits
    return len(hits) > RATE_MAX


def _eph(text: str, blocks: list | None = None) -> dict:
    return {"response_type": "ephemeral", "text": text[:2900],
            "blocks": blocks or [{"type": "section",
                                  "text": {"type": "mrkdwn", "text": text[:2900]}}]}


def _http(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict | list]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def _gh_api(method: str, url: str, body: dict | None = None):
    return _http(method, url, os.environ.get("GITHUB_API_TOKEN", ""), body)


def _find_workflow(repo: str, hint: str | None):
    """Locate the caller's Veriq workflow file: explicit hint, path/name heuristics."""
    if hint:
        return hint
    status, data = _gh_api("GET", f"https://api.github.com/repos/{repo}/actions/workflows?per_page=100")
    if status != 200:
        return None
    wanted = ("ai-audit.yml", "ai-agent.yml", "veriq")
    for wf in data.get("workflows", []):
        name, path = str(wf.get("name", "")).lower(), str(wf.get("path", "")).lower()
        if wf.get("state") != "active":
            continue
        if path.endswith(wanted) or any(w in path for w in wanted) or "veriq" in name or "engineering agent" in name:
            return path.rsplit("/", 1)[-1]
    return None


def _trigger_scan(text: str) -> dict:
    if not os.environ.get("GITHUB_API_TOKEN"):
        return _eph(":lock: Scan triggering isn't configured on this gateway yet "
                    "(secret `GITHUB_API_TOKEN`). Everything else — /ask, /status — works. "
                    "Link README §3 in a repo to get audits on push/PR anyway.")
    parts = text.split()
    if not parts or not REPO_RE.match(parts[0]):
        return _eph(":warning: Usage: `/scan owner/repo [branch] [workflow.yml]`")
    repo, branch, hint = parts[0], (parts[1] if len(parts) > 1 else ""), (" ".join(parts[2:]) or None)
    wf = _find_workflow(repo, hint)
    if not wf:
        return _eph(f":file_folder: No Veriq workflow found in `{repo}` — add "
                    f"`examples/ai-audit-caller.yml` from EIDEN-GROUP/Veriq as "
                    f"`.github/workflows/ai-agent.yml` (README §3), then `/scan {repo}`.")
    if os.environ.get("VERIQ_ALLOWED_REPOS", ""):
        allowed = {r.strip().lower() for r in os.environ["VERIQ_ALLOWED_REPOS"].split(",") if r.strip()}
        if repo.lower() not in allowed:
            return _eph(f":no_entry: `{repo}` isn't in this gateway's allowed scan list.")
    ref = branch or _default_branch(repo)
    if not ref:
        return _eph(f":x: Couldn't resolve a branch for `{repo}` (bad name or no token access).")
    status, _ = _gh_api("POST",
                        f"https://api.github.com/repos/{repo}/actions/workflows/{urllib.parse.quote(wf)}/dispatches",
                        {"ref": ref})
    if status not in (200, 204):
        return _eph(f":x: GitHub rejected the dispatch (HTTP {status}) for `{repo}` "
                    f"workflow `{wf}` @ `{ref}` — check the token's Actions:write/contents scope.")
    run_url = f"https://github.com/{repo}/actions/workflows/{urllib.parse.quote(wf)}"
    return _eph(f":white_check_mark: Veriq scan started on `{repo}` @ `{ref}` — expect the 👾 analysis "
                f"in a few minutes: approval DM + admin audit + PR comment as usual.\n"
                f":link: <{run_url}|watch it here>  ·  then `/status` to see results land.")


def _default_branch(repo: str) -> str:
    status, data = _gh_api("GET", f"https://api.github.com/repos/{repo}")
    return str(data.get("default_branch", "")) if status == 200 else ""


def _memory_context(store, user_id: str) -> list[dict]:
    """Long-term memory injection: summary + saved facts about this user."""
    mem = store.memory(user_id)
    ctx: list[dict] = []
    parts = []
    if mem.get("summary"):
        parts.append(f"CONVERSATION MEMORY SUMMARY: {str(mem['summary'])[:2500]}")
    if mem.get("facts"):
        parts.append("SAVED FACTS: " + " | ".join(str(f)[:200] for f in mem["facts"][-20:]))
    if parts:
        ctx.append({"role": "system", "content": "\n".join(parts)})
    return ctx


def _ask(store, text: str, user_name: str) -> dict:
    question = text.strip()
    if not question:
        return _eph(":question: Ask me something: `/ask how is the auth layer holding up eiden-group/web`")
    repo = None
    parts = question.split()
    if len(parts) > 1 and REPO_RE.match(parts[-1]):
        repo, question = parts.pop(), " ".join(parts)
    elif len(parts) > 2 and REPO_RE.match(parts[0]):
        repo, question = parts[0], " ".join(parts[1:])
    if not repo:
        recs = store.recent_results(20)
        repo = recs[0].get("repository") if recs else None
    ctx: list[dict] = [{"role": "system", "content": llm.PERSONA}]
    ctx += _memory_context(store, _CURRENT_USER[0])
    if repo:
        latest = store.latest_result(repo)
        if latest:
            ctx.append({"role": "system", "content": "LATEST VERIQ AUDIT (authoritative evidence, "
                        f"verbatim): {json.dumps(latest, default=str)[:4000]}"})
        hist = store.repo_history(repo, 5)
        if len(hist) > 1:
            ctx.append({"role": "system", "content": "AUDIT HISTORY (oldest-first, compact): "
                        + json.dumps(hist[:0:-1], default=str)[:3000]})
    ctx += store.chat(_CURRENT_USER[0])[-16:]
    ctx.append({"role": "user", "content": f"{question}" + (f"\n(context repo: {repo})" if repo else "")})
    answer = llm.chat(ctx)
    if answer is None:
        return _eph(":cloud_lightning: The AI backend is unreachable right now — your chat will be "
                    "remembered; ask again in a moment, or check `AI unavailable` in my system prompt.")
    _remember_turn(store, question, answer)
    if len(answer) > 2900:
        answer = answer[:2890] + "…"
    return _eph(answer, [{"type": "section", "text": {"type": "mrkdwn", "text": answer}}])


TRANSCRIPT_CONSOLIDATE_AT = 44  # compress older half once transcript grows past this


def maybe_consolidate(store, user_id: str) -> None:
    """Background task: keep memory durable but bounded — fold the older half of a long
    transcript into the rolling summary + extract facts (NIM, cheap model pass)."""
    mem = store.memory(user_id)
    if len(mem.get("chat", [])) < TRANSCRIPT_CONSOLIDATE_AT:
        return
    keep, fold = mem["chat"][-16:], mem["chat"][:-16]
    prompt = (
        "Compress chat history into memory. Reply ONLY JSON: "
        '{"summary": string (<=900 chars, merge prior summary + these turns), '
        '"facts": [durable short user facts, max 6, omit trivia]}\n'
        f"PRIOR SUMMARY: {mem.get('summary','')[:2500]}\n"
        f"PRIOR FACTS: {json.dumps(mem.get('facts', [])[-20:])[:1500]}\n"
        f"NEW TURNS: {json.dumps(fold)[:14000]}"
    )
    out = llm.chat([{"role": "system", "content": "You maintain 👾 Veriq's memory store. JSON "
                     "only, no prose."},
                    {"role": "user", "content": prompt}], json_mode=True, max_tokens=600)
    if not out:
        # NIM down: drop the middle anyway so memory never explodes; keep facts verbatim.
        mem["chat"] = keep
        store.set_memory(user_id, mem)
        return
    try:
        data = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        mem["chat"] = keep
        store.set_memory(user_id, mem)
        return
    if str(data.get("summary", "")).strip():
        mem["summary"] = str(data["summary"])[:2500]
    if isinstance(data.get("facts"), list):
        fresh = [str(f)[:200] for f in data["facts"]][:6]
        mem["facts"] = (mem.get("facts", []) + [f for f in fresh if f not in mem.get("facts", [])])[-40:]
        if len(mem["facts"]) > 12:
            mem["facts"] = mem["facts"][-12:]
    mem["chat"] = keep
    store.set_memory(user_id, mem)


def _remember_fact(store, user_id: str, text: str) -> dict:
    note = text.strip()
    if not note:
        return _eph(":pencil: Tell me what to keep: `/remember the staging DB is on db-03` "
                    "(goes through the secret redactor first)")
    try:
        from agent.redact import redact_text
        note = redact_text(note)
    except ImportError:
        pass
    mem = store.memory(user_id)
    mem["facts"] = (mem.get("facts", []) + [note[:300]])[-40:]
    store.set_memory(user_id, mem)
    return _eph(f":brain: Stored. I now keep *{len(mem['facts'])}* facts about you.\n"
                f"`{note[:250]}`\n(`/memory` reviews it all · `/forget` purges.)")


def _show_memory(store, user_id: str) -> dict:
    mem = store.memory(user_id)
    n = len(mem.get("chat", []))
    body = [f":brain:  *What 👾 Veriq remembers about you*  (`{user_id}`)"]
    body.append(f"• chat transcript kept: *{n} turns* (rolling window, 90-day idle expiry)")
    summary = str(mem.get("summary") or "").strip()
    body.append(":memo:  long-term summary:\n```\n" + (summary[:2000] or "— none yet (chat with me /ask and it builds itself) —") + "\n```")
    facts = mem.get("facts") or []
    if facts:
        body.append(":pushpin:  saved facts:")
        body += [f"  {i+1}. `{str(f)[:200]}`" for i, f in enumerate(facts[-15:])]
    else:
        body.append(":pushpin:  no explicit facts yet — add via `/remember <note>`")
    return _eph("\n".join(body))


# helpers for per-request user binding (set by router before calling handlers)
_CURRENT_USER: list[str] = [""]


def _remember_turn(store, q: str, a: str) -> None:
    hist = store.chat(_CURRENT_USER[0])
    hist += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    store.remember(_CURRENT_USER[0], hist)


def _status(store, user_name: str, admin_id: str, user_id: str) -> dict:
    recs = store.recent_results(25)
    is_admin = bool(admin_id) and user_id == admin_id
    mine = [r for r in recs if r.get("slack_user") == user_id or r.get("triggered_by") == user_name or is_admin]
    if not mine:
        return _eph(":inbox_tray: No audits reported to this gateway yet. Start one: `/scan owner/repo`")
    lines = []
    for r in mine[:8]:
        fx = f" · fixed {r.get('fixed',0)}" if r.get("fixed") else ""
        lines.append(f"• `{r['repository']}` `{str(r.get('commit',''))[:7]}` — "
                     f"{r.get('score')}/100 · 🟢{r.get('cr',0)} 🟠{r.get('hi',0)} 🟡{r.get('me',0)} 🔵{r.get('lo',0)}"
                     f" · {r.get('decision')}{fx} · <{r.get('run_url') or '#'}|run>")
    header = f":shield: *Veriq — last audits this gateway knows*{' (admin view: all)' if is_admin and len(mine)>1 else ''}"
    return _eph("\n".join([header] + lines))


def handle_command(store, fields: dict) -> dict:
    """Route a signed Slash-Command form. NEVER leaks other users' data: ephemeral."""
    cmd = str(fields.get("command", "")).lower().lstrip("/")
    user_id = str(fields.get("user_id", ""))
    user_name = str(fields.get("user_name", ""))
    text = str(fields.get("text", ""))
    _CURRENT_USER[0] = user_id
    if _throttle(user_id):
        return _eph(":hourglass: You're firing fast — wait a minute before the next one.")
    if cmd in ("scan", "audit"):
        return _trigger_scan(text)
    if cmd == "ask":
        return _ask(store, text, user_name)
    if cmd == "status":
        return _status(store, user_name, os.environ.get("SLACK_ADMIN_USER_ID", ""), user_id)
    if cmd in ("remember", "keep"):
        return _remember_fact(store, user_id, text)
    if cmd == "memory":
        return _show_memory(store, user_id)
    if cmd == "clear":
        mem = store.memory(user_id)
        mem["chat"] = []
        store.set_memory(user_id, mem)
        return _eph(":broom: Transcript cleared — summary and facts kept so I still know the "
                    "context. Use /forget for a full purge.")
    if cmd == "forget":
        store.forget(user_id)
        return _eph(":wastebasket: Erased. Transcript, summary, facts — all memory of me about "
                    "you is gone. (Audit results stay as repo records.)")
    if cmd == "help":
        return _eph(HELP_TXT)
    if cmd == "veriq":
        head, _, rest = text.partition(" ")
        sub = head.lower()
        if sub in ("scan", "audit") and rest:
            return _trigger_scan(rest)
        if sub == "status":
            return _status(store, user_name, os.environ.get("SLACK_ADMIN_USER_ID", ""), user_id)
        if sub == "clear":
            mem = store.memory(user_id)
            mem["chat"] = []
            store.set_memory(user_id, mem)
            return _eph(":broom: Transcript cleared.")
        if sub in ("remember", "keep") and rest:
            return _remember_fact(store, user_id, rest)
        if sub == "memory":
            return _show_memory(store, user_id)
        if sub == "forget":
            store.forget(user_id)
            return _eph(":wastebasket: All memory erased.")
        if sub == "help":
            return _eph(HELP_TXT)
        if not text.strip():
            return _eph(HELP_TXT)
        return _ask(store, rest if sub == "ask" else text, user_name)
    return _eph(f":grey_question: Unknown command `/{cmd}` — try /help")


# ---------------------------------------------------------------- free text ----
def _post_slack(channel: str, text: str, thread_ts: str = "") -> None:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        return
    payload = {"channel": channel, "text": text[:2900]}
    if thread_ts:
        payload.update(thread_ts=thread_ts)
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {tok}",
                                        "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def bot_user_id() -> str:
    global _BOT_ID
    if _BOT_ID:
        return _BOT_ID
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        return ""
    req = urllib.request.Request("https://slack.com/api/auth.test",
                                 headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        if data.get("ok"):
            _BOT_ID = str(data.get("user_id", ""))
    except Exception:
        pass
    return _BOT_ID


def handle_message_event(store, event: dict) -> bool:
    """Events-API message callback: answer DMs to the bot and @veriq mentions.
    Returns True if handled (so the app can stay quiet otherwise)."""
    if event.get("bot_id") or event.get("subtype"):
        return False
    channel = str(event.get("channel", ""))
    text = str(event.get("text", "")).strip()
    user_id = str(event.get("user", ""))
    if not text or not user_id:
        return False
    is_dm = channel.startswith("D")
    mentioned = f"<@{bot_user_id()}>" in text if bot_user_id() else False
    if is_dm:
        text = re.sub(r"^(hi|hey|hello|yo)[\s,.!]+", "", text, flags=re.I) or text
    if not (is_dm or mentioned):
        return False                      # noise in channels: stay silent
    clean = text.replace(f"<@{bot_user_id()}>", "").strip() if mentioned else text
    if not clean:
        _post_slack(channel, ":wave: Ask me anything — `/help` lists my commands (they answer privately).",
                    event.get("ts", ""))
        return True
    if _throttle(user_id):
        return False
    _CURRENT_USER[0] = user_id
    rep = _ask(store, clean, str(event.get("user_name", user_id)))
    answer = str(rep.get("blocks", [{}])[0].get("text", {}).get("text", rep.get("text", "")))
    if answer.startswith(":cloud_lightning:"):
        answer += " (in chat I stay silent until NIM is back)"
        return True
    _post_slack(channel, answer, event.get("ts", ""))
    return True
