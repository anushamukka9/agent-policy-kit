"""Tests for the Rego and Cedar policy exporters.

The exporters produce text for other engines, and neither engine is a
test dependency, so these tests pin the generated text: package and
rule structure, operator mappings, ordering, and, most importantly,
that everything the target language cannot say shows up as a warning
instead of being silently dropped.
"""

from pathlib import Path

import pytest

from agent_policy_kit import (
    export_cedar,
    export_policy,
    export_rego,
    load_policy,
)
from agent_policy_kit.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "policies"


def load(name):
    return load_policy(EXAMPLES / name)


def write_policy(tmp_path, text):
    path = tmp_path / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return load_policy(path)


# ---------------------------------------------------------------------------
# Dispatch


def test_export_policy_dispatches():
    policy = load("support-bot.yaml")
    assert export_policy(policy, "rego").format == "rego"
    assert export_policy(policy, "cedar").format == "cedar"


def test_export_policy_unknown_format():
    policy = load("support-bot.yaml")
    with pytest.raises(ValueError, match="unknown export format"):
        export_policy(policy, "sentinel")


# ---------------------------------------------------------------------------
# Rego: structure and ordering


def test_rego_header_package_and_default():
    result = export_rego(load("support-bot.yaml"))
    assert "package agentpolicykit.support_bot_policy" in result.text
    assert 'default result := {"decision": "deny", "rule": null}' in result.text
    assert 'result := {"decision": rules[i].decision, "rule": rules[i].name}' in result.text
    assert "i := min(matched)" in result.text


def test_rego_preserves_rule_order_and_decisions():
    result = export_rego(load("support-bot.yaml"))
    names = [
        "kb-search",
        "read-own-ticket",
        "comment-own-ticket",
        "small-refund-needs-approval",
        "large-refund-denied",
        "no-user-management",
    ]
    positions = [result.text.index(f'"name": "{name}"') for name in names]
    assert positions == sorted(positions)
    assert '"name": "small-refund-needs-approval", "decision": "approve"' in result.text
    for idx in range(len(names)):
        assert f"matched contains {idx} if {{" in result.text


def test_rego_default_allow(tmp_path):
    policy = write_policy(
        tmp_path,
        """
name: open-policy
default: allow
rules:
  - name: block-admin
    decision: deny
    match:
      tool: "admin.*"
""",
    )
    result = export_rego(policy)
    assert 'default result := {"decision": "allow", "rule": null}' in result.text


def test_rego_no_rules_policy(tmp_path):
    policy = write_policy(tmp_path, "name: empty-policy\ndefault: deny\nrules: []\n")
    result = export_rego(policy)
    assert "matched contains" not in result.text
    assert 'result := {"decision": "deny", "rule": null}' in result.text


def test_rego_all_example_policies_export_without_warnings():
    for name in (
        "support-bot.yaml",
        "data-analyst.yaml",
        "deploy-bot.yaml",
        "org-baseline.yaml",
        "agent-frameworks.yaml",
    ):
        result = export_rego(load(name))
        assert result.warnings == [], name
        assert not result.lossy


# ---------------------------------------------------------------------------
# Rego: condition mappings


def test_rego_exact_glob_and_regex_tools():
    result = export_rego(load("data-analyst.yaml"))
    assert 'input.tool == "notebook.run"' in result.text
    support = export_rego(load("support-bot.yaml"))
    assert 'glob.match("users.*", null, input.tool)' in support.text
    assert 'regex.match("^(?:.*_pii$)$", input.args.table)' in result.text


def test_rego_numeric_and_membership_operators():
    result = export_rego(load("support-bot.yaml"))
    assert "input.args.amount <= 200" in result.text
    assert "input.args.amount > 200" in result.text


def test_rego_in_and_not_in(tmp_path):
    policy = write_policy(
        tmp_path,
        """
name: membership-policy
default: deny
rules:
  - name: known-regions-only
    decision: allow
    match:
      tool: files.read
      args.region: {in: [us, eu]}
  - name: never-sandbox
    decision: deny
    match:
      tool: files.write
      args.env: {not_in: [sandbox, dev]}
""",
    )
    result = export_rego(policy)
    assert 'input.args.region in {"us", "eu"}' in result.text
    assert 'input.args.env != "sandbox"' in result.text
    assert 'input.args.env != "dev"' in result.text


def test_rego_exists_true_and_false(tmp_path):
    analyst = export_rego(load("data-analyst.yaml"))
    assert '"table" in object.keys(input.args)' in analyst.text
    policy = write_policy(
        tmp_path,
        """
name: exists-policy
default: deny
rules:
  - name: no-attachment
    decision: allow
    match:
      tool: mail.send
      args.attachment: {exists: false}
""",
    )
    result = export_rego(policy)
    assert 'not "attachment" in object.keys(input.args)' in result.text


def test_rego_placeholder_becomes_input_reference():
    result = export_rego(load("support-bot.yaml"))
    assert "input.args.assignee == input.agent" in result.text


def test_rego_contains_startswith_endswith_and_length():
    result = export_rego(load("org-baseline.yaml"))
    assert 'startswith(input.args.sql, "SELECT")' in result.text
    assert 'endswith(input.args.filename, ".csv")' in result.text
    assert "count(input.args.filters) <= 5" in result.text


def test_rego_any_of_makes_multiple_bodies(tmp_path):
    policy = write_policy(
        tmp_path,
        """
name: either-policy
default: deny
rules:
  - name: read-either-way
    decision: allow
    match:
      any_of:
        - tool: docs.read
        - tool: kb.search
""",
    )
    result = export_rego(policy)
    assert result.text.count("matched contains 0 if {") == 2
    assert 'input.tool == "docs.read"' in result.text
    assert 'input.tool == "kb.search"' in result.text


