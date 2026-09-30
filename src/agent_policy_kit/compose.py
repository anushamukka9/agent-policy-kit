"""Policy composition: one decision from several policies.

Real deployments rarely have a single policy. There is usually an
org-wide baseline ("no prod deploys outside business hours, ever") and
team policies on top ("the deploy bot may touch staging freely"). This
module combines them into one decision with an explicit precedence
strategy, so the layering is visible instead of implicit.

Strategies:

- ``deny_overrides`` (the default): the most restrictive decision
  wins. Any deny beats any approve, any approve beats allow. Use this
  when the baseline is a safety floor no team policy may punch through.
- ``allow_overrides``: the most permissive decision wins. Any allow
  beats any approve, any approve beats deny. Use this when the extra
  policies are carve-outs from a strict baseline.
- ``first_wins``: policies are ordered most-specific to least-specific.
  The first policy with a matching rule decides; when no policy has a
  matching rule, the first policy's default decides. Use this when you
  think of the stack as overrides, like CSS.

Ties inside one strategy go to the earlier policy in the list, so the
order is meaningful and the result is deterministic.

Example:

    from agent_policy_kit import evaluate_set, load_policy_set

    pset = load_policy_set(["policies/org-baseline.yaml", "policies/deploy-bot.yaml"])
    decision = evaluate_set(pset, {"tool": "deploy", "args": {"env": "prod"}})
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_policy_kit.engine import Decision, evaluate
from agent_policy_kit.policy import Policy, load_policy

STRATEGIES = ("deny_overrides", "allow_overrides", "first_wins")

# Lower rank wins. deny_overrides is most-restrictive-first,
# allow_overrides is most-permissive-first.
_RANK = {
    "deny_overrides": {"deny": 0, "approve": 1, "allow": 2},
    "allow_overrides": {"allow": 0, "approve": 1, "deny": 2},
}


@dataclass
class PolicySet:
    """An ordered stack of policies plus the strategy that combines them."""

    policies: list[Policy]
    strategy: str = "deny_overrides"

    def __post_init__(self) -> None:
        if not self.policies:
            raise ValueError("a PolicySet needs at least one policy")
        if self.strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy {self.strategy!r}, want one of {STRATEGIES}")
        names = [p.name for p in self.policies]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate policy names in PolicySet: {names}")

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.policies]


def load_policy_set(paths: list[str | Path], strategy: str = "deny_overrides") -> PolicySet:
    """Load several policy files into one PolicySet, in the given order."""
    return PolicySet([load_policy(p) for p in paths], strategy=strategy)


def _retag(decision: Decision, reason: str) -> Decision:
    return Decision(
        decision=decision.decision,
        rule=decision.rule,
        reason=reason,
        policy=decision.policy,
        action=decision.action,
    )


def evaluate_set(pset: PolicySet, action: dict[str, Any], now: datetime | None = None) -> Decision:
    """Evaluate one action against every policy in the set, then combine.

    The returned decision keeps the winning policy's name and rule, and
    the reason names the strategy so the audit log shows how the layers
    combined.
    """
    now = now or datetime.now(timezone.utc)
    results = [(policy, evaluate(policy, action, now)) for policy in pset.policies]
    count = len(results)
    strategy = pset.strategy

    if strategy == "first_wins":
        for policy, decision in results:
            if decision.rule is not None:
                return _retag(
                    decision,
                    f"policy '{policy.name}' rule '{decision.rule}' -> {decision.decision} "
                    f"(first_wins across {count} policies)",
                )
        policy, decision = results[0]
        return _retag(
            decision,
            f"no rule matched in any policy, first policy '{policy.name}' default "
            f"-> {decision.decision} (first_wins across {count} policies)",
        )

    rank = _RANK[strategy]
    # min() keeps the earliest policy on ties, so the order is the tiebreaker.
    policy, decision = min(results, key=lambda item: rank[item[1].decision])
    if decision.rule is not None:
        reason = (
            f"policy '{policy.name}' rule '{decision.rule}' -> {decision.decision} "
            f"({strategy} across {count} policies)"
        )
    else:
        reason = (
            f"policy '{policy.name}' default -> {decision.decision} "
            f"({strategy} across {count} policies)"
        )
    return _retag(decision, reason)
