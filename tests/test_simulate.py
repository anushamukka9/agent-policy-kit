import json
from pathlib import Path

import pytest

from agent_policy_kit import (
    Scenario,
    load_policy,
    load_policy_set,
    load_scenarios,
    simulate,
    simulate_many,
    simulate_set,
)
from agent_policy_kit.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
POLICY = EXAMPLES / "policies" / "support-bot.yaml"


def support_policy():
    return load_policy(POLICY)


def test_simulate_allow_names_the_rule():
    decision = simulate(support_policy(), {"agent": "support-bot", "tool": "kb.search", "args": {}})
    assert decision.decision == "allow"
    assert decision.rule == "kb-search"


def test_simulate_deny_names_the_rule():
    decision = simulate(
        support_policy(),
        {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 250}},
    )
    assert decision.decision == "deny"
    assert decision.rule == "large-refund-denied"


def test_simulate_default_when_no_rule_matches():
    decision = simulate(
        support_policy(), {"agent": "support-bot", "tool": "email.send", "args": {}}
    )
    assert decision.decision == "deny"
    assert decision.rule is None
    assert "policy default" in decision.reason


def test_simulate_many_counts_and_expectations():
    scenarios = [
        Scenario("search", {"agent": "support-bot", "tool": "kb.search"}, expect="allow"),
        Scenario(
            "small refund",
            {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 50}},
            expect="approve",
        ),
        Scenario(
            "big refund",
            {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 250}},
            expect="allow",
        ),
    ]
    report = simulate_many(support_policy(), scenarios)
    assert report.counts == {"allow": 1, "deny": 1, "approve": 1}
    assert report.ok is False
    assert [r.name for r in report.failures] == ["big refund"]
    assert report.results[0].passed is True


def test_simulate_many_no_expectations_passed_is_none():
    report = simulate_many(support_policy(), [Scenario("s", {"agent": "a", "tool": "kb.search"})])
    assert report.results[0].passed is None
    assert report.ok is True


def test_scenario_rejects_bad_expectation():
    with pytest.raises(ValueError, match="expect must be one of"):
        Scenario("x", {"tool": "kb.search"}, expect="maybe")


def test_scenario_requires_tool():
    with pytest.raises(ValueError, match="needs at least a 'tool'"):
        Scenario("x", {"agent": "a"})


def test_load_scenarios_list_wrapped_and_flat(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "wrapped",
                    "action": {"agent": "support-bot", "tool": "kb.search"},
                    "expect": "allow",
                },
                {"name": "flat", "agent": "support-bot", "tool": "users.delete", "expect": "deny"},
                {"agent": "support-bot", "tool": "kb.search"},
            ]
        ),
        encoding="utf-8",
    )
    scenarios = load_scenarios(path)
    assert [s.name for s in scenarios] == ["wrapped", "flat", "scenario-3"]
    assert scenarios[1].action["tool"] == "users.delete"
    assert scenarios[1].expect == "deny"


def test_load_scenarios_object_and_jsonl(tmp_path):
    obj = tmp_path / "obj.json"
    obj.write_text(
        json.dumps({"scenarios": [{"tool": "kb.search", "expect": "allow"}]}),
        encoding="utf-8",
    )
    assert load_scenarios(obj)[0].expect == "allow"

    jsonl = tmp_path / "scenarios.jsonl"
    jsonl.write_text(
        '{"tool": "kb.search", "expect": "allow"}\n{"tool": "users.delete"}\n',
        encoding="utf-8",
    )
    loaded = load_scenarios(jsonl)
    assert len(loaded) == 2
    assert loaded[1].name == "scenario-2"


def test_load_scenarios_empty_file_errors(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_scenarios(path)


def test_simulate_set_uses_composition():
    pset = load_policy_set(
        [EXAMPLES / "policies" / "org-baseline.yaml", EXAMPLES / "policies" / "deploy-bot.yaml"]
    )
    scenarios = [Scenario("destroy", {"agent": "deploy-bot", "tool": "db.destroy"})]
    report = simulate_set(pset, scenarios)
    assert report.results[0].decision.decision == "deny"


def test_cli_simulate_inline_tool(tmp_path, capsys):
    code = main(
        [
            "simulate",
            "--policy",
            str(POLICY),
            "--tool",
            "refund.issue",
            "--agent",
            "support-bot",
            "--args",
            '{"amount": 50}',
        ]
    )
    assert code == 3
    out = capsys.readouterr().out
    assert "decision: approve" in out
    assert "small-refund-needs-approval" in out
    assert "nothing recorded" in out


def test_cli_simulate_action_file(tmp_path, capsys):
    action = tmp_path / "action.json"
    action.write_text(
        json.dumps({"agent": "support-bot", "tool": "kb.search", "args": {}}),
        encoding="utf-8",
    )
    code = main(["simulate", "--policy", str(POLICY), "--action", str(action)])
    assert code == 0
    assert "decision: allow" in capsys.readouterr().out


def test_cli_simulate_batch_all_expectations_pass(tmp_path, capsys):
    scenarios = EXAMPLES / "simulate-scenarios.json"
    code = main(["simulate", "--policy", str(POLICY), "--actions", str(scenarios)])
    assert code == 0
    out = capsys.readouterr().out
    assert "summary:" in out
    assert "PASS" in out
    assert "nothing recorded" in out


def test_cli_simulate_batch_failed_expectation_exits_2(tmp_path, capsys):
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(
        json.dumps([{"name": "wrong", "tool": "kb.search", "expect": "deny"}]),
        encoding="utf-8",
    )
    code = main(["simulate", "--policy", str(POLICY), "--actions", str(scenarios)])
    assert code == 2
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "expectations failed: 1" in out


def test_cli_simulate_batch_no_expectations_worst_decision_wins(tmp_path, capsys):
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(
        json.dumps([{"tool": "kb.search"}, {"tool": "refund.issue", "args": {"amount": 50}}]),
        encoding="utf-8",
    )
    code = main(["simulate", "--policy", str(POLICY), "--actions", str(scenarios)])
    assert code == 3


def test_cli_simulate_json_format(tmp_path, capsys):
    code = main(
        [
            "simulate",
            "--policy",
            str(POLICY),
            "--tool",
            "kb.search",
            "--agent",
            "support-bot",
            "--format",
            "json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["decision"] == "allow"
    assert payload["results"][0]["rule"] == "kb-search"
    assert payload["counts"] == {"allow": 1, "deny": 0, "approve": 0}
    assert payload["ok"] is True


def test_cli_simulate_bad_args_json_exits_2(capsys):
    code = main(["simulate", "--policy", str(POLICY), "--tool", "kb.search", "--args", "not-json"])
    assert code == 2
    assert "policy-kit:" in capsys.readouterr().err


def test_cli_simulate_needs_an_input(capsys):
    code = main(["simulate", "--policy", str(POLICY)])
    assert code == 2
    assert "needs --action, --actions, or --tool" in capsys.readouterr().err
