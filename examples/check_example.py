"""Evaluate a few actions against the support-bot policy.

Run from the repo root:

    python examples/check_example.py
"""

from pathlib import Path

from agent_policy_kit import evaluate, load_policy

POLICY = Path(__file__).parent / "policies" / "support-bot.yaml"

ACTIONS = [
    {"agent": "support-bot", "tool": "kb.search",
     "args": {"q": "how do refunds work"}},
    {"agent": "support-bot", "tool": "tickets.read",
     "args": {"ticket": "T-1042", "assignee": "support-bot"}},
    {"agent": "support-bot", "tool": "refund.issue",
     "args": {"ticket": "T-1042", "amount": 45, "currency": "USD"}},
    {"agent": "support-bot", "tool": "refund.issue",
     "args": {"ticket": "T-1042", "amount": 4500, "currency": "USD"}},
    {"agent": "support-bot", "tool": "users.delete",
     "args": {"user": "u-99"}},
]


def main() -> None:
    policy = load_policy(POLICY)
    print(f"policy: {policy.name} (default: {policy.default})\n")
    for action in ACTIONS:
        decision = evaluate(policy, action)
        args = ", ".join(f"{k}={v}" for k, v in action["args"].items())
        print(f"{action['tool']}({args})")
        print(f"  -> {decision.decision}: {decision.reason}\n")


if __name__ == "__main__":
    main()
