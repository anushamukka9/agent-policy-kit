"""agent-policy-kit: policy-as-code guardrails for AI agents.

Write YAML rules that say what your agents may do. Every action gets a
deterministic decision: allow, deny, or approve (human-in-the-loop).
"""

from agent_policy_kit.approvals import (
    ApprovalStore,
    list_pending,
    request_approval,
    resolve_approval,
    sweep_expired,
)
from agent_policy_kit.audit import log_decision, log_resolution, read_tail
from agent_policy_kit.engine import Decision, evaluate
from agent_policy_kit.policy import Policy, PolicyError, Rule, load_policy

__version__ = "0.1.0"

__all__ = [
    "ApprovalStore",
    "Decision",
    "Policy",
    "PolicyError",
    "Rule",
    "evaluate",
    "list_pending",
    "load_policy",
    "log_decision",
    "log_resolution",
    "read_tail",
    "request_approval",
    "resolve_approval",
    "sweep_expired",
]