def test_rego_not_block_becomes_helper():
    result = export_rego(load("org-baseline.yaml"))
    assert "not _not_1" in result.text
    assert "_not_1 if {" in result.text
    assert 'glob.match("api.admin.*", null, input.tool)' in result.text


def test_rego_time_window_and_days():
    deploy = export_rego(load("deploy-bot.yaml"))
    assert "_in_window(540, 1020)" in deploy.text
    assert "time.clock([time.now_ns()" in deploy.text
    baseline = export_rego(load("org-baseline.yaml"))
    assert '_day_in({"mon", "tue", "wed", "thu", "fri"})' in baseline.text


def test_rego_escapes_quotes_in_strings(tmp_path):
    policy = write_policy(
        tmp_path,
        """
name: quote-policy
default: deny
rules:
  - name: odd-tool
    decision: allow
    match:
      tool: 'say "hi"'
""",
    )
    result = export_rego(policy)
    assert 'input.tool == "say \\"hi\\""' in result.text


# ---------------------------------------------------------------------------
# Cedar: structure, mapping, and honest losses


def test_cedar_allow_and_deny_effects():
    result = export_cedar(load("support-bot.yaml"))
    assert '@id("support-bot-policy.kb-search")' in result.text
    assert "permit (principal, action, resource) when {" in result.text
    assert 'context.tool == "kb.search"' in result.text
    assert '@id("support-bot-policy.no-user-management")' in result.text
    assert 'context.tool like "users.*"' in result.text


def test_cedar_approve_becomes_forbid_with_warning():
    result = export_cedar(load("support-bot.yaml"))
    assert '@id("support-bot-policy.small-refund-needs-approval")' in result.text
    block = result.text.split("small-refund-needs-approval")[1]
    assert block.lstrip().startswith('")\nforbid')
    assert any(
        "small-refund-needs-approval" in w and "no approve decision" in w for w in result.warnings
    )
    assert result.lossy


def test_cedar_warns_that_forbid_beats_order():
    result = export_cedar(load("support-bot.yaml"))
    assert any("no rule order" in w for w in result.warnings)


def test_cedar_default_deny_needs_no_catch_all():
    result = export_cedar(load("support-bot.yaml"))
    assert "permit (principal, action, resource);" not in result.text
    assert "Policy default is deny" in result.text


def test_cedar_default_allow_gets_catch_all_permit(tmp_path):
    policy = write_policy(
        tmp_path,
        """
name: open-policy
default: allow
rules:
  - name: block-admin
    decision: deny
    match:
      tool: "admin.*"
""",
    )
    result = export_cedar(policy)
    assert "permit (principal, action, resource);" in result.text
    assert any("default is allow" in w for w in result.warnings)


def test_cedar_regex_rule_is_left_out_with_warning():
    result = export_cedar(load("data-analyst.yaml"))
    assert "no-pii-tables" not in result.text.split("NOT EXPORTED")[0]
    assert "NOT EXPORTED: rule 'no-pii-tables'" in result.text
    assert any("no-pii-tables" in w and "NOT exported" in w for w in result.warnings)
    # The other rules still export fine.
    assert '@id("data-analyst-policy.notebook-runs")' in result.text
    assert "context.args.row_limit <= 100000" in result.text


def test_cedar_time_rule_is_left_out_with_warning():
    result = export_cedar(load("deploy-bot.yaml"))
    assert "NOT EXPORTED: rule 'prod-deploy-needs-approval-in-hours'" in result.text
    assert any("time window" in w for w in result.warnings)
    # The staging approve rule has no clock and still exports, as forbid.
    assert '@id("deploy-bot-policy.staging-deploy-needs-approval")' in result.text


def test_cedar_placeholder_and_not_and_length_loss():
    support = export_cedar(load("support-bot.yaml"))
    assert "context.args.assignee == context.agent" in support.text
    baseline = export_cedar(load("org-baseline.yaml"))
    assert '!(context.tool like "api.admin.*")' in baseline.text
    assert 'context.args.filename like "*.csv"' in baseline.text
    # The length operator has no Cedar equivalent, so the rule that
    # uses it is left out with a warning instead of a wrong policy.
    assert "NOT EXPORTED: rule 'safe-select-queries'" in baseline.text
    assert any("safe-select-queries" in w and "length" in w for w in baseline.warnings)


def test_cedar_args_access_is_guarded_by_has():
    result = export_cedar(load("support-bot.yaml"))
    assert '(context.args has "amount" && context.args.amount <= 200)' in result.text


def test_cedar_exists_uses_has(tmp_path):
    result = export_cedar(load("data-analyst.yaml"))
    assert 'context.args has "table"' in result.text


# ---------------------------------------------------------------------------
# CLI


def test_cli_export_rego_to_stdout(tmp_path, capsys):
    code = main(["export", "--policy", str(EXAMPLES / "support-bot.yaml")])
    assert code == 0
    out = capsys.readouterr().out
    assert "package agentpolicykit.support_bot_policy" in out


def test_cli_export_cedar_to_file_with_warnings(tmp_path, capsys):
    target = tmp_path / "analyst.cedar"
    code = main(
        [
            "export",
            "--policy",
            str(EXAMPLES / "data-analyst.yaml"),
            "--format",
            "cedar",
            "--output",
            str(target),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "wrote cedar export of data-analyst-policy" in captured.out
    assert "no-pii-tables" in captured.err
    assert "NOT EXPORTED" in target.read_text(encoding="utf-8")


def test_cli_export_bad_policy_returns_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ndefault: sometimes\n", encoding="utf-8")
    code = main(["export", "--policy", str(bad)])
    assert code == 2
    assert "policy-kit:" in capsys.readouterr().err
