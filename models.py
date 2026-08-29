"""Pydantic schemas and audit-log persistence for the churn-risk HITL workflow."""

import json
from pathlib import Path

from pydantic import BaseModel

AUDIT_LOG_PATH = Path(__file__).parent / "audit_log.json"


class AuditEntry(BaseModel):
    timestamp: str
    agent_id: str
    action: str
    confidence: float
    reviewer_id: str
    decision: str


def read_audit_log() -> list[dict]:
    """Read the full audit trail. Returns [] if the file is missing/empty."""
    if not AUDIT_LOG_PATH.exists():
        return []
    text = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return json.loads(text)


def append_audit_entry(entry: AuditEntry) -> None:
    """Append one audit entry to audit_log.json without overwriting history."""
    entries = read_audit_log()
    entries.append(entry.model_dump())
    AUDIT_LOG_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )
