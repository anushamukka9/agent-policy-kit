"""End-to-end approval flow: evaluate, request, approve, audit.

Run from the repo root:

    python examples/approval_flow.py

Uses a temporary store and audit log under /tmp so nothing is left
behind. For the persistent CLI version of this flow, see docs/cli.md.
"""

import tempfile
from pathlib import Path

from agent_policy_kit import (
    ApprovalStore,
    evaluate,
    list_pending,
    load_policy,
    log_decision,
    log_resolution,
    read_tail,
    request_approval,
    resolve_approval,
)

POLICY = Path(__file__).parent / "policies" / "deploy-bot.yaml"

ACTION = {
    "agent": "deploy-bot",
    "tool": "deploy",
    "args": {"env": "staging", "ref": "abc123"},
}


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="policy-kit-demo-"))
    store_path = tmpdir / "approvals.json"
    audit_path = tmpdir / "audit.log"

    policy = load_policy(POLICY)
    decision = evaluate(policy, ACTION)
    print(f"decision: {decision.decision} ({decision.reason})")

    store = ApprovalStore(store_path)
    request = request_approval(store, ACTION, policy.name,
                               decision.rule or "", ttl_minutes=30)
    log_decision(audit_path, decision, ACTION, request["id"])
    print(f"approval requested: {request['id']}")

    print(f"pending: {[r['id'] for r in list_pending(store)]}")

    resolved = resolve_approval(store, request["id"], approved=True,
                                by="on-call-human", note="change window is fine")
    log_resolution(audit_path, resolved)
    print(f"resolved: {resolved['id']} -> {resolved['status']}")

    print("\naudit log:")
    for rec in read_tail(audit_path, 10):
        print(f"  {rec['ts']} {rec['kind']:10} {rec['decision']}")


if __name__ == "__main__":
    main()
