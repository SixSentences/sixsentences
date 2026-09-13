"""Outbound webhooks: signed, filtered, best-effort delivery."""

import httpx

from sixsentences_server.config import Settings
from sixsentences_server.core.db import Webhook, db_session, get_default_org, init_db
from sixsentences_server.core.notifications import fire_run_event, is_safe_url, sign


def _client(sent: list[httpx.Request], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fire_signs_and_delivers(settings: Settings) -> None:
    init_db()
    sent: list[httpx.Request] = []
    with db_session() as session:
        org = get_default_org(session)
        session.add(
            Webhook(org_id=org.id, url="https://hook.example/x", secret="s3cr3t", events=[])
        )
        session.flush()
        delivered = fire_run_event(
            session,
            org.id,
            "run.completed",
            {"run_id": 1},
            http=_client(sent),
            guard=lambda url: True,  # example host does not resolve; test delivery
        )
        assert delivered == 1
        request = sent[0]
        assert request.headers["X-SixSentences-Event"] == "run.completed"
        # the receiver can verify the payload with the shared secret
        assert sign("s3cr3t", request.content) == request.headers["X-SixSentences-Signature"]


def test_event_filter_is_respected(settings: Settings) -> None:
    init_db()
    sent: list[httpx.Request] = []
    with db_session() as session:
        org = get_default_org(session)
        session.add(Webhook(org_id=org.id, url="https://x", secret="s", events=["run.failed"]))
        session.flush()
        # a completed event does not match a failed-only hook
        assert fire_run_event(session, org.id, "run.completed", {}, http=_client(sent)) == 0
        assert sent == []


def test_no_hooks_is_a_noop(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        # no webhook configured -> returns 0 without making any request
        assert fire_run_event(session, org.id, "run.completed", {}) == 0


def test_is_safe_url_blocks_ssrf_targets() -> None:
    assert is_safe_url("https://hooks.example.com/x")  # public host: allowed
    assert not is_safe_url("http://localhost/x")
    assert not is_safe_url("http://127.0.0.1/x")
    assert not is_safe_url("http://169.254.169.254/latest/meta-data")  # cloud metadata
    assert not is_safe_url("http://10.0.0.5/internal")  # private range
    assert not is_safe_url("http://[::1]/x")  # ipv6 loopback
    assert not is_safe_url("file:///etc/passwd")  # non-http scheme
    assert not is_safe_url("https://metadata.google.internal/x")


def test_unsafe_target_is_skipped_on_fire(settings: Settings) -> None:
    init_db()
    sent: list[httpx.Request] = []
    with db_session() as session:
        org = get_default_org(session)
        # a hook that somehow points inward is not delivered to (defense in depth)
        session.add(Webhook(org_id=org.id, url="http://127.0.0.1/x", secret="s", events=[]))
        session.flush()
        assert fire_run_event(session, org.id, "run.completed", {}, http=_client(sent)) == 0
        assert sent == []


def test_dead_endpoint_never_raises(settings: Settings) -> None:
    init_db()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with db_session() as session:
        org = get_default_org(session)
        session.add(Webhook(org_id=org.id, url="https://dead", secret="s", events=[]))
        session.flush()
        # best-effort: a failing delivery is swallowed, delivered count is 0
        assert (
            fire_run_event(session, org.id, "run.completed", {}, http=http, guard=lambda url: True)
            == 0
        )
