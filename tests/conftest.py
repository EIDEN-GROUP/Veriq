"""Shared gateway test helpers: fresh TestClient (memory backend) + signed posts."""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import time
import urllib.parse

import pytest


def gw_client():
    from fastapi.testclient import TestClient
    os.environ.pop("UPSTASH_REDIS_REST_URL", None)
    os.environ.pop("UPSTASH_REDIS_REST_TOKEN", None)
    import gateway.app as g
    importlib.reload(g)  # rebind store on the reloaded module + fresh memory store
    g.reset_state()
    return TestClient(g.app), g


@pytest.fixture()
def gateway():
    return gw_client()


def _signed(client, path: str, raw_body: str, secret: str = "s3cr3t"):
    os.environ["SLACK_SIGNING_SECRET"] = secret
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"v0:{ts}:{raw_body}".encode(), hashlib.sha256).hexdigest()
    return client.post(path, content=raw_body.encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "X-Slack-Signature": f"v0={mac}",
                                "X-Slack-Request-Timestamp": ts})


def sign_form(client, path: str, fields: dict, secret: str = "s3cr3t"):
    """Signed plain form-body POST (slash commands shape)."""
    return _signed(client, path, urllib.parse.urlencode(fields), secret)


def sign_payload(client, path: str, fields: dict, payload: dict, secret: str = "s3cr3t"):
    """Signed form POST carrying an interactive JSON payload (block_actions shape)."""
    merged = dict(fields)
    merged["payload"] = json.dumps(payload)
    return _signed(client, path, urllib.parse.urlencode(merged), secret)
