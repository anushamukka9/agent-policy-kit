import json
from datetime import datetime, timezone
from pathlib import Path

from agent_policy_kit import (
    ApprovalStore,
    evaluate,
    load_policy,
    log_decision,
    log_resolution,
    read_tail,
    request_approval,
    resolve_approval,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "policies"


def test_log_decision_appends_jsonl(tmp_path):
    audit_path = tmp_path / "audit.log"
    policy = load_policy(EXAMPLES / "support-bot.yaml")
    action = {"agent": "support-bot", "tool": "kb.search", "args": {}}
    decision = evaluate(policy, action)
    log_decision(audit_path, decision, action)

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "decision"
    assert rec["decision"] == "allow"
    assert rec["policy"] == "support-bot-policy"
    assert rec["rule"] == "kb-search"
    assert rec["action"] == action
    assert rec["approval_request_id"] is None
    assert rec["ts"]  # timestamp present


def test_log_decision_with_approval_id(tmp_path):
    audit_path = tmp_path / "audit.log"
    policy = load_policy(EXAMPLES / "deploy-bot.yaml")
    action = {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "staging"}}
    decision = evaluate(policy, action)
    log_decision(audit_path, decision, action, approval_request_id="apr-abc123")
    rec = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["approval_request_id"] == "apr-abc123"


def test_log_resolution(tmp_path):
    audit_path = tmp_path / "audit.log"
    store = ApprovalStore(tmp_path / "approvals.json")
    now = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    action = {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "staging"}}
    req = request_approval(store, action, "deploy-bot-policy", "r", now=now)
    resolved = resolve_approval(store, req["id"], True, by="anusha", now=now)
    log_resolution(audit_path, resolved)

    rec = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "resolution"
    assert rec["decision"] == "approved"
    assert "anusha" in rec["reason"]
    assert rec["approval_request_id"] == req["id"]


def test_read_tail_returns_last_n_oldest_first(tmp_path):
    audit_path = tmp_path / "audit.log"
    policy = load_policy(EXAMPLES / "support-bot.yaml")
    action = {"agent": "support-bot", "tool": "kb.search", "args": {}}
    for _ in range(5):
        log_decision(audit_path, evaluate(policy, action), action)
    tail = read_tail(audit_path, 3)
    assert len(tail) == 3
    assert [r["kind"] for r in tail] == ["decision"] * 3


def test_read_tail_missing_file(tmp_path):
    assert read_tail(tmp_path / "nope.log", 10) == []
