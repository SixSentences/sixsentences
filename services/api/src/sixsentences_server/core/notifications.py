"""Outbound webhooks: notify a tenant's endpoint when a run changes state.

Best-effort and non-blocking to the pipeline — a delivery failure is swallowed,
never raised into a run. Each payload is signed with the webhook's secret
(HMAC-SHA256, ``X-SixSentences-Signature``) so the receiver can verify it came
from us. A webhook with an empty ``events`` list receives every event.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import Webhook
from sixsentences_server.core.net import is_public_http_url


def is_safe_url(url: str) -> bool:
    """Guard against SSRF on webhook targets: http(s) to a public host only.

    Structural (no DNS) at this layer so a valid webhook is not rejected on a
    transient resolution failure; delivery is blind (no read-back, no redirect
    following), so a name that resolves inward is a lower risk than the acquisition
    fetcher, which resolves every hop. See ``core.net.is_public_http_url``."""
    return is_public_http_url(url, resolve=False)


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def fire_run_event(
    session: Session,
    org_id: int,
    event: str,
    payload: dict[str, Any],
    *,
    http: httpx.Client | None = None,
    guard: Callable[[str], bool] | None = None,
) -> int:
    """POST the event to every matching active webhook; return the count
    delivered. `guard` gates each target URL (default: resolve=True SSRF check);
    injectable so a test can allow example hostnames that do not resolve."""
    check = guard or is_public_http_url
    hooks = session.scalars(
        select(Webhook).where(Webhook.org_id == org_id, Webhook.active.is_(True))
    ).all()
    # resolve=True at delivery: a hostname that resolves to a private IP is
    # refused now, not just literal-IP SSRF probes at registration time
    targets = [h for h in hooks if (not h.events or event in h.events) and check(h.url)]
    if not targets:
        return 0
    body = json.dumps({"event": event, "data": payload}).encode()
    client = http or httpx.Client(timeout=10)
    delivered = 0
    for hook in targets:
        try:
            response = client.post(
                hook.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SixSentences-Event": event,
                    "X-SixSentences-Signature": sign(hook.secret, body),
                },
            )
        except httpx.HTTPError:
            continue  # best-effort: a dead endpoint never breaks a run
        if response.status_code < 400:
            delivered += 1
    return delivered
