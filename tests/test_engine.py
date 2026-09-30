from datetime import datetime, timezone

from agent_policy_kit import evaluate, load_policy
from agent_policy_kit.policy import Policy, Rule

SUPPORT = load_policy(
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "examples"
    / "policies"
    / "support-bot.yaml"
)
DEPLOY = load_policy(
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "examples"
    / "policies"
    / "deploy-bot.yaml"
)


def policy_with(rules, default="deny"):
    return Policy(
        name="t",
        version=1,
        default=default,
        rules=[Rule(name=n, decision=d, match=m) for n, d, m in rules],
    )


def test_first_match_wins():
    p = policy_with(
        [
            ("first", "deny", {"tool": "deploy"}),
            ("second", "allow", {"tool": "deploy"}),
        ]
    )
    d = evaluate(p, {"tool": "deploy"})
    assert d.decision == "deny"
    assert d.rule == "first"


def test_default_applies_when_nothing_matches():
    p = policy_with([], default="allow")
    d = evaluate(p, {"tool": "anything"})
    assert d.decision == "allow"
    assert d.rule is None
    assert "policy default" in d.reason


def test_glob_tool_match():
    p = policy_with([("r", "deny", {"tool": "users.*"})])
    assert evaluate(p, {"tool": "users.delete"}).decision == "deny"
    assert (
        evaluate(
            p,
            {
                "tool": "users.delete",
            },
        ).decision
        == "deny"
    )
    assert evaluate(p, {"tool": "tickets.read"}).decision == "deny"  # default


def test_glob_does_not_match_partial_without_wildcard():
    p = policy_with([("r", "allow", {"tool": "tickets.read"})])
    assert evaluate(p, {"tool": "tickets.read.extra"}).decision == "deny"


def test_regex_tool_match():
    p = policy_with([("r", "deny", {"tool": "regex:^db\\..*"})])
    assert evaluate(p, {"tool": "db.migrate"}).decision == "deny"
    assert evaluate(p, {"tool": "adb.migrate"}).decision == "deny"  # default


def test_agent_condition():
    p = policy_with([("r", "allow", {"agent": "support-*", "tool": "kb.search"})])
    assert evaluate(p, {"agent": "support-bot", "tool": "kb.search"}).decision == "allow"
    assert evaluate(p, {"agent": "admin", "tool": "kb.search"}).decision == "deny"


def test_placeholder_substitution():
    p = policy_with([("r", "allow", {"tool": "tickets.read", "args.assignee": "{agent}"})])
    ok = {"agent": "bot-a", "tool": "tickets.read", "args": {"assignee": "bot-a"}}
    bad = {"agent": "bot-a", "tool": "tickets.read", "args": {"assignee": "bot-b"}}
    assert evaluate(p, ok).decision == "allow"
    assert evaluate(p, bad).decision == "deny"


def test_numeric_operators():
    p = policy_with(
        [
            ("small", "allow", {"args.amount": {"lte": 200}}),
            ("big", "deny", {"args.amount": {"gt": 200}}),
        ]
    )
    assert evaluate(p, {"args": {"amount": 200}}).decision == "allow"
    assert evaluate(p, {"args": {"amount": 201}}).decision == "deny"
    assert evaluate(p, {"args": {}}).decision == "deny"  # missing amount


def test_numeric_operators_reject_bool_and_str():
    p = policy_with([("r", "allow", {"args.amount": {"gt": 5}})])
    assert evaluate(p, {"args": {"amount": True}}).decision == "deny"
    assert evaluate(p, {"args": {"amount": "10"}}).decision == "deny"
    assert evaluate(p, {"args": {"amount": 10}}).decision == "allow"


def test_in_and_not_in():
    p = policy_with(
        [
            ("r1", "allow", {"args.env": {"in": ["staging", "dev"]}}),
            ("r2", "deny", {"args.env": {"not_in": ["staging", "dev"]}}),
        ]
    )
    assert evaluate(p, {"args": {"env": "staging"}}).decision == "allow"
    assert evaluate(p, {"args": {"env": "prod"}}).decision == "deny"


