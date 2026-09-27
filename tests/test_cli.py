import json
from pathlib import Path

from agent_policy_kit.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "policies"


def write_action(tmp_path, action):
    path = tmp_path / "action.json"
    path.write_text(json.dumps(action), encoding="utf-8")
    return path


def check(tmp_path, policy_name, action, extra=(), capsys=None):
    args = [
        "check",
        "--policy",
        str(EXAMPLES / policy_name),
        "--action",
        str(write_action(tmp_path, action)),
        *extra,
    ]
    return main(args)


def test_check_allow_exit_0(tmp_path, capsys):
    code = check(
        tmp_path, "support-bot.yaml", {"agent": "support-bot", "tool": "kb.search", "args": {}}
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "decision: allow" in out


def test_check_deny_exit_2(tmp_path, capsys):
    code = check(
        tmp_path, "support-bot.yaml", {"agent": "support-bot", "tool": "users.delete", "args": {}}
    )
    assert code == 2
    assert "decision: deny" in capsys.readouterr().out


def test_check_approve_exit_3_and_records_request(tmp_path, capsys):
    approvals = tmp_path / "approvals.json"
    code = check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 50}},
        extra=["--approvals", str(approvals)],
    )
    assert code == 3
    out = capsys.readouterr().out
    assert "decision: approve" in out
    assert "apr-" in out
    stored = json.loads(approvals.read_text(encoding="utf-8"))
    assert len(stored["requests"]) == 1


def test_check_approve_without_store_still_exit_3(tmp_path, capsys):
    code = check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 50}},
    )
    assert code == 3
    assert "no approval store configured" in capsys.readouterr().out


def test_check_json_format(tmp_path, capsys):
    code = check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "kb.search", "args": {}},
        extra=["--format", "json"],
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "allow"
    assert payload["rule"] == "kb-search"


def test_check_bad_policy_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ndefault: sometimes\n", encoding="utf-8")
    code = main(["check", "--policy", str(bad), "--action", str(write_action(tmp_path, {}))])
    assert code == 2
    assert "policy-kit:" in capsys.readouterr().err


def test_check_writes_audit(tmp_path):
    audit = tmp_path / "audit.log"
    code = check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "kb.search", "args": {}},
        extra=["--audit", str(audit)],
    )
    assert code == 0
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1


def _approve_request(tmp_path):
    approvals = tmp_path / "approvals.json"
    check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "refund.issue", "args": {"amount": 50}},
        extra=["--approvals", str(approvals)],
    )
    stored = json.loads(approvals.read_text(encoding="utf-8"))
    request_id = next(iter(stored["requests"]))
    return approvals, request_id


def test_pending_lists_request(tmp_path, capsys):
    approvals, request_id = _approve_request(tmp_path)
    code = main(["pending", "--approvals", str(approvals)])
    assert code == 0
    assert request_id in capsys.readouterr().out


def test_pending_empty(tmp_path, capsys):
    code = main(["pending", "--approvals", str(tmp_path / "a.json")])
    assert code == 0
    assert "no pending" in capsys.readouterr().out


def test_approve_then_pending_empty(tmp_path, capsys):
    approvals, request_id = _approve_request(tmp_path)
    audit = tmp_path / "audit.log"
    code = main(
        [
            "approve",
            request_id,
            "--approvals",
            str(approvals),
            "--by",
            "anusha",
            "--audit",
            str(audit),
        ]
    )
    assert code == 0
    assert "approved by anusha" in capsys.readouterr().out
    main(["pending", "--approvals", str(approvals)])
    assert "no pending" in capsys.readouterr().out
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1


def test_deny_request(tmp_path, capsys):
    approvals, request_id = _approve_request(tmp_path)
    code = main(
        [
            "deny",
            request_id,
            "--approvals",
            str(approvals),
            "--by",
            "on-call",
            "--note",
            "too risky",
        ]
    )
    assert code == 0
    assert "denied by on-call" in capsys.readouterr().out


def test_resolve_unknown_id_exit_2(tmp_path, capsys):
    code = main(["approve", "apr-nope", "--approvals", str(tmp_path / "a.json"), "--by", "x"])
    assert code == 2
    assert "unknown approval request" in capsys.readouterr().err


def test_audit_command(tmp_path, capsys):
    audit = tmp_path / "audit.log"
    check(
        tmp_path,
        "support-bot.yaml",
        {"agent": "support-bot", "tool": "kb.search", "args": {}},
        extra=["--audit", str(audit)],
    )
    code = main(["audit", "--audit", str(audit), "--tail", "5"])
    assert code == 0
    out = capsys.readouterr().out
    assert "decision" in out and "allow" in out


def test_audit_empty(tmp_path, capsys):
    code = main(["audit", "--audit", str(tmp_path / "empty.log")])
    assert code == 0
    assert "empty" in capsys.readouterr().out
