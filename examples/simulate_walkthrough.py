"""Simulate tool calls against a policy before any of them run.

Run from the repo root:

    python examples/simulate_walkthrough.py

Three parts. First, one inline what-if: what would happen if the
support bot tried to issue a $50 refund right now? Second, a batch of
scenarios from examples/simulate-scenarios.json, each carrying the
decision I expect, so the run doubles as a regression test for the
policy. Third, the same batch through the Python API, which is how
you would wire simulation into an editor hook or a policy test suite.

Nothing here records anything. No approval request is created for the
$50 refund, and no audit line is written. That is the whole point of
simulate: it is the rehearsal, not the performance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_policy_kit import load_policy, load_scenarios, simulate_many  # noqa: E402
from agent_policy_kit.cli import main  # noqa: E402

HERE = Path(__file__).resolve().parent
POLICY = HERE / "policies" / "support-bot.yaml"
SCENARIOS = HERE / "simulate-scenarios.json"


def main_example() -> None:
    print("== one inline what-if: a $50 refund ==")
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
            '{"ticket": "T-1042", "amount": 50}',
        ]
    )
    print(f"exit code: {code} (3 means a human would have to decide)")
    print()

    print("== a batch of scenarios, checked against expectations ==")
    code = main(["simulate", "--policy", str(POLICY), "--actions", str(SCENARIOS)])
    print(f"exit code: {code} (0 means every expectation held)")
    print()

    print("== the same batch from Python ==")
    report = simulate_many(load_policy(POLICY), load_scenarios(SCENARIOS))
    print(f"counts: {report.counts}")
    print(f"all expectations held: {report.ok}")
    for result in report.results:
        rule = result.decision.rule or "(policy default)"
        print(f"  {result.name}: {result.decision.decision} via {rule}")


if __name__ == "__main__":
    main_example()
