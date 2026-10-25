"""Export policies to OPA/Rego and Cedar.

Teams that already run a policy engine should not have to rewrite
their agent policies by hand to adopt this kit, and teams that start
here should not be locked in. This module translates a loaded
:class:`~agent_policy_kit.policy.Policy` into two widely used policy
languages:

- **Rego** (for Open Policy Agent): the translation is faithful.
  Rule order is preserved, so first-match-wins behaves exactly like
  the built-in engine, and all three decisions (allow, deny,
  approve) survive. The output targets OPA 1.0 (Rego v1 syntax) and
  expects the action as ``input`` in the same shape this kit uses:
  ``input.agent``, ``input.tool``, ``input.resource``,
  ``input.args.<field>``. Time windows are evaluated against the
  wall clock (``time.now_ns()`` in UTC) instead of a caller-supplied
  ``now``, which is the one deliberate difference from
  :func:`agent_policy_kit.evaluate`.

- **Cedar** (for Amazon Verified Permissions and friends): Cedar
  has permit/forbid only, no rule order, and no clock, regex,
  string interpolation, or length operation. The translation is
  therefore lossy, and it says so: ``approve`` rules become
  ``forbid`` (fail closed, the
  human step has to happen outside Cedar), any matching ``forbid``
  beats every ``permit`` regardless of source order, and rules that
  use a construct Cedar cannot express are left out of the output
  as comments instead of being silently weakened. Every loss is
  listed in :attr:`ExportResult.warnings`. The generated policies
  read the action from Cedar ``context``: ``context.agent``,
  ``context.tool``, ``context.resource``, and ``context.args``
  (a record mirroring the action's ``args``).

The Rego output was verified against real OPA: every bundled
benchmark scenario produces the same decision and rule name in
both engines. The Cedar output was verified to parse and evaluate
in a real Cedar authorizer. Both are still worth your own review
before production use; the honest limits are spelled out in
docs/export.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_policy_kit.policy import Policy

FORMATS = ("rego", "cedar")

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GLOB_CHARS = ("*", "?", "[")
_DAY_FULL = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}
_COMPARE = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


@dataclass
class ExportResult:
    """One exported policy: the text plus everything the export lost."""

    format: str
    text: str
    warnings: list[str] = field(default_factory=list)

    @property
    def lossy(self) -> bool:
        """True when the target language could not say everything."""
        return bool(self.warnings)


def export_policy(policy: Policy, fmt: str) -> ExportResult:
    """Export ``policy`` to ``fmt`` (one of :data:`FORMATS`)."""
    if fmt == "rego":
        return export_rego(policy)
    if fmt == "cedar":
        return export_cedar(policy)
    raise ValueError(f"unknown export format {fmt!r}, want one of {FORMATS}")


# ---------------------------------------------------------------------------
# Shared helpers


def _sanitize(name: str) -> str:
    """Turn a policy or rule name into a safe identifier fragment."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _quote(value: str) -> str:
    """Quote a string the way both Rego and Cedar accept (JSON style)."""
    return json.dumps(value, ensure_ascii=False)


