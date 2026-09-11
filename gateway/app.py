"""Veriq approval gateway (deploy once per org; Actions polls it).

Security: validates Slack signature + timestamp (replay window), binds every
decision to (audit_id, repository, commit, pr), enforces single-use + expiry,
and authorizes approver = mapped dev for that audit OR admin.
Run: uvicorn gateway.app:app --port 8080. Slack Event URL -> /slack/actions.

State: MemoryStore by default (single VPS process). Set UPSTASH_REDIS_REST_URL +
UPSTASH_REDIS_REST_TOKEN for shared state on Vercel serverless / multi-replica.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from gateway import chat
from gateway.store import get_store

app = FastAPI(title="veriq-approval-gateway")
store = get_store()


def _challenge_from_body(body: bytes) -> str | None:
    """Detect Slack's url_verification challenge in either JSON or form(payload) shape."""
    candidates: list[dict] = []
    try:
        data = json.loads(body.decode() or "{}")
        if isinstance(data, dict):
            candidates.append(data)
    except (ValueError, UnicodeDecodeError):
        pass
    try:
        import urllib.parse
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
        if "payload" in fields:
            candidates.append(json.loads(fields["payload"]))
        if "challenge" in fields:
            candidates.append({"type": "url_verification", "challenge": fields["challenge"]})
    except (ValueError, UnicodeDecodeError):
        pass
    for c in candidates:
        if isinstance(c, dict) and c.get("challenge"):
            # Slack docs say type=url_verification, but observed saves omit it.
            # A bare {"token","challenge"} must still echo, else Save sees only errors.
            if c.get("type") == "url_verification" or "type" not in c:
                return str(c["challenge"])
    return None


def reset_state() -> None:
    """Test helper: wipe pending + decisions (memory backend)."""
    store.clear_all()


def _signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    mac = hmac.new(_signing_secret().encode(), f"v0:{timestamp}:".encode() + body,
                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={mac}", signature)


def _user_map() -> dict[str, str]:
    try:
        return json.loads(os.environ.get("SLACK_USER_MAP", "{}"))
    except json.JSONDecodeError:
        return {}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "backend": type(store).__name__}


