from datetime import datetime, timedelta, timezone

import pytest

from agent_policy_kit import (
    ApprovalStore,
    list_pending,
    request_approval,
    resolve_approval,
    sweep_expired,
)

ACTION = {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "staging"}}
NOW = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)


def store(tmp_path):
    return ApprovalStore(tmp_path / "approvals.json")


def test_request_creates_pending(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "deploy-bot-policy", "some-rule", ttl_minutes=30, now=NOW)
    assert req["id"].startswith("apr-")
    assert req["status"] == "pending"
    assert req["resolved_by"] is None
    assert req["expires_at"] == (NOW + timedelta(minutes=30)).isoformat()


def test_request_ids_unique(tmp_path):
    s = store(tmp_path)
    ids = {request_approval(s, ACTION, "p", "r", now=NOW)["id"] for _ in range(20)}
    assert len(ids) == 20


def test_ttl_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        request_approval(store(tmp_path), ACTION, "p", "r", ttl_minutes=0, now=NOW)


def test_approve_flow(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "p", "r", now=NOW)
    resolved = resolve_approval(s, req["id"], True, by="anusha", note="looks fine", now=NOW)
    assert resolved["status"] == "approved"
    assert resolved["resolved_by"] == "anusha"
    assert resolved["note"] == "looks fine"


def test_deny_flow(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "p", "r", now=NOW)
    resolved = resolve_approval(s, req["id"], False, by="on-call", now=NOW)
    assert resolved["status"] == "denied"


def test_double_resolve_rejected(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "p", "r", now=NOW)
    resolve_approval(s, req["id"], True, by="a", now=NOW)
    with pytest.raises(ValueError, match="already approved"):
        resolve_approval(s, req["id"], False, by="b", now=NOW)


def test_unknown_id_rejected(tmp_path):
    with pytest.raises(KeyError):
        resolve_approval(store(tmp_path), "apr-deadbeef", True, by="a", now=NOW)


def test_expiry(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "p", "r", ttl_minutes=30, now=NOW)
    later = NOW + timedelta(minutes=31)
    swept = sweep_expired(s, now=later)
    assert len(swept) == 1
    assert s.get(req["id"])["status"] == "expired"


def test_resolve_after_expiry_rejected(tmp_path):
    s = store(tmp_path)
    req = request_approval(s, ACTION, "p", "r", ttl_minutes=30, now=NOW)
    later = NOW + timedelta(minutes=60)
    with pytest.raises(ValueError, match="already expired"):
        resolve_approval(s, req["id"], True, by="a", now=later)


def test_list_pending_sweeps_expired(tmp_path):
    s = store(tmp_path)
    fresh = request_approval(s, ACTION, "p", "r", ttl_minutes=30, now=NOW)
    old = request_approval(s, ACTION, "p", "r", ttl_minutes=30, now=NOW - timedelta(hours=2))
    pending = list_pending(s, now=NOW)
    assert [r["id"] for r in pending] == [fresh["id"]]
    assert s.get(old["id"])["status"] == "expired"


def test_store_survives_reload(tmp_path):
    path = tmp_path / "approvals.json"
    request_approval(ApprovalStore(path), ACTION, "p", "r", now=NOW)
    reloaded = ApprovalStore(path)
    assert len(reloaded.all()) == 1


def test_empty_store(tmp_path):
    s = store(tmp_path)
    assert s.all() == []
    assert list_pending(s, now=NOW) == []
