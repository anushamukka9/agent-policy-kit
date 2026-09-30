import textwrap
from pathlib import Path

import pytest

from agent_policy_kit.policy import PolicyError, load_policy

GOOD = textwrap.dedent("""\
    name: test-policy
    version: 2
    description: a test
    default: allow
    rules:
      - name: r1
        decision: allow
        match:
          tool: tickets.read
      - name: r2
        description: second rule
        decision: approve
        match:
          tool: deploy
          args.env: prod
    """)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_good_policy(tmp_path):
    policy = load_policy(write(tmp_path, GOOD))
    assert policy.name == "test-policy"
    assert policy.version == 2
    assert policy.default == "allow"
    assert [r.name for r in policy.rules] == ["r1", "r2"]
    assert policy.rules[0].match == {"tool": "tickets.read"}
    assert policy.rules[1].decision == "approve"


def test_defaults(tmp_path):
    policy = load_policy(write(tmp_path, "name: minimal\nrules: []\n"))
    assert policy.default == "deny"
    assert policy.version == 1
    assert policy.description == ""
    assert policy.rules == []


def test_bad_decision_rejected(tmp_path):
    text = "name: x\nrules:\n  - name: r\n    decision: maybe\n    match:\n      tool: a\n"
    with pytest.raises(PolicyError):
        load_policy(write(tmp_path, text))


def test_bad_default_rejected(tmp_path):
    with pytest.raises(PolicyError):
        load_policy(write(tmp_path, "name: x\ndefault: sometimes\n"))


def test_unknown_match_key_rejected(tmp_path):
    text = "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n      frobnicate: yes\n"
    with pytest.raises(PolicyError, match="unknown match condition"):
        load_policy(write(tmp_path, text))


def test_duplicate_rule_names_rejected(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n  - name: r\n    decision: deny\n    match:\n"
        "      tool: b\n"
    )
    with pytest.raises(PolicyError, match="duplicate rule name"):
        load_policy(write(tmp_path, text))


def test_rule_without_match_rejected(tmp_path):
    text = "name: x\nrules:\n  - name: r\n    decision: allow\n"
    with pytest.raises(PolicyError, match="'match' must be"):
        load_policy(write(tmp_path, text))


def test_empty_match_rejected(tmp_path):
    text = "name: x\nrules:\n  - name: r\n    decision: allow\n    match: {}\n"
    with pytest.raises(PolicyError, match="'match' must be"):
        load_policy(write(tmp_path, text))


def test_bad_time_window_rejected(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      time:\n        window: 9-5\n"
    )
    with pytest.raises(PolicyError, match="bad time window"):
        load_policy(write(tmp_path, text))


def test_time_needs_window_key(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      time:\n        hours: 9\n"
    )
    with pytest.raises(PolicyError, match="exactly a 'window'"):
        load_policy(write(tmp_path, text))


def test_bad_operator_rejected(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      args.amount: {sometimes: 5}\n"
    )
    with pytest.raises(PolicyError, match="unknown operators"):
        load_policy(write(tmp_path, text))


def test_in_needs_list(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      args.env: {in: prod}\n"
    )
    with pytest.raises(PolicyError, match="'in' needs a list"):
        load_policy(write(tmp_path, text))


def test_any_of_must_be_list(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n      any_of: {tool: a}\n"
    )
    with pytest.raises(PolicyError, match="'any_of' must be a non-empty list"):
        load_policy(write(tmp_path, text))


def test_any_of_groups_validated(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      any_of:\n        - {tool: a}\n        - {bogus: b}\n"
    )
    with pytest.raises(PolicyError, match="unknown match condition"):
        load_policy(write(tmp_path, text))


def test_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        load_policy(tmp_path / "nope.yaml")


def test_invalid_yaml_rejected(tmp_path):
    with pytest.raises(PolicyError, match="invalid YAML"):
        load_policy(write(tmp_path, "name: [unclosed\n"))


def test_non_mapping_rejected(tmp_path):
    with pytest.raises(PolicyError, match="must be a mapping"):
        load_policy(write(tmp_path, "- just\n- a\n- list\n"))


def test_not_block_accepted(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: 'api.*'\n      not:\n        tool: 'api.admin.*'\n"
    )
    policy = load_policy(write(tmp_path, text))
    assert policy.rules[0].match["not"] == {"tool": "api.admin.*"}


def test_not_block_must_be_mapping(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      not: 'api.admin.*'\n"
    )
    with pytest.raises(PolicyError, match="'not' must be a non-empty mapping"):
        load_policy(write(tmp_path, text))


def test_not_block_cannot_nest(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      not:\n        not:\n          tool: b\n"
    )
    with pytest.raises(PolicyError, match="cannot be nested"):
        load_policy(write(tmp_path, text))


def test_not_block_validates_inner_conditions(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      not:\n        bogus: b\n"
    )
    with pytest.raises(PolicyError, match="unknown match condition"):
        load_policy(write(tmp_path, text))


def test_time_days_accepted(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      time:\n        window: '09:00-17:00'\n"
        "        days: [mon, tue, wed, thu, fri]\n"
    )
    policy = load_policy(write(tmp_path, text))
    assert policy.rules[0].match["time"]["days"] == ["mon", "tue", "wed", "thu", "fri"]


def test_time_days_rejects_bad_day(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      time:\n        window: '09:00-17:00'\n"
        "        days: [funday]\n"
    )
    with pytest.raises(PolicyError, match="'time.days' must be"):
        load_policy(write(tmp_path, text))


def test_time_rejects_unknown_keys(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      time:\n        window: '09:00-17:00'\n"
        "        timezone: 'UTC'\n"
    )
    with pytest.raises(PolicyError, match="unknown keys"):
        load_policy(write(tmp_path, text))


def test_startswith_endswith_operators_accepted(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n"
        "      args.sql: {startswith: 'SELECT'}\n"
        "      args.filename: {endswith: '.csv'}\n"
    )
    policy = load_policy(write(tmp_path, text))
    assert policy.rules[0].match["args.sql"] == {"startswith": "SELECT"}


def test_startswith_needs_string(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      args.sql: {startswith: 5}\n"
    )
    with pytest.raises(PolicyError, match="'startswith' needs a string"):
        load_policy(write(tmp_path, text))


def test_length_operator_accepted(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n"
        "      args.filters: {length: {max: 5}}\n"
        "      args.code: {length: 3}\n"
    )
    policy = load_policy(write(tmp_path, text))
    assert policy.rules[0].match["args.filters"] == {"length": {"max": 5}}


def test_length_rejects_bad_bounds(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      args.filters: {length: {min: 5, max: 2}}\n"
    )
    with pytest.raises(PolicyError, match="min cannot exceed max"):
        load_policy(write(tmp_path, text))


def test_length_rejects_negative(tmp_path):
    text = (
        "name: x\nrules:\n  - name: r\n    decision: allow\n    match:\n"
        "      tool: a\n      args.filters: {length: -1}\n"
    )
    with pytest.raises(PolicyError, match="cannot be negative"):
        load_policy(write(tmp_path, text))


def test_org_baseline_policy_loads(tmp_path):
    from pathlib import Path as P

    examples = P(__file__).resolve().parent.parent / "examples" / "policies"
    policy = load_policy(examples / "org-baseline.yaml")
    assert policy.name == "org-baseline-policy"
    assert len(policy.rules) == 5