def _state_blocks(audit_id: str, emoji: str, title: str, detail: str, footer: str) -> list[dict]:
    """Self-contained mirrors of slack.formatting state blocks (gateway image has no slack pkg)."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji}  {title}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"`{audit_id}`  ·  {footer}"}]},
    ]


@app.post("/audits")
def register_audit(payload: dict, x_veriq_token: str = Header(default="")) -> dict:
    """Called by the Action when it posts the approval request.
    If GATEWAY_REGISTRATION_TOKEN is set, callers must present it (X-Veriq-Token):
    stops URL-leak spam of fake pending audits. Reads/decisions always verified via HMAC."""
    reg_token = os.environ.get("GATEWAY_REGISTRATION_TOKEN", "")
    if reg_token and not hmac.compare_digest(x_veriq_token, reg_token):
        raise HTTPException(401, "bad registration token")
    aid = str(payload["audit_id"])
    store.set_pending(aid, {**payload, "created": time.time(),
                            "expires": time.time() + int(payload.get("timeout_minutes", 30)) * 60})
    return {"ok": True}


@app.get("/approvals/{audit_id}")
def get_decision(audit_id: str) -> dict:
    dec = store.get_decision(audit_id)
    if dec:
        return dec
    pend = store.get_pending(audit_id)
    if pend and time.time() > pend["expires"]:
        expired = {"decision": "expired", "audit_id": audit_id}
        store.set_decision(audit_id, expired)
        return expired
    return {"decision": "pending", "audit_id": audit_id}


@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks,
                       x_slack_signature: str = Header(default=""),
                       x_slack_request_timestamp: str = Header(default="")) -> Response:
    """Events API endpoint: challenge echo, signed event acks (must reply <3s — so chat
    answering runs as a background task), and event_id dedupe against Slack retries."""
    body = await request.body()
    challenge = _challenge_from_body(body)
    if challenge is not None:
        return PlainTextResponse(challenge)
    if not _signing_secret() or not verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad slack signature")
    try:
        data = json.loads(body.decode())
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("type") == "event_callback":
        event = data.get("event") or {}
        eid = str(data.get("event_id") or
                  f"{event.get('channel')}:{event.get('ts')}:{event.get('user')}")
        if str(event.get("type", "")) == "message" and not store.event_seen(eid):
            background_tasks.add_task(_chat_safe, event)  # ack now, answer in threadpool
    return JSONResponse({"ok": True})


def _chat_safe(event: dict) -> None:
    try:
        chat.handle_message_event(store, event)
    except Exception as exc:  # a broken chat must never poison the queue
        print(f"chat event error: {type(exc).__name__}")


@app.post("/slack/slash")
async def slack_slash(request: Request, background_tasks: BackgroundTasks,
                      x_slack_signature: str = Header(default=""),
                      x_slack_request_timestamp: str = Header(default="")) -> Response:
    """Slash commands -> ONLY ephemeral responses (visible to the invoker alone)."""
    body = await request.body()
    if not _signing_secret() or not verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad slack signature")
    import urllib.parse
    fields = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
    if not str(fields.get("command", "")).startswith("/"):
        raise HTTPException(400, "not a command")
    resp, deferred = chat.run_command(store, fields)
    user_id = str(fields.get("user_id", ""))
    if deferred:
        background_tasks.add_task(_deferred_safe, deferred)
    elif user_id:
        background_tasks.add_task(chat.maybe_consolidate, store, user_id)
    return JSONResponse(resp)


def _deferred_safe(fn) -> None:
    try:
        fn()
    except Exception as exc:
        print(f"deferred answer error: {type(exc).__name__}")


@app.post("/results")
def report_result(payload: dict, x_veriq_token: str = Header(default="")) -> dict:
    """CI reports finished audits here (X-Veriq-Token gated like /audits) so /status
    and /ask can use them. Shape-restricted: never store anything else."""
    reg_token = os.environ.get("GATEWAY_REGISTRATION_TOKEN", "")
    if reg_token and not hmac.compare_digest(x_veriq_token, reg_token):
        raise HTTPException(401, "bad registration token")
    repo = str(payload.get("repository") or "")
    if "/" not in repo:
        raise HTTPException(400, "missing repository")
    s = payload.get("severity_counts") or {}
    fx = payload.get("fixes") or {}
    ap = payload.get("approval") or {}
    store.add_result({
        "audit_id": str(payload.get("audit_id", ""))[:64],
        "repository": repo[:120],
        "commit": str(payload.get("commit", ""))[:40],
        "pr_number": payload.get("pr_number"),
        "triggered_by": str(payload.get("triggered_by", ""))[:80],
        "slack_user": str(payload.get("slack_user", ""))[:40],
        "score": max(0, min(100, int(payload.get("overall_score", 0) or 0))),
        "cr": int(s.get("CRITICAL", 0) or 0), "hi": int(s.get("HIGH", 0) or 0),
        "me": int(s.get("MEDIUM", 0) or 0), "lo": int(s.get("LOW", 0) or 0),
        "tests": str((payload.get("tests") or {}).get("status", ""))[:16],
        "build": str((payload.get("build") or {}).get("status", ""))[:16],
        "security": str((payload.get("security") or {}).get("status", ""))[:16],
        "decision": str(ap.get("decision", ""))[:40],
        "fixed": int(fx.get("fixed", 0) or 0),
        "run_url": str(payload.get("run_url", ""))[:200],
        "reported_at": time.time(),
    })
    return {"ok": True}


@app.post("/slack/actions")
async def slack_actions(request: Request,
                        x_slack_signature: str = Header(default=""),
                        x_slack_request_timestamp: str = Header(default="")) -> Response:
    body = await request.body()
    # Slack URL-verification challenge must be echoed back verbatim (sent when the
    # Interactivity Request URL is saved; carries no side effects, so answer before
    # signature checks -- it may legitimately arrive unsigned).
    challenge = _challenge_from_body(body)
    if challenge is not None:
        return PlainTextResponse(challenge)
    if not _signing_secret() or not verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad slack signature")
    # Misrouted Events API callbacks (event_callback JSON, signed): ack quietly once
    # instead of 400-looping Slack retries. No side effects here either way.
    try:
        direct = json.loads(body.decode())
    except ValueError:
        direct = None
    if isinstance(direct, dict) and direct.get("type") == "event_callback":
        return JSONResponse({"ok": True})
    form = await request.form()
    try:
        payload = json.loads(str(form.get("payload", "{}")))
    except ValueError:
        # Never log payloads (they embed tokens); shape only, with sensitive keys dropped.
        shape = {k: type(v).__name__ for k, v in form.items() if k != "payload"}
        print(f"interactivity: unparseable payload shape={shape}")
        raise HTTPException(400, "bad payload json")
    actions = payload.get("actions", [])
    if not actions:
        print(f"interactivity: empty actions (type={payload.get('type')}, "
              f"challenge={'challenge' in payload})")
        raise HTTPException(400, "no actions")
    action_id = str(actions[0].get("action_id", ""))  # approve:<audit>|reject:<audit>
    verb, _, audit_id = action_id.partition(":")
    if verb not in ("approve", "reject") or not audit_id:
        raise HTTPException(400, "bad action_id")
    pend = store.get_pending(audit_id)
    if not pend:
        raise HTTPException(404, "unknown audit")
    already = store.get_decision(audit_id)
    clicker = str(payload.get("user", {}).get("name") or payload.get("user", {}).get("id", ""))
    if already:
        done = "approved @{}".format(already.get("approver_slack_id", "?")) \
            if already.get("decision") == "approved" else f"{already.get('decision')}"
        return JSONResponse({"response_type": "ephemeral",
                             "text": f"🔁 Already recorded: `{audit_id}` → {done}. Your second click "
                                     f"changed nothing (anti-duplicate)."})
    if time.time() > pend["expires"]:
        store.set_decision(audit_id, {"decision": "expired", "audit_id": audit_id})
        return JSONResponse({"replace_original": True, "blocks": _state_blocks(
            audit_id, "⏱️", "Approval window closed",
            ":hourglass_flowing_sand:  This audit expired before the decision was recorded — "
            "*no code was modified.*", "re-run the audit to ask again")})
    # value binds repo|commit|pr — reject mismatches (prevents old approval reuse)
    value = str(actions[0].get("value", ""))
    repo, _, rest = value.partition("|")
    if repo != pend.get("repository"):
        raise HTTPException(403, "repository mismatch")
    slack_user = str(payload.get("user", {}).get("id", ""))
    admin = os.environ.get("SLACK_ADMIN_USER_ID", "")
    # Authorize: mapped dev for this audit's github actor, or admin.
    allowed_dev = str(pend.get("slack_user") or "")
    if slack_user != admin and slack_user != allowed_dev:
        # reverse-map check: slack id must belong to the triggering github user
        rev = {v: k for k, v in _user_map().items()}
        if rev.get(slack_user) != pend.get("triggered_by") and slack_user != admin:
            raise HTTPException(403, "unauthorized approver")
    nonce = hashlib.sha256(f"{audit_id}{slack_user}{time.time()}".encode()).hexdigest()[:12]
    store.set_decision(audit_id, {
        "decision": "approved" if verb == "approve" else "rejected",
        "audit_id": audit_id, "repository": pend.get("repository"),
        "commit": pend.get("commit"), "pr_number": pend.get("pr_number"),
        "approver_slack_id": slack_user, "approver_name": clicker,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nonce": nonce,
    })
    repo = str(pend.get("repository") or "")
    if verb == "approve":
        detail = f":white_check_mark:  *@{clicker or slack_user}* approved the AI fixes"
        if repo:
            detail += f"  ·  <https://github.com/{repo}|`{repo}`>"
        blocks = _state_blocks(audit_id, "🟢", "Approved — AI is on it", detail,
                               "verification (tests · build · security · browser) runs now — "
                               "the 👾 agent posts the outcome into this message")
    else:
        blocks = _state_blocks(audit_id, "🔴", "Fix declined",
                               f":no_entry:  *@{clicker or slack_user}* declined automatic fixes. "
                               f"*No code was modified.* The audit report stands on its own.",
                               "human review of the findings is recommended")
    return JSONResponse({"replace_original": True, "blocks": blocks,
                         "text": ("🟢 approved" if verb == "approve" else "🔴 declined")})
