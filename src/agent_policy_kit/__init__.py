"""agent-policy-kit: policy-as-code guardrails for AI agents.

Write YAML rules that say what your agents may do. Every action gets a
deterministic decision: allow, deny, or approve (human-in-the-loop).
"""

from agent_policy_kit.adapters.base import ApprovalRequiredError, PolicyDeniedError
from agent_policy_kit.approvals import (
    ApprovalStore,
    list_pending,
    request_approval,
    resolve_approval,
    sweep_expired,
)
from agent_policy_kit.audit import log_decision, log_resolution, read_tail
from agent_policy_kit.compose import PolicySet, evaluate_set, load_policy_set
from agent_policy_kit.engine import Decision, Explanation, RuleTrace, evaluate, explain
from agent_policy_kit.policy import Policy, PolicyError, Rule, load_policy

__version__ = "0.3.0"

__all__ = [
    "ApprovalRequiredError",
    "ApprovalStore",
    "Decision",
    "Explanation",
    "Policy",
    "PolicyDeniedError",
    "PolicyError",
    "PolicySet",
    "Rule",
    "RuleTrace",
    "evaluate",
    "evaluate_set",
    "explain",
    "list_pending",
    "load_policy",
    "load_policy_set",
    "log_decision",
    "log_resolution",
    "read_tail",
    "request_approval",
    "resolve_approval",
    "sweep_expired",
]
