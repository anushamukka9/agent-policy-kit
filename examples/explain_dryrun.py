"""Explain a surprising decision, then dry-run an approval.

Run from the repo root:

    python examples/explain_dryrun.py

A refund for $50 against the support-bot policy needs a human, which is
expected. But a refund for $250 is denied outright, and the denial is
worth understanding: ``explain`` prints the rule-by-rule trace so you
can see which conditions fired and which said no.

The second half shows --dry-run through the CLI: an approve decision is
computed and printed, but no approval request is recorded and nothing
is written to the audit log.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_policy_kit import explain, load_policy  # noqa: E402
from agent_policy_kit.cli import main  # noqa: E402

POLICY = Path(__file__).resolve().parent / "policies" / "support-bot.yaml"


def print_trace(explanation) -> None:
    decision = explanation.decision
    print(f"decision: {decision.decision} ({decision.reason})")
    for trace in explanation.traces:
        mark = ">" if trace.matched else "x"
        print(f"  {mark} {trace.name} ({trace.decision})")
        for miss in trace.misses:
            print(f"      - {miss}")
    print()


def main_example() -> None:
    policy = load_policy(POLICY)

    print("== why is a $250 refund denied? ==")
    action = {
        "agent": "support-bot",
        "tool": "refund.issue",
        "args": {"ticket": "T-1042", "amount": 250},
    }
    print_trace(explain(policy, action))

    print("== dry-run of a $50 refund (needs approval) ==")
    with tempfile.TemporaryDirectory() as tmp:
        action_path = Path(tmp) / "action.json"
        action_path.write_text(
            json.dumps(
                {
                    "agent": "support-bot",
                    "tool": "refund.issue",
                    "args": {"ticket": "T-1042", "amount": 50},
                }
            ),
            encoding="utf-8",
        )
        approvals = Path(tmp) / "approvals.json"
        audit = Path(tmp) / "audit.log"
        code = main(
            [
                "check",
                "--policy",
                str(POLICY),
                "--action",
                str(action_path),
                "--approvals",
                str(approvals),
                "--audit",
                str(audit),
                "--dry-run",
            ]
        )
        print(f"exit code: {code} (3 means a human must decide)")
        print(f"approvals file exists: {approvals.exists()}")
        print(f"audit log exists: {audit.exists()}")


if __name__ == "__main__":
    main_example()
