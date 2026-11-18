"""Policy simulation: dry-run decisions without recording anything.

``check`` answers one live question and, unless you pass ``--dry-run``,
writes to the approval store and the audit log. Simulation is the
other half of the workflow: you are editing a policy, or rehearsing
what an agent is about to do, and you want to know what *would*
happen across a whole set of tool calls before any of them run.

Nothing here records anything. There is no approval store, no audit
log, no side effect to clean up. A simulation is a pure read of the
policy, which is what makes it safe to run in an editor hook, in CI
against a proposed policy change, or in a notebook while you tune
rules.

A scenario is one named tool call plus, optionally, the decision you
expect. Expectations turn a simulation into a regression test for
your policy: edit a rule, re-run the scenarios, and any scenario whose
decision moved shows up as a failure with the rule that decided it.

Scenario files come in three shapes, all JSON:

- a list of entries,
- an object with a ``scenarios`` list,
- JSONL, one entry per line.

Each entry is either a bare action object (``agent`` / ``tool`` /
``args`` / ``resource``), or a wrapped object with ``name``, ``action``
and an optional ``expect`` (``allow``, ``deny`` or ``approve``). A flat
entry with ``name`` and ``expect`` next to the action fields works too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_policy_kit.compose import PolicySet, evaluate_set
from agent_policy_kit.engine import Decision, evaluate
from agent_policy_kit.policy import Policy

DECISIONS = ("allow", "deny", "approve")


@dataclass
class Scenario:
    """One named tool call to simulate, with an optional expected decision."""

    name: str
    action: dict[str, Any]
    expect: str | None = None

    def __post_init__(self) -> None:
        if self.expect is not None and self.expect not in DECISIONS:
            raise ValueError(
                f"scenario {self.name!r}: expect must be one of {DECISIONS}, got {self.expect!r}"
            )
        if not isinstance(self.action, dict) or "tool" not in self.action:
            raise ValueError(f"scenario {self.name!r}: action needs at least a 'tool' field")


@dataclass
class SimulationResult:
    """The decision for one scenario, and whether it matched the expectation."""

    scenario: Scenario
    decision: Decision

    @property
    def name(self) -> str:
        return self.scenario.name

    @property
    def expected(self) -> str | None:
        return self.scenario.expect

    @property
    def passed(self) -> bool | None:
        """True or False when the scenario carried an expectation, else None."""
        if self.scenario.expect is None:
            return None
        return self.decision.decision == self.scenario.expect


@dataclass
class SimulationReport:
    """Every result from one simulation run, plus the tallies a caller needs."""

    results: list[SimulationResult] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tallies = {"allow": 0, "deny": 0, "approve": 0}
        for result in self.results:
            tallies[result.decision.decision] += 1
        return tallies

    @property
    def failures(self) -> list[SimulationResult]:
        return [r for r in self.results if r.passed is False]

    @property
    def ok(self) -> bool:
        """True when every expectation held. Vacuously true with no expectations."""
        return not self.failures


def _coerce_scenario(entry: Any, index: int) -> Scenario:
    if not isinstance(entry, dict):
        raise ValueError(f"scenario {index}: entry must be a JSON object")
    if "action" in entry:
        action = entry["action"]
        name = str(entry.get("name") or f"scenario-{index + 1}")
        expect = entry.get("expect")
    elif "tool" in entry:
        action = {k: v for k, v in entry.items() if k not in ("name", "expect")}
        name = str(entry.get("name") or f"scenario-{index + 1}")
        expect = entry.get("expect")
    else:
        raise ValueError(f"scenario {index}: entry needs an 'action' object or a 'tool' field")
    return Scenario(name=name, action=action, expect=expect)


def load_scenarios(source: str | Path) -> list[Scenario]:
    """Load scenarios from a JSON list, a ``scenarios`` object, or JSONL."""
    text = Path(source).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"scenario file {source} is empty")
    entries: list[Any]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        if isinstance(parsed, dict) and "scenarios" in parsed:
            entries = parsed["scenarios"]
        elif isinstance(parsed, list):
            entries = parsed
        elif isinstance(parsed, dict):
            entries = [parsed]
        else:
            raise ValueError("scenario file must hold a list, an object, or JSONL lines")
    if not isinstance(entries, list) or not entries:
        raise ValueError("scenario file holds no scenarios")
    return [_coerce_scenario(entry, i) for i, entry in enumerate(entries)]


def simulate(policy: Policy, action: dict[str, Any], now: datetime | None = None) -> Decision:
    """Simulate one tool call against one policy. Records nothing, ever."""
    now = now or datetime.now(timezone.utc)
    return evaluate(policy, action, now)


def simulate_many(
    policy: Policy, scenarios: list[Scenario], now: datetime | None = None
) -> SimulationReport:
    """Simulate every scenario against one policy, in file order."""
    now = now or datetime.now(timezone.utc)
    return SimulationReport(
        results=[
            SimulationResult(scenario=s, decision=evaluate(policy, s.action, now))
            for s in scenarios
        ]
    )


def simulate_set(
    pset: PolicySet, scenarios: list[Scenario], now: datetime | None = None
) -> SimulationReport:
    """Simulate every scenario against a composed policy set."""
    now = now or datetime.now(timezone.utc)
    return SimulationReport(
        results=[
            SimulationResult(scenario=s, decision=evaluate_set(pset, s.action, now))
            for s in scenarios
        ]
    )
