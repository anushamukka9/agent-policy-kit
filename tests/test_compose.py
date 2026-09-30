"""Tests for policy composition: PolicySet, strategies, and load_policy_set."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_policy_kit import evaluate_set, load_policy_set
from agent_policy_kit.compose import STRATEGIES, PolicySet
from agent_policy_kit.policy import Policy, Rule

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "policies"
WEDNESDAY = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def policy_with(name, rules, default="deny"):
    return Policy(
        name=name,
        version=1,
        default=default,
        rules=[Rule(name=n, decision=d, match=m) for n, d, m in rules],
    )


def test_deny_overrides_picks_most_restrictive():
    pset = PolicySet(
        [
            policy_with("team", [("t1", "allow", {"tool": "x"})]),
            policy_with("org", [("o1", "deny", {"tool": "x"})]),
        ],
        strategy="deny_overrides",
    )
    decision = evaluate_set(pset, {"tool": "x"})
    assert decision.decision == "deny"
    assert decision.policy == "org"
    assert decision.rule == "o1"
    assert "deny_overrides" in decision.reason


def test_deny_overrides_approve_beats_allow():
    pset = PolicySet(
        [
            policy_with("a", [("r", "allow", {"tool": "x"})]),
            policy_with("b", [("r", "approve", {"tool": "x"})]),
        ],
        strategy="deny_overrides",
    )
    assert evaluate_set(pset, {"tool": "x"}).decision == "approve"


def test_allow_overrides_picks_most_permissive():
    pset = PolicySet(
        [
            policy_with("org", [("o1", "deny", {"tool": "x"})]),
            policy_with("team", [("t1", "allow", {"tool": "x"})]),
        ],
        strategy="allow_overrides",
    )
    decision = evaluate_set(pset, {"tool": "x"})
    assert decision.decision == "allow"
    assert decision.policy == "team"


def test_first_wins_uses_first_matching_policy():
    pset = PolicySet(
        [
            policy_with("specific", [("s1", "approve", {"tool": "deploy"})]),
            policy_with("general", [("g1", "allow", {"tool": "deploy"})]),
        ],
        strategy="first_wins",
    )
    decision = evaluate_set(pset, {"tool": "deploy"})
    assert decision.decision == "approve"
    assert decision.policy == "specific"


def test_first_wins_falls_back_to_first_default():
    pset = PolicySet(
        [
            policy_with("first", [], default="allow"),
            policy_with("second", [], default="deny"),
        ],
        strategy="first_wins",
    )
    decision = evaluate_set(pset, {"tool": "anything"})
    assert decision.decision == "allow"
    assert decision.policy == "first"
    assert decision.rule is None


def test_ties_go_to_earlier_policy():
    pset = PolicySet(
        [
            policy_with("first", [("r", "deny", {"tool": "x"})]),
            policy_with("second", [("r", "deny", {"tool": "x"})]),
        ],
        strategy="deny_overrides",
    )
    assert evaluate_set(pset, {"tool": "x"}).policy == "first"


def test_empty_policy_set_rejected():
    with pytest.raises(ValueError, match="at least one policy"):
        PolicySet([], strategy="deny_overrides")


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError, match="unknown strategy"):
        PolicySet([policy_with("a", [])], strategy="sometimes")


def test_duplicate_policy_names_rejected():
    with pytest.raises(ValueError, match="duplicate policy names"):
        PolicySet([policy_with("a", []), policy_with("a", [])])


def test_load_policy_set_from_files():
    pset = load_policy_set(
        [EXAMPLES / "org-baseline.yaml", EXAMPLES / "deploy-bot.yaml"],
        strategy="deny_overrides",
    )
    assert pset.names == ["org-baseline-policy", "deploy-bot-policy"]
    assert pset.strategy == "deny_overrides"


def test_composed_example_policies():
    pset = load_policy_set(
        [EXAMPLES / "org-baseline.yaml", EXAMPLES / "deploy-bot.yaml"],
        strategy="deny_overrides",
    )
    # deploy-bot would approve a business-hours prod deploy, but the
    # org baseline default-denies it, and deny_overrides floors it.
    decision = evaluate_set(
        pset,
        {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "prod"}},
        now=WEDNESDAY,
    )
    assert decision.decision == "deny"
    assert decision.policy == "org-baseline-policy"


def test_composed_allow_overrides_changes_outcome():
    pset = load_policy_set(
        [EXAMPLES / "org-baseline.yaml", EXAMPLES / "deploy-bot.yaml"],
        strategy="allow_overrides",
    )
    decision = evaluate_set(
        pset,
        {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "prod"}},
        now=WEDNESDAY,
    )
    assert decision.decision == "approve"
    assert decision.policy == "deploy-bot-policy"


def test_strategies_listed():
    assert set(STRATEGIES) == {"deny_overrides", "allow_overrides", "first_wins"}
