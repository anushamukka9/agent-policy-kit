"""Append-only JSONL decision log.

Every evaluation writes one line: when it happened, which policy and
rule decided, what the decision was, and the action that was judged.
Approval resolutions are logged too. The log is the audit trail you
show an incident responder or an auditor. See docs/audit-log.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: str | Path, record: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def log_decision(
    audit_path: str | Path,
    decision: Any,
    action: dict[str, Any],
    approval_request_id: str | None = None,
) -> None:
    """Log one policy evaluation."""
    _append(
        audit_path,
        {
            "ts": _utcnow(),
            "kind": "decision",
            "policy": decision.policy,
            "rule": decision.rule,
            "decision": decision.decision,
            "reason": decision.reason,
            "approval_request_id": approval_request_id,
            "action": action,
        },
    )


def log_resolution(audit_path: str | Path, request: dict[str, Any]) -> None:
    """Log a human's approve/deny on an approval request."""
    _append(
        audit_path,
        {
            "ts": _utcnow(),
            "kind": "resolution",
            "policy": request["policy"],
            "rule": request["rule"],
            "decision": request["status"],
            "reason": f"request {request['id']} {request['status']} by {request['resolved_by']}",
            "approval_request_id": request["id"],
            "action": request["action"],
        },
    )


def read_tail(audit_path: str | Path, n: int = 20) -> list[dict[str, Any]]:
    """Return the last n records, oldest first."""
    path = Path(audit_path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-n:]:
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