def _scalar(value: Any) -> str:
    """Render a YAML scalar as a Rego/Cedar literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return _quote(str(value))


def _is_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in _GLOB_CHARS)


class _Unsupported(Exception):
    """A construct the target language cannot express."""


# ---------------------------------------------------------------------------
# Rego


def _rego_args_ref(field_name: str) -> str:
    if _IDENT_RE.fullmatch(field_name):
        return f"input.args.{field_name}"
    return f"input.args[{_quote(field_name)}]"


def _rego_field_ref(path: str) -> str | None:
    """Map a placeholder path like 'agent' or 'args.amount' to input."""
    if path in ("agent", "tool", "resource"):
        return f"input.{path}"
    if path.startswith("args."):
        return _rego_args_ref(path[len("args.") :])
    return None


class _RegoBuilder:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.warnings: list[str] = []
        self.helpers: list[tuple[str, list[list[str]]]] = []
        self.uses_time = False
        self._counter = 0

    # -- string patterns (tool / agent / resource / plain string specs)

    def string_match(self, pattern: str, value_expr: str, where: str) -> list[str]:
        placeholders = _PLACEHOLDER_RE.findall(pattern)
        if placeholders:
            return [self._placeholder_match(pattern, placeholders, value_expr, where)]
        if pattern.startswith("regex:"):
            body = pattern[len("regex:") :]
            return [f"regex.match({_quote('^(?:' + body + ')$')}, {value_expr})"]
        if _is_glob(pattern):
            return [f"glob.match({_quote(pattern)}, null, {value_expr})"]
        return [f"{value_expr} == {_quote(pattern)}"]

    def _placeholder_match(
        self, pattern: str, placeholders: list[str], value_expr: str, where: str
    ) -> str:
        refs: list[str] = []
        for path in placeholders:
            ref = _rego_field_ref(path)
            if ref is None:
                self.warnings.append(
                    f"{where}: placeholder '{{{path}}}' has no input field in Rego; "
                    "it was left as literal text"
                )
                ref = _quote("{" + path + "}")
            refs.append(ref)
        if len(placeholders) == 1 and pattern == "{" + placeholders[0] + "}":
            return f"{value_expr} == {refs[0]}"
        fmt = _PLACEHOLDER_RE.sub("%v", pattern.replace("%", "%%"))
        sprintf = f"sprintf({_quote(fmt)}, [{', '.join(refs)}])"
        if pattern.startswith("regex:"):
            body = fmt[len("regex:") :]
            args = ", ".join(refs)
            return f"regex.match(sprintf({_quote('^(?:' + body + ')$')}, [{args}]), {value_expr})"
        if _is_glob(_PLACEHOLDER_RE.sub("", pattern)):
            return f"glob.match({sprintf}, null, {value_expr})"
        return f"{value_expr} == {sprintf}"

    # -- argument value specs

    def value_match(self, spec: Any, field_name: str, where: str) -> list[str]:
        expr = _rego_args_ref(field_name)
        if isinstance(spec, dict):
            lines: list[str] = []
            for op, operand in spec.items():
                lines.extend(self._operator(op, operand, expr, field_name, where))
            return lines
        if isinstance(spec, str):
            return self.string_match(spec, expr, where)
        return [f"{expr} == {_scalar(spec)}"]

    def _operator(self, op: str, operand: Any, expr: str, field_name: str, where: str) -> list[str]:
        if op == "eq":
            return [f"{expr} == {_scalar(operand)}"]
        if op == "ne":
            return [f"{expr} != {_scalar(operand)}"]
        if op in _COMPARE:
            return [f"{expr} {_COMPARE[op]} {_scalar(operand)}"]
        if op == "in":
            return [f"{expr} in {_rego_collection(operand)}"]
        if op == "not_in":
            values = operand if isinstance(operand, list) else [operand]
            if values and all(isinstance(v, (str, int, float, bool)) for v in values):
                return [f"{expr} != {_scalar(v)}" for v in values]
            return [f"not {expr} in {_rego_collection(operand)}"]
        if op == "regex":
            return [f"regex.match({_quote('^(?:' + str(operand) + ')$')}, {expr})"]
        if op == "glob":
            return self.string_match(str(operand), expr, where)
        if op == "exists":
            key = _quote(field_name)
            if operand:
                return [f"{key} in object.keys(input.args)"]
            return [f"not {key} in object.keys(input.args)"]
        if op == "contains":
            return [f"contains({expr}, {_scalar(operand)})"]
        if op == "startswith":
            return [f"startswith({expr}, {_scalar(operand)})"]
        if op == "endswith":
            return [f"endswith({expr}, {_scalar(operand)})"]
        if op == "length":
            if isinstance(operand, int):
                return [f"count({expr}) == {operand}"]
            lines = []
            if operand.get("min") is not None:
                lines.append(f"count({expr}) >= {operand['min']}")
            if operand.get("max") is not None:
                lines.append(f"count({expr}) <= {operand['max']}")
            return lines
        self.warnings.append(f"{where}: operator '{op}' has no Rego mapping and was dropped")
        return []

    # -- whole match blocks; returns one line list per alternative (any_of)

    def conditions(self, match: dict[str, Any], where: str) -> list[list[str]]:
        alternatives: list[list[str]] = [[]]
        for key, spec in match.items():
            if key == "any_of":
                groups: list[list[str]] = []
                for i, group in enumerate(spec):
                    groups.extend(self.conditions(group, f"{where}.any_of[{i}]"))
                alternatives = [alt + grp for alt in alternatives for grp in groups]
            elif key == "not":
                self._counter += 1
                helper = f"_not_{self._counter}"
                self.helpers.append((helper, self.conditions(spec, f"{where}.not")))
                alternatives = [alt + [f"not {helper}"] for alt in alternatives]
            elif key == "time":
                self.uses_time = True
                start, end = _window_minutes(spec["window"])
                lines = [f"_in_window({start}, {end})"]
                days = spec.get("days")
                if days:
                    wanted = ", ".join(_quote(d) for d in days)
                    lines.append(f"_day_in({{{wanted}}})")
                alternatives = [alt + lines for alt in alternatives]
            elif key in ("agent", "tool", "resource"):
                lines = self.string_match(spec, f"input.{key}", where)
                alternatives = [alt + lines for alt in alternatives]
            elif key.startswith("args."):
                lines = self.value_match(spec, key[len("args.") :], where)
                alternatives = [alt + lines for alt in alternatives]
        return alternatives


def _rego_collection(values: Any) -> str:
    if isinstance(values, list):
        if not values:
            return "set()"
        inner = ", ".join(_scalar(v) for v in values)
        return "{" + inner + "}"
    return _scalar(values)


def _window_minutes(window: str) -> tuple[int, int]:
    start_s, end_s = window.split("-")
    return _hhmm(start_s), _hhmm(end_s)


def _hhmm(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


_REGO_TIME_HELPERS = """\
_in_window(start, end) if {
\tclock := time.clock([time.now_ns(), "UTC"])
\tcur := (clock[0] * 60) + clock[1]
\tstart <= end
\tcur >= start
\tcur < end
}

