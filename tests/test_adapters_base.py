"""Tests for the adapter shared machinery: config, decide, check_action."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from agent_policy_kit import (
    ApprovalRequiredError,
    ApprovalStore,
    PolicyDeniedError,
    load_policy,
    load_policy_set,
    read_tail,
    resolve_approval,
)
from agent_policy_kit.adapters import (
    AdapterConfig,
    build_action,
    check_action,
    decide,
)

POLICY_YAML = """
name: adapter-test-policy
default: deny
rules:
  - name: allow-reads
    decision: allow
    match:
      tool: "docs.*"
  - name: big-refund-needs-approval
    decision: approve
    match:
      tool: refund.issue
      args.amount: {gt: 100}
  - name: small-refund-ok
    decision: allow
    match:
      tool: refund.issue
      args.amount: {lte: 100}
"""


@pytest.fixture()
def policy(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return load_policy(path)


@pytest.fixture()
def config(policy, tmp_path):
    return AdapterConfig(
        policy=policy,
        approval_store=ApprovalStore(tmp_path / "approvals.json"),
        audit_path=tmp_path / "audit.log",
        agent_name="support-bot",
    )


def test_build_action_shapes_the_engine_dict():
    action = build_action("bot", "tickets.read", {"id": "T-1"}, resource="tickets/prod")
    assert action == {
        "agent": "bot",
        "tool": "tickets.read",
        "args": {"id": "T-1"},
        "resource": "tickets/prod",
    }


def test_build_action_coerces_non_dict_args():
    assert build_action("bot", "t", "oops")["args"] == {"args": "oops"}
    action = build_action("bot", "t", {"a": 1})
    assert "resource" not in action


def test_config_needs_exactly_one_policy_source(policy, tmp_path):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("name: b\ndefault: deny\nrules: []\n", encoding="utf-8")
    pset = load_policy_set([baseline])
    with pytest.raises(ValueError):
        AdapterConfig()
    with pytest.raises(ValueError):
        AdapterConfig(policy=policy, policy_set=pset)


def test_decide_returns_without_raising(config):
    decision = decide(config, build_action("support-bot", "docs.search", {"q": "x"}))
    assert decision.decision == "allow"
    assert decision.rule == "allow-reads"


def test_decide_audits_every_evaluation(config, tmp_path):
    decide(config, build_action("support-bot", "docs.search", {"q": "x"}))
    records = read_tail(tmp_path / "audit.log")
    assert len(records) == 1
    assert records[0]["decision"] == "allow"
    assert records[0]["kind"] == "decision"


def test_decide_fires_the_hook(policy):
    seen = []
    config = AdapterConfig(policy=policy, on_decision=seen.append)
    decide(config, build_action("b", "docs.search", {}))
    assert len(seen) == 1 and seen[0].decision == "allow"


def test_check_action_allows(config):
    decision = check_action(config, build_action("support-bot", "refund.issue", {"amount": 50}))
    assert decision.decision == "allow"
    assert decision.rule == "small-refund-ok"


def test_check_action_denies_and_carries_the_decision(config):
    action = build_action("support-bot", "users.delete", {"id": "u-9"})
    with pytest.raises(PolicyDeniedError) as exc_info:
        check_action(config, action)
    assert exc_info.value.decision.decision == "deny"
    assert "denied" in str(exc_info.value)


def test_check_action_approve_queues_a_ticket_and_raises(config, tmp_path):
    action = build_action("support-bot", "refund.issue", {"amount": 500})
    with pytest.raises(ApprovalRequiredError) as exc_info:
        check_action(config, action)
    ticket = exc_info.value.approval_request
    assert ticket is not None
    assert ticket["status"] == "pending"
    assert ticket["rule"] == "big-refund-needs-approval"
    assert ticket["id"] in str(exc_info.value)
    # the ticket is really in the store
    store = ApprovalStore(tmp_path / "approvals.json")
    assert store.get(ticket["id"])["status"] == "pending"
    # and the audit line carries the ticket id
    records = read_tail(tmp_path / "audit.log")
    assert records[-1]["approval_request_id"] == ticket["id"]


def test_check_action_approve_then_retry_after_human_approval(config):
    action = build_action("support-bot", "refund.issue", {"amount": 500})
    with pytest.raises(ApprovalRequiredError) as exc_info:
        check_action(config, action)
    ticket_id = exc_info.value.approval_request["id"]
    resolve_approval(config.approval_store, ticket_id, True, by="anusha")
    # the same action now goes through as allow
    decision = check_action(config, action)
    assert decision.decision == "allow"
    assert ticket_id in decision.reason


def test_check_action_expired_approval_does_not_grant(config):
    action = build_action("support-bot", "refund.issue", {"amount": 500})
    with pytest.raises(ApprovalRequiredError) as exc_info:
        check_action(config, action)
    ticket_id = exc_info.value.approval_request["id"]
    # approve it, then pretend the ticket expired long ago
    resolve_approval(config.approval_store, ticket_id, True, by="anusha")
    data = config.approval_store._read()
    data["requests"][ticket_id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    config.approval_store._write(data)
    with pytest.raises(ApprovalRequiredError):
        check_action(config, action)


def test_check_action_approve_without_store_still_raises(policy):
    config = AdapterConfig(policy=policy)
    with pytest.raises(ApprovalRequiredError) as exc_info:
        check_action(config, build_action("b", "refund.issue", {"amount": 500}))
    assert exc_info.value.approval_request is None


def test_check_action_works_without_audit_or_store(policy):
    config = AdapterConfig(policy=policy)
    decision = check_action(config, build_action("b", "docs.search", {}))
    assert decision.decision == "allow"


def test_check_action_accepts_a_policy_set(tmp_path):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(
        yaml.safe_dump(
            {
                "name": "baseline",
                "default": "deny",
                "rules": [
                    {
                        "name": "never-delete-users",
                        "decision": "deny",
                        "match": {"tool": "users.delete"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    team = tmp_path / "team.yaml"
    team.write_text(
        yaml.safe_dump(
            {
                "name": "team",
                "default": "deny",
                "rules": [
                    {
                        "name": "team-can-delete",
                        "decision": "allow",
                        "match": {"tool": "users.delete"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pset = load_policy_set([baseline, team])  # deny_overrides: baseline wins
    config = AdapterConfig(policy_set=pset)
    with pytest.raises(PolicyDeniedError) as exc_info:
        check_action(config, build_action("b", "users.delete", {}))
    assert exc_info.value.decision.policy == "baseline"


def test_audit_log_survives_unserializable_args(policy, tmp_path):
    config = AdapterConfig(policy=policy, audit_path=tmp_path / "audit.log")
    decide(config, build_action("b", "docs.search", {"blob": object()}))
    records = read_tail(tmp_path / "audit.log")
    assert len(records) == 1
    # the file on disk is valid JSONL
    for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_agent_name_defaults_to_agent(policy):
    config = AdapterConfig(policy=policy)
    assert config.agent_name == "agent"
    action = build_action(config.agent_name, "docs.search", {})
    assert decide(config, action).decision == "allow"
