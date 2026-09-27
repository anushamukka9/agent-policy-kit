"""Command line interface.

Exit codes are the contract CI relies on:

    0  allow            the action may proceed
    2  deny             the action must not proceed
    3  approve          a human must decide; request id is printed

Examples:

    policy-kit check --policy policy.yaml --action action.json
    policy-kit check --policy policy.yaml --action action.json \\
        --approvals approvals.json --audit audit.log --format json
    policy-kit pending --approvals approvals.json
    policy-kit approve apr-3f9a1c --approvals approvals.json --by anusha
    policy-kit deny apr-3f9a1c --approvals approvals.json --by anusha --note "too risky"
    policy-kit audit --audit audit.log --tail 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
from agent_policy_kit.policy import PolicyError

EXIT_ALLOW = 0
EXIT_DENY = 2
EXIT_APPROVE = 3


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print(f"policy-kit: {exc}", file=sys.stderr)
        return 2
    try:
        action = _read_json(args.action)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"policy-kit: cannot read action file: {exc}", file=sys.stderr)
        return 2
    if not isinstance(action, dict):
        print("policy-kit: action file must contain a JSON object", file=sys.stderr)
        return 2

    decision = evaluate(policy, action)
    approval_request_id = None
    if decision.decision == "approve" and args.approvals:
        store = ApprovalStore(args.approvals)
        request = request_approval(
            store, action, policy.name, decision.rule or "", ttl_minutes=args.ttl_minutes
        )
        approval_request_id = request["id"]
    if args.audit:
        log_decision(args.audit, decision, action, approval_request_id)

    if args.format == "json":
        payload = {
            "decision": decision.decision,
            "rule": decision.rule,
            "reason": decision.reason,
            "policy": decision.policy,
            "approval_request_id": approval_request_id,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"decision: {decision.decision}")
        print(f"rule:     {decision.rule or '(policy default)'}")
        print(f"reason:   {decision.reason}")
        if approval_request_id:
            print(f"approval: {approval_request_id} (pending, expires in {args.ttl_minutes} min)")
        elif decision.decision == "approve":
            print("approval: no approval store configured, request not recorded")

    return {"allow": EXIT_ALLOW, "deny": EXIT_DENY, "approve": EXIT_APPROVE}[decision.decision]


def _cmd_pending(args: argparse.Namespace) -> int:
    store = ApprovalStore(args.approvals)
    pending = list_pending(store)
    if args.format == "json":
        print(json.dumps(pending, indent=2))
    elif not pending:
        print("no pending approval requests")
    else:
        for req in pending:
            action = req["action"]
            print(
                f"{req['id']}  {action.get('agent', '?')} "
                f"{action.get('tool', '?')}  requested {req['requested_at']} "
                f"expires {req['expires_at']}"
            )
            print(f"    rule: {req['rule']}  reason: pending human decision")
    return 0


def _cmd_resolve(args: argparse.Namespace, approved: bool) -> int:
    store = ApprovalStore(args.approvals)
    try:
        request = resolve_approval(
            store, args.request_id, approved, by=args.by, note=args.note or ""
        )
    except (KeyError, ValueError) as exc:
        print(f"policy-kit: {exc}", file=sys.stderr)
        return 2
    if args.audit:
        log_resolution(args.audit, request)
    verb = "approved" if approved else "denied"
    print(f"{args.request_id} {verb} by {args.by}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    records = read_tail(args.audit, args.tail)
    if not records:
        print("audit log is empty")
        return 0
    for rec in records:
        print(
            f"{rec['ts']}  {rec['kind']:10} {rec['decision']:8} "
            f"{rec['policy']} / {rec['rule'] or '(default)'}"
        )
        print(f"    {rec['reason']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="policy-kit",
        description="Policy-as-code guardrails for AI agents: allow, deny, or route to a human.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="evaluate one action against a policy")
    check.add_argument("--policy", required=True, help="policy YAML file")
    check.add_argument("--action", required=True, help="action JSON file")
    check.add_argument("--format", choices=["table", "json"], default="table")
    check.add_argument(
        "--approvals", default=None, help="approval store JSON file (records approve requests)"
    )
    check.add_argument("--audit", default=None, help="audit JSONL log file")
    check.add_argument(
        "--ttl-minutes", type=int, default=30, help="approval request TTL in minutes (default 30)"
    )
    check.set_defaults(func=_cmd_check)

    pending = sub.add_parser("pending", help="list pending approval requests")
    pending.add_argument("--approvals", required=True)
    pending.add_argument("--format", choices=["table", "json"], default="table")
    pending.set_defaults(func=_cmd_pending)

    approve = sub.add_parser("approve", help="approve a pending request")
    approve.add_argument("request_id")
    approve.add_argument("--approvals", required=True)
    approve.add_argument("--by", required=True, help="who is approving")
    approve.add_argument("--note", default="", help="optional note")
    approve.add_argument("--audit", default=None, help="audit JSONL log file")
    approve.set_defaults(func=lambda a: _cmd_resolve(a, True))

    deny = sub.add_parser("deny", help="deny a pending request")
    deny.add_argument("request_id")
    deny.add_argument("--approvals", required=True)
    deny.add_argument("--by", required=True, help="who is denying")
    deny.add_argument("--note", default="", help="optional note")
    deny.add_argument("--audit", default=None, help="audit JSONL log file")
    deny.set_defaults(func=lambda a: _cmd_resolve(a, False))

    audit = sub.add_parser("audit", help="show recent audit log entries")
    audit.add_argument("--audit", required=True, help="audit JSONL log file")
    audit.add_argument("--tail", type=int, default=20)
    audit.set_defaults(func=_cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