def test_exists_operator():
    p = policy_with(
        [
            ("r1", "allow", {"args.token": {"exists": True}}),
            ("r2", "deny", {"args.token": {"exists": False}}),
        ]
    )
    assert evaluate(p, {"args": {"token": "x"}}).decision == "allow"
    assert evaluate(p, {"args": {}}).decision == "deny"


def test_contains_operator():
    p = policy_with([("r", "deny", {"args.cmd": {"contains": "rm -rf"}})])
    assert evaluate(p, {"args": {"cmd": "please rm -rf /"}}).decision == "deny"
    assert evaluate(p, {"args": {"cmd": "ls"}}).decision == "deny"


def test_ne_operator():
    p = policy_with([("r", "allow", {"args.env": {"ne": "prod"}})])
    assert evaluate(p, {"args": {"env": "staging"}}).decision == "allow"
    assert evaluate(p, {"args": {"env": "prod"}}).decision == "deny"


def test_any_of():
    p = policy_with(
        [
            (
                "r",
                "allow",
                {
                    "any_of": [
                        {"tool": "logs.tail"},
                        {"tool": "ci.status"},
                    ]
                },
            )
        ]
    )
    assert evaluate(p, {"tool": "logs.tail"}).decision == "allow"
    assert evaluate(p, {"tool": "ci.status"}).decision == "allow"
    assert evaluate(p, {"tool": "deploy"}).decision == "deny"


def test_time_window():
    p = policy_with([("r", "allow", {"tool": "deploy", "time": {"window": "09:00-17:00"}})])
    inside = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    edge = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
    assert evaluate(p, {"tool": "deploy"}, now=inside).decision == "allow"
    assert evaluate(p, {"tool": "deploy"}, now=edge).decision == "deny"


def test_overnight_window():
    p = policy_with([("r", "allow", {"tool": "deploy", "time": {"window": "22:00-06:00"}})])
    night = datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc)
    early = datetime(2026, 9, 27, 5, 59, tzinfo=timezone.utc)
    day = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    assert evaluate(p, {"tool": "deploy"}, now=night).decision == "allow"
    assert evaluate(p, {"tool": "deploy"}, now=early).decision == "allow"
    assert evaluate(p, {"tool": "deploy"}, now=day).decision == "deny"


def test_resource_condition():
    p = policy_with([("r", "allow", {"tool": "logs.tail", "resource": "logs/staging/*"})])
    assert evaluate(p, {"tool": "logs.tail", "resource": "logs/staging/api"}).decision == "allow"
    assert evaluate(p, {"tool": "logs.tail", "resource": "logs/prod/api"}).decision == "deny"
    assert evaluate(p, {"tool": "logs.tail"}).decision == "deny"


def test_reason_names_rule():
    p = policy_with([("my-rule", "deny", {"tool": "x"})])
    d = evaluate(p, {"tool": "x"})
    assert "my-rule" in d.reason
    assert "deny" in d.reason


def test_example_policies_load():
    assert SUPPORT.name == "support-bot-policy"
    assert DEPLOY.name == "deploy-bot-policy"
    assert len(SUPPORT.rules) == 6
    assert len(DEPLOY.rules) == 6


def test_support_bot_spot_checks():
    allow = evaluate(SUPPORT, {"agent": "support-bot", "tool": "kb.search", "args": {}})
    assert allow.decision == "allow"
    deny = evaluate(SUPPORT, {"agent": "support-bot", "tool": "users.delete", "args": {}})
    assert deny.decision == "deny"
    approve = evaluate(
        SUPPORT, {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 50}}
    )
    assert approve.decision == "approve"
    assert approve.rule == "small-refund-needs-approval"


def test_not_block_carves_out():
    p = policy_with([("r", "allow", {"tool": "api.*", "not": {"tool": "api.admin.*"}})])
    assert evaluate(p, {"tool": "api.users.list"}).decision == "allow"
    assert evaluate(p, {"tool": "api.admin.reset"}).decision == "deny"


def test_not_block_with_args():
    p = policy_with([("r", "allow", {"tool": "deploy", "not": {"args.env": "prod"}})])
    assert evaluate(p, {"tool": "deploy", "args": {"env": "staging"}}).decision == "allow"
    assert evaluate(p, {"tool": "deploy", "args": {"env": "prod"}}).decision == "deny"


