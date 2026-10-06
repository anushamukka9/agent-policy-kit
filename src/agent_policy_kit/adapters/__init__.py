"""Framework adapters: enforce the same YAML policies inside agent frameworks.

``agent_policy_kit.adapters.openai_agents`` and
``agent_policy_kit.adapters.crewai`` wrap your framework's tool calls
with policy checks. The shared pieces (config, decision enforcement,
the two exceptions you catch) live in
``agent_policy_kit.adapters.base`` and are re-exported here.
"""

from agent_policy_kit.adapters.base import (
    AdapterConfig,
    ApprovalRequiredError,
    PolicyDeniedError,
    build_action,
    check_action,
    decide,
)

__all__ = [
    "AdapterConfig",
    "ApprovalRequiredError",
    "PolicyDeniedError",
    "build_action",
    "check_action",
    "decide",
]
