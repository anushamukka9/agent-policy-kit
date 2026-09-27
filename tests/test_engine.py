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
