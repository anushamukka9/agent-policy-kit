"""Compose an org baseline with a team policy.

Run from the repo root:

    python examples/policy_composition.py

Loads examples/policies/org-baseline.yaml (the org-wide floor) together
with examples/policies/deploy-bot.yaml (the team policy) and evaluates
three actions under each composition strategy, so you can see how the
precedence changes the outcome. The clock is fixed to a Wednesday at
10:00 UTC so the time-window rules behave the same on every run.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_policy_kit import evaluate_set, load_policy_set  # noqa: E402
from agent_policy_kit.compose import STRATEGIES  # noqa: E402

POLICIES = Path(__file__).resolve().parent / "policies"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)  # Wednesday, in business hours

ACTIONS = [
    (
        "prod deploy in business hours (team approves, baseline is silent)",
        {"agent": "deploy-bot", "tool": "deploy", "args": {"env": "prod"}},
    ),
    (
        "db migration (team denies it by rule)",
        {"agent": "deploy-bot", "tool": "db.migrate", "args": {}},
    ),
    (
        "destroy tool (baseline denies it by rule)",
        {"agent": "deploy-bot", "tool": "db.destroy", "args": {}},
    ),
]


def main() -> None:
    paths = [POLICIES / "org-baseline.yaml", POLICIES / "deploy-bot.yaml"]
    for strategy in STRATEGIES:
        pset = load_policy_set([str(p) for p in paths], strategy=strategy)
        print(f"strategy: {strategy}")
        for label, action in ACTIONS:
            decision = evaluate_set(pset, action, now=NOW)
            print(f"  {decision.decision:7}  {label}")
            print(f"           {decision.reason}")
        print()


if __name__ == "__main__":
    main()
