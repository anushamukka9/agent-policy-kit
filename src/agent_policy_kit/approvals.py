"""Human-in-the-loop approval gates.

When a policy says ``approve``, the action is not allowed yet and not
denied either. It becomes an approval request that sits in a store
until a human approves or denies it, or the TTL expires. The store is a
plain JSON file, so it survives restarts and is easy to inspect.

A request looks like this:

    {
        "id": "apr-3f9a1c",
        "status": "pending",          # pending | approved | denied | expired
        "policy": "deploy-bot-policy",
        "rule": "prod-deploy-needs-approval",
        "action": {"agent": ..., "tool": ..., "args": ...},
        "requested_at": "2026-09-27T19:02:11+00:00",
        "expires_at": "2026-09-27T19:32:11+00:00",
        "resolved_at": null,
        "resolved_by": null,
        "note": null,
    }
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"


class ApprovalStore:
    """File-backed store of approval requests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "requests": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("requests", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self._read()["requests"].get(request_id)

    def all(self) -> list[dict[str, Any]]:
        data = self._read()
        return [data["requests"][k] for k in sorted(data["requests"])]


def _now_utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc)


def request_approval(
    store: ApprovalStore,
    action: dict[str, Any],
    policy_name: str,
    rule_name: str,
    ttl_minutes: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a pending approval request. Returns the request dict."""
    if ttl_minutes <= 0:
        raise ValueError("ttl_minutes must be positive")
    stamp = _now_utc(now)
    request_id = "apr-" + secrets.token_hex(3)
    request = {
        "id": request_id,
        "status": STATUS_PENDING,
        "policy": policy_name,
        "rule": rule_name,
        "action": action,
        "requested_at": stamp.isoformat(),
        "expires_at": (stamp + timedelta(minutes=ttl_minutes)).isoformat(),
        "resolved_at": None,
        "resolved_by": None,
        "note": None,
    }
    data = store._read()
    data["requests"][request_id] = request
    store._write(data)
    return request


def resolve_approval(
    store: ApprovalStore,
    request_id: str,
    approved: bool,
    by: str,
    note: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approve or deny a pending request. Only pending requests resolve."""
    data = store._read()
    request = data["requests"].get(request_id)
    if request is None:
        raise KeyError(f"unknown approval request '{request_id}'")
    if request["status"] != STATUS_PENDING:
        raise ValueError(f"request '{request_id}' is already {request['status']}, cannot resolve")
    stamp = _now_utc(now)
    if stamp >= datetime.fromisoformat(request["expires_at"]):
        request["status"] = STATUS_EXPIRED
        request["resolved_at"] = stamp.isoformat()
        data["requests"][request_id] = request
        store._write(data)
        raise ValueError(f"request '{request_id}' already expired")
    request["status"] = STATUS_APPROVED if approved else STATUS_DENIED
    request["resolved_at"] = stamp.isoformat()
    request["resolved_by"] = by
    request["note"] = note
    data["requests"][request_id] = request
    store._write(data)
    return request


def sweep_expired(store: ApprovalStore, now: datetime | None = None) -> list[dict[str, Any]]:
    """Mark overdue pending requests as expired. Returns the ones swept."""
    stamp = _now_utc(now)
    data = store._read()
    swept = []
    for request in data["requests"].values():
        if request["status"] == STATUS_PENDING and stamp >= datetime.fromisoformat(
            request["expires_at"]
        ):
            request["status"] = STATUS_EXPIRED
            request["resolved_at"] = stamp.isoformat()
            swept.append(request)
    if swept:
        store._write(data)
    return swept


def list_pending(store: ApprovalStore, now: datetime | None = None) -> list[dict[str, Any]]:
    """Pending requests, sweeping expired ones first so the list is honest."""
    sweep_expired(store, now)
    return [r for r in store.all() if r["status"] == STATUS_PENDING]
