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