def test_time_days_filter():
    p = policy_with(
        [
            (
                "r",
                "allow",
                {"tool": "x", "time": {"window": "09:00-17:00", "days": ["mon", "tue"]}},
            )
        ]
    )
    monday = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
    wednesday = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
    assert evaluate(p, {"tool": "x"}, now=monday).decision == "allow"
    assert evaluate(p, {"tool": "x"}, now=wednesday).decision == "deny"


def test_startswith_endswith_operators():
    p = policy_with(
        [
            ("r1", "allow", {"args.sql": {"startswith": "SELECT"}}),
            ("r2", "deny", {"args.name": {"endswith": ".tmp"}}),
        ]
    )
    assert evaluate(p, {"args": {"sql": "SELECT 1"}}).decision == "allow"
    assert evaluate(p, {"args": {"sql": "DELETE FROM t"}}).decision == "deny"
    assert evaluate(p, {"args": {"name": "scratch.tmp"}}).decision == "deny"
    assert evaluate(p, {"args": {"name": "report.csv"}}).decision == "deny"  # default
    assert evaluate(p, {"args": {"sql": 42}}).decision == "deny"  # not a string


def test_length_operator():
    p = policy_with(
        [
            ("bounded", "allow", {"args.filters": {"length": {"max": 3}}}),
            ("exact", "allow", {"args.code": {"length": 4}}),
        ]
    )
    assert evaluate(p, {"args": {"filters": ["a", "b"]}}).decision == "allow"
    assert evaluate(p, {"args": {"filters": ["a", "b", "c", "d"]}}).decision == "deny"
    assert evaluate(p, {"args": {"code": "abcd"}}).decision == "allow"
    assert evaluate(p, {"args": {"code": "abcde"}}).decision == "deny"
    assert evaluate(p, {"args": {"code": 1234}}).decision == "deny"  # numbers have no length


def test_length_min_bound():
    p = policy_with([("r", "allow", {"args.tags": {"length": {"min": 1, "max": 2}}})])
    assert evaluate(p, {"args": {"tags": ["a"]}}).decision == "allow"
    assert evaluate(p, {"args": {"tags": []}}).decision == "deny"


def test_explain_returns_decision_and_traces():
    from agent_policy_kit import explain

    p = policy_with(
        [
            ("first", "allow", {"tool": "a"}),
            ("second", "deny", {"tool": "b"}),
        ]
    )
    explanation = explain(p, {"tool": "b"})
    assert explanation.decision.decision == "deny"
    assert explanation.decision.rule == "second"
    assert len(explanation.traces) == 2
    assert explanation.traces[0].matched is False
    assert explanation.traces[0].misses, "a failed rule should say why"
    assert explanation.traces[1].matched is True
    assert explanation.traces[1].hits == ["tool"]


def test_explain_miss_messages_name_the_condition():
    from agent_policy_kit import explain

    p = policy_with([("r", "allow", {"tool": "tickets.read", "args.assignee": "{agent}"})])
    explanation = explain(p, {"agent": "bot-a", "tool": "tickets.read", "args": {}})
    assert explanation.decision.decision == "deny"
    trace = explanation.traces[0]
    assert any("args.assignee" in miss and "missing" in miss for miss in trace.misses)


def test_explain_default_decision_has_empty_trace_hits():
    from agent_policy_kit import explain

    p = policy_with([], default="deny")
    explanation = explain(p, {"tool": "x"})
    assert explanation.decision.decision == "deny"
    assert explanation.decision.rule is None
    assert explanation.traces == []


def test_org_baseline_spot_checks():
    from pathlib import Path as P

    baseline = load_policy(
        P(__file__).resolve().parent.parent / "examples" / "policies" / "org-baseline.yaml"
    )
    wednesday = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    assert evaluate(baseline, {"tool": "prod.deploy"}, now=wednesday).decision == "allow"
    assert evaluate(baseline, {"tool": "prod.deploy"}, now=saturday).decision == "deny"
    assert evaluate(baseline, {"tool": "api.admin.reset"}).decision == "deny"
    assert evaluate(baseline, {"tool": "db.destroy"}).decision == "deny"