_in_window(start, end) if {
\tclock := time.clock([time.now_ns(), "UTC"])
\tcur := (clock[0] * 60) + clock[1]
\tstart > end
\tcur >= start
}

_in_window(start, end) if {
\tclock := time.clock([time.now_ns(), "UTC"])
\tcur := (clock[0] * 60) + clock[1]
\tstart > end
\tcur < end
}

_day_in(want) if {
\tday := lower(time.weekday([time.now_ns(), "UTC"]))
\tsubstring(day, 0, 3) in want
}"""


def export_rego(policy: Policy) -> ExportResult:
    """Export ``policy`` as an OPA 1.0 Rego module."""
    builder = _RegoBuilder(policy)
    rule_alternatives = [
        builder.conditions(rule.match, f"rule '{rule.name}'") for rule in policy.rules
    ]

    lines: list[str] = [
        f"# Generated by agent-policy-kit from policy '{policy.name}' (version {policy.version}).",
        "# Do not edit by hand; change the YAML policy and export again.",
        "#",
        "# Evaluate with OPA 1.0: the action goes in as input with the",
        "# same shape agent-policy-kit uses (input.agent, input.tool,",
        "# input.resource, input.args). The result is an object with",
        '# "decision" (allow, deny, or approve) and "rule" (the rule',
        "# name, or null when the policy default applied). Rule order",
        "# is preserved: the first matching rule wins, exactly like",
        "# the built-in engine. Time windows read the wall clock in",
        "# UTC (time.now_ns) instead of a caller-supplied time.",
    ]
    for warning in builder.warnings:
        lines.append(f"# WARNING: {warning}")
    lines.append("")
    lines.append(f"package agentpolicykit.{_sanitize(policy.name)}")
    lines.append("")

    if policy.rules:
        entries = ",\n".join(
            f'\t{{"name": {_quote(rule.name)}, "decision": {_quote(rule.decision)}}}'
            for rule in policy.rules
        )
        lines.append("rules := [")
        lines.append(entries)
        lines.append("]")
        lines.append("")
        lines.append(f'default result := {{"decision": {_quote(policy.default)}, "rule": null}}')
        lines.append("")
        lines.append('result := {"decision": rules[i].decision, "rule": rules[i].name} if {')
        lines.append("\ti := min(matched)")
        lines.append("}")
        for idx, alternatives in enumerate(rule_alternatives):
            lines.append("")
            for alt in alternatives:
                lines.append(f"matched contains {idx} if {{")
                lines.extend(f"\t{line}" for line in alt)
                lines.append("}")
    else:
        lines.append("# This policy has no rules; every action gets the default.")
        lines.append(f'result := {{"decision": {_quote(policy.default)}, "rule": null}}')

    for helper_name, alternatives in builder.helpers:
        lines.append("")
        for alt in alternatives:
            lines.append(f"{helper_name} if {{")
            lines.extend(f"\t{line}" for line in alt)
            lines.append("}")

    if builder.uses_time:
        lines.append("")
        lines.append(_REGO_TIME_HELPERS)

    return ExportResult(format="rego", text="\n".join(lines) + "\n", warnings=builder.warnings)


# ---------------------------------------------------------------------------
# Cedar


def _cedar_args_ref(field_name: str) -> str:
    return f"context.args.{field_name}" if _IDENT_RE.fullmatch(field_name) else ""


class _CedarBuilder:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.warnings: list[str] = []

    def field_expr(self, path: str) -> str:
        if path in ("agent", "tool", "resource"):
            return f"context.{path}"
        if path.startswith("args."):
            ref = _cedar_args_ref(path[len("args.") :])
            if ref:
                return ref
        raise _Unsupported(f"placeholder '{{{path}}}' has no Cedar context field")

    def string_match(self, pattern: str, expr: str) -> str:
        placeholders = _PLACEHOLDER_RE.findall(pattern)
        if placeholders:
            if len(placeholders) == 1 and pattern == "{" + placeholders[0] + "}":
                return f"{expr} == {self.field_expr(placeholders[0])}"
            raise _Unsupported(
                "a placeholder embedded in a longer string (Cedar has no string interpolation)"
            )
        if pattern.startswith("regex:"):
            raise _Unsupported("a regex match (Cedar has no regular expressions)")
        if _is_glob(pattern):
            if "[" in pattern or "?" in pattern:
                raise _Unsupported(
                    "a glob with character classes or single-character wildcards "
                    "(Cedar 'like' only supports '*')"
                )
            return f"{expr} like {_quote(pattern)}"
        return f"{expr} == {_quote(pattern)}"

    def value_match(self, spec: Any, field_name: str) -> str:
        expr = _cedar_args_ref(field_name)
        if not expr:
            raise _Unsupported(f"argument name '{field_name}' is not a Cedar identifier")
        if isinstance(spec, dict):
            parts = [self._operator(op, operand, expr, field_name) for op, operand in spec.items()]
            body = " && ".join(parts)
            if set(spec) == {"exists"}:
                return body
        elif isinstance(spec, str):
            body = self.string_match(spec, expr)
        else:
            body = f"{expr} == {_scalar(spec)}"
        # Cedar errors on a missing record attribute instead of calling
        # it false, so every real access sits behind a has check (the
        # Python engine treats a missing field as a non-match).
        return f"(context.args has {_quote(field_name)} && {body})"

    def _operator(self, op: str, operand: Any, expr: str, field_name: str) -> str:
        if op == "eq":
            return f"{expr} == {_scalar(operand)}"
        if op == "ne":
            return f"{expr} != {_scalar(operand)}"
        if op in _COMPARE:
            return f"{expr} {_COMPARE[op]} {_scalar(operand)}"
        if op in ("in", "not_in"):
            if not isinstance(operand, list):
                raise _Unsupported(f"operator '{op}' without a list value")
            coll = "[" + ", ".join(_scalar(v) for v in operand) + "]"
            if op == "in":
                return f"{coll}.contains({expr})"
            return f"!({coll}.contains({expr}))"
        if op == "regex":
            raise _Unsupported("a regex match (Cedar has no regular expressions)")
        if op == "glob":
            return self.string_match(str(operand), expr)
        if op == "exists":
            if operand:
                return f"context.args has {_quote(field_name)}"
            return f"!(context.args has {_quote(field_name)})"
        if op in ("contains", "startswith", "endswith"):
            return self._like_part(op, operand, expr)
        if op == "length":
            raise _Unsupported(
                "a length check (Cedar has no size or length operation for strings or sets)"
            )
        raise _Unsupported(f"operator '{op}'")

    def _like_part(self, op: str, operand: Any, expr: str) -> str:
        text = str(operand)
        if any(ch in text for ch in ("*", "?", "\\", '"')):
            raise _Unsupported(
                f"operator '{op}' with a value that cannot be written as a Cedar like pattern"
            )
        pattern = {"contains": f"*{text}*", "startswith": f"{text}*", "endswith": f"*{text}"}[op]
        return f"{expr} like {_quote(pattern)}"

    def conditions(self, match: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, spec in match.items():
            if key == "any_of":
                groups = [self.conditions(group) for group in spec]
                parts.append("(" + " || ".join(groups) + ")")
            elif key == "not":
                parts.append(f"!({self.conditions(spec)})")
            elif key == "time":
                raise _Unsupported("a time window (Cedar policies have no clock)")
            elif key in ("agent", "tool", "resource"):
                parts.append(self.string_match(spec, f"context.{key}"))
            elif key.startswith("args."):
                parts.append(self.value_match(spec, key[len("args.") :]))
        return " && ".join(parts)


def export_cedar(policy: Policy) -> ExportResult:
    """Export ``policy`` as Cedar policies, with losses called out."""
    builder = _CedarBuilder(policy)
    warnings = builder.warnings

    effects: set[str] = set()
    blocks: list[str] = []
    skipped: list[str] = []
    for rule in policy.rules:
        effect = "permit" if rule.decision == "allow" else "forbid"
        try:
            condition = builder.conditions(rule.match)
        except _Unsupported as exc:
            warnings.append(
                f"rule '{rule.name}' was NOT exported: it uses {exc}. "
                "Export it by hand or keep enforcing it in agent-policy-kit"
            )
            keys = ", ".join(sorted(rule.match))
            skipped.append(
                f"// NOT EXPORTED: rule '{rule.name}' ({rule.decision}) uses {exc}.\n"
                f"// Source match keys: {keys}. See the warnings in the export result."
            )
            continue
        if rule.decision == "approve":
            warnings.append(
                f"rule '{rule.name}': Cedar has no approve decision, so the rule "
                "was exported as forbid. The human approval step has to happen "
                "outside Cedar; treat this forbid as 'needs a human', not as a "
                "hard no"
            )
        effects.add(effect)
        header = f'@id("{policy.name}.{rule.name}")'
        if rule.description:
            header = f"// {rule.description}\n{header}"
        blocks.append(
            f"{header}\n{effect} (principal, action, resource) when {{\n\t{condition}\n}};"
        )

    if "permit" in effects and "forbid" in effects:
        warnings.append(
            "Cedar has no rule order: any matching forbid wins over every permit, "
            "while agent-policy-kit is first-match-wins. If a source deny rule was "
            "meant to apply only after an earlier allow rule matched, the exported "
            "policies are stricter than the source"
        )
    if policy.default == "allow":
        warnings.append(
            "the policy default is allow, exported as a trailing unconditional "
            "permit. Matching forbid policies still win over it in Cedar"
        )
        blocks.append(
            "// Policy default: allow (exported as a catch-all permit).\n"
            "permit (principal, action, resource);"
        )
    elif policy.default == "approve":
        warnings.append(
            "the policy default is approve, which Cedar cannot express. Nothing "
            "was emitted for it, so unmatched actions are implicitly forbidden "
            "in Cedar (stricter than the source)"
        )

    lines: list[str] = [
        f"// Generated by agent-policy-kit from policy '{policy.name}' (version {policy.version}).",
        "// Do not edit by hand; change the YAML policy and export again.",
        "//",
        "// The action is expected in context: context.agent, context.tool,",
        "// context.resource, and context.args (a record mirroring the",
        "// action's args). Cedar has no approve decision, no rule order,",
        "// and no clock; read the export warnings before relying on this",
        "// file. Every lossy rule is listed there, and rules Cedar cannot",
        "// express at all appear below as NOT EXPORTED comments.",
    ]
    if policy.default == "deny":
        lines.append("// Policy default is deny: in Cedar, anything no permit")
        lines.append("// matches is forbidden, so the default needs no policy.")
    lines.append("")
    for block in blocks:
        lines.append(block)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    if skipped:
        lines.append("")
        lines.extend(skipped)
    return ExportResult(format="cedar", text="\n".join(lines) + "\n", warnings=warnings)
