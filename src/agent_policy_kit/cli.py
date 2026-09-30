"""Command line interface.

Exit codes are the contract CI relies on:

    0  allow            the action may proceed
    2  deny             the action must not proceed
    3  approve          a human must decide; request id is printed

Examples:

    policy-kit check --policy policy.yaml --action action.json
    policy-kit check --policy policy.yaml --action action.json \\
        --approvals approvals.json --audit audit.log --format json
    policy-kit check --policy org-baseline.yaml --policy deploy-bot.yaml \\
        --action action.json --combine deny_overrides
    policy-kit check --policy policy.yaml --action action.json --dry-run
    policy-kit validate policy.yaml org-baseline.yaml
    policy-kit explain --policy policy.yaml --action action.json
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
    evaluate_set,
    explain,
    list_pending,
    load_policy,
    load_policy_set,
    log_decision,
    log_resolution,
    read_tail,
    request_approval,
    resolve_approval,
)
from agent_policy_kit.compose import STRATEGIES
from agent_policy_kit.policy import PolicyError

EXIT_ALLOW = 0
EXIT_DENY = 2
EXIT_APPROVE = 3


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        if len(args.policy) == 1:
            policy = load_policy(args.policy[0])
            decide = lambda action: evaluate(policy, action)  # noqa: E731
        else:
            pset = load_policy_set(args.policy, strategy=args.combine)
            decide = lambda action: evaluate_set(pset, action)  # noqa: E731
    except (PolicyError, OSError, ValueError) as exc:
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

    decision = decide(action)
    approval_request_id = None
    if args.dry_run:
        recorded = False
    else:
        recorded = True
        if decision.decision == "approve" and args.approvals:
            store = ApprovalStore(args.approvals)
            request = request_approval(
                store,
                action,
                decision.policy,
                decision.rule or "",
                ttl_minutes=args.ttl_minutes,
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
        elif decision.decision == "approve" and not recorded:
            print("approval: dry run, request not recorded")
        elif decision.decision == "approve":
            print("approval: no approval store configured, request not recorded")
    if args.dry_run:
        print("dry-run: decision computed, nothing recorded")

    return {"allow": EXIT_ALLOW, "deny": EXIT_DENY, "approve": EXIT_APPROVE}[decision.decision]


def _cmd_validate(args: argparse.Namespace) -> int:
    """Check policy files for schema errors without evaluating anything."""
    failed = 0
    for path in args.policies:
        try:
            policy = load_policy(path)
        except (PolicyError, OSError) as exc:
            print(f"{path}: INVALID: {exc}", file=sys.stderr)
            failed += 1
        else:
            print(f"{path}: valid ({policy.name}, {len(policy.rules)} rule(s))")
    return 2 if failed else 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """Show the rule-by-rule trace for one action."""
    try:
        policy = load_policy(args.policy)
    except (PolicyError, OSError) as exc:
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

    explanation = explain(policy, action)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "decision": explanation.decision.decision,
                    "rule": explanation.decision.rule,
                    "reason": explanation.decision.reason,
                    "policy": explanation.decision.policy,
                    "traces": [
                        {
                            "rule": t.name,
                            "decision": t.decision,
                            "matched": t.matched,
                            "hits": t.hits,
                            "misses": t.misses,
                        }
                        for t in explanation.traces
                    ],
                },
                indent=2,
            )
        )
        return 0
    print(f"decision: {explanation.decision.decision}")
    print(f"rule:     {explanation.decision.rule or '(policy default)'}")
    print(f"reason:   {explanation.decision.reason}")
    print()
    print(f"rule trace ({len(explanation.traces)} rules, first match wins):")
    for trace in explanation.traces:
        mark = ">" if trace.matched else "x"
        print(f"  {mark} {trace.name} ({trace.decision})")
        for hit in trace.hits:
            print(f"      + {hit}")
        for miss in trace.misses:
            print(f"      - {miss}")
    return 0


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
    check.add_argument(
        "--policy",
        required=True,
        action="append",
        help="policy YAML file; repeat to compose several policies",
    )
    check.add_argument("--action", required=True, help="action JSON file")
    check.add_argument("--format", choices=["table", "json"], default="table")
    check.add_argument(
        "--combine",
        choices=STRATEGIES,
        default="deny_overrides",
        help="precedence when several --policy files are given (default: deny_overrides)",
    )
    check.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate without recording approvals or writing to the audit log",
    )
    check.add_argument(
        "--approvals", default=None, help="approval store JSON file (records approve requests)"
    )
    check.add_argument("--audit", default=None, help="audit JSONL log file")
    check.add_argument(
        "--ttl-minutes", type=int, default=30, help="approval request TTL in minutes (default 30)"
    )
    check.set_defaults(func=_cmd_check)

    validate = sub.add_parser("validate", help="check policy files for schema errors")
    validate.add_argument("policies", nargs="+", help="policy YAML files to validate")
    validate.set_defaults(func=_cmd_validate)

    explain_cmd = sub.add_parser("explain", help="show the rule-by-rule trace for one action")
    explain_cmd.add_argument("--policy", required=True, help="policy YAML file")
    explain_cmd.add_argument("--action", required=True, help="action JSON file")
    explain_cmd.add_argument("--format", choices=["table", "json"], default="table")
    explain_cmd.set_defaults(func=_cmd_explain)

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
