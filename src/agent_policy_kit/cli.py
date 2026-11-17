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
    policy-kit simulate --policy policy.yaml --tool refund.issue --args '{"amount": 50}'
    policy-kit simulate --policy policy.yaml --actions scenarios.json
    policy-kit validate policy.yaml org-baseline.yaml
    policy-kit explain --policy policy.yaml --action action.json
    policy-kit export --policy policy.yaml --format rego --output policy.rego
    policy-kit export --policy policy.yaml --format cedar
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
    export_policy,
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
from agent_policy_kit.simulate import Scenario, load_scenarios, simulate_many, simulate_set

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


def _cmd_simulate(args: argparse.Namespace) -> int:
    """Simulate one tool call, an inline call, or a batch of scenarios.

    Simulation never records anything: there is deliberately no
    --approvals or --audit option here. With no expectations, the exit
    code follows the decision (single call: 0 allow, 2 deny, 3
    approve; batch: the most restrictive decision present). When any
    scenario carries an expectation, the exit code answers whether
    the policy behaved as expected instead: 0 when every expectation
    held, 2 when one failed.
    """
    try:
        if len(args.policy) == 1:
            single = load_policy(args.policy[0])
            pset = None
        else:
            single = None
            pset = load_policy_set(args.policy, strategy=args.combine)
    except (PolicyError, OSError, ValueError) as exc:
        print(f"policy-kit: {exc}", file=sys.stderr)
        return 2

    is_batch = bool(args.actions)
    try:
        if args.actions:
            scenarios = load_scenarios(args.actions)
        elif args.action:
            action = _read_json(args.action)
            if not isinstance(action, dict):
                print("policy-kit: action file must contain a JSON object", file=sys.stderr)
                return 2
            scenarios = [Scenario(name="action", action=action)]
        elif args.tool:
            inline_args: dict[str, Any] = {}
            if args.args:
                inline_args = json.loads(args.args)
                if not isinstance(inline_args, dict):
                    print("policy-kit: --args must be a JSON object", file=sys.stderr)
                    return 2
            action = {"agent": args.agent, "tool": args.tool, "args": inline_args}
            if args.resource:
                action["resource"] = args.resource
            scenarios = [Scenario(name="inline", action=action)]
        else:
            print(
                "policy-kit: simulate needs --action, --actions, or --tool",
                file=sys.stderr,
            )
            return 2
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"policy-kit: cannot read simulation input: {exc}", file=sys.stderr)
        return 2

    report = simulate_many(single, scenarios) if pset is None else simulate_set(pset, scenarios)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "results": [
                        {
                            "name": r.name,
                            "decision": r.decision.decision,
                            "rule": r.decision.rule,
                            "reason": r.decision.reason,
                            "policy": r.decision.policy,
                            "expected": r.expected,
                            "passed": r.passed,
                        }
                        for r in report.results
                    ],
                    "counts": report.counts,
                    "ok": report.ok,
                },
                indent=2,
            )
        )
    elif not is_batch:
        result = report.results[0]
        decision = result.decision
        print(f"decision: {decision.decision}")
        print(f"rule:     {decision.rule or '(policy default)'}")
        print(f"reason:   {decision.reason}")
        if result.expected is not None:
            verdict = "PASS" if result.passed else "FAIL"
            print(f"expect:   {result.expected} ({verdict})")
        print("simulate: nothing recorded, no approval requested, no audit line written")
    else:
        for result in report.results:
            line = (
                f"{result.name}: {result.decision.decision} "
                f"via {result.decision.rule or '(policy default)'}"
            )
            if result.expected is not None:
                line += f" [expected {result.expected}: {'PASS' if result.passed else 'FAIL'}]"
            print(line)
        counts = report.counts
        print(
            f"summary: {len(report.results)} scenarios, "
            f"{counts['allow']} allow, {counts['deny']} deny, {counts['approve']} approve"
        )
        if report.failures:
            print(f"expectations failed: {len(report.failures)}")
        print("simulate: nothing recorded, no approval requested, no audit line written")

    if any(r.expected is not None for r in report.results):
        # Expectation mode: the exit code answers "did the policy behave
        # as expected", not "what was the decision".
        return EXIT_ALLOW if report.ok else EXIT_DENY
    if len(report.results) == 1:
        decision_name = report.results[0].decision.decision
        return {"allow": EXIT_ALLOW, "deny": EXIT_DENY, "approve": EXIT_APPROVE}[decision_name]
    counts = report.counts
    if counts["deny"]:
        return EXIT_DENY
    if counts["approve"]:
        return EXIT_APPROVE
    return EXIT_ALLOW


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


def _cmd_export(args: argparse.Namespace) -> int:
    """Export one policy to Rego or Cedar, printing every loss."""
    try:
        policy = load_policy(args.policy)
    except (PolicyError, OSError) as exc:
        print(f"policy-kit: {exc}", file=sys.stderr)
        return 2
    result = export_policy(policy, args.format)
    for warning in result.warnings:
        print(f"policy-kit: warning: {warning}", file=sys.stderr)
    if args.output:
        Path(args.output).write_text(result.text, encoding="utf-8")
        print(f"wrote {args.format} export of {policy.name} to {args.output}")
    else:
        print(result.text, end="")
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

    simulate_cmd = sub.add_parser(
        "simulate",
        help="dry-run one tool call or a batch of scenarios; records nothing",
    )
    simulate_cmd.add_argument(
        "--policy",
        required=True,
        action="append",
        help="policy YAML file; repeat to compose several policies",
    )
    simulate_cmd.add_argument("--action", default=None, help="single action JSON file")
    simulate_cmd.add_argument(
        "--actions",
        default=None,
        help="scenario file: JSON list, JSON object with a scenarios list, or JSONL",
    )
    simulate_cmd.add_argument("--tool", default=None, help="tool name for an inline simulation")
    simulate_cmd.add_argument("--agent", default="simulate", help="agent name for --tool mode")
    simulate_cmd.add_argument("--resource", default=None, help="resource for --tool mode")
    simulate_cmd.add_argument(
        "--args", default=None, help="tool arguments as a JSON object string, for --tool mode"
    )
    simulate_cmd.add_argument("--format", choices=["table", "json"], default="table")
    simulate_cmd.add_argument(
        "--combine",
        choices=STRATEGIES,
        default="deny_overrides",
        help="precedence when several --policy files are given (default: deny_overrides)",
    )
    simulate_cmd.set_defaults(func=_cmd_simulate)

    validate = sub.add_parser("validate", help="check policy files for schema errors")
    validate.add_argument("policies", nargs="+", help="policy YAML files to validate")
    validate.set_defaults(func=_cmd_validate)

    explain_cmd = sub.add_parser("explain", help="show the rule-by-rule trace for one action")
    explain_cmd.add_argument("--policy", required=True, help="policy YAML file")
    explain_cmd.add_argument("--action", required=True, help="action JSON file")
    explain_cmd.add_argument("--format", choices=["table", "json"], default="table")
    explain_cmd.set_defaults(func=_cmd_explain)

    export_cmd = sub.add_parser(
        "export", help="export a policy to OPA/Rego or Cedar (losses are printed as warnings)"
    )
    export_cmd.add_argument("--policy", required=True, help="policy YAML file")
    export_cmd.add_argument(
        "--format",
        choices=["rego", "cedar"],
        default="rego",
        help="target language (default: rego)",
    )
    export_cmd.add_argument("--output", default=None, help="write to this file instead of stdout")
    export_cmd.set_defaults(func=_cmd_export)

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
