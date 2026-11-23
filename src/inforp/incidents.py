from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from inforp.config import Settings
from inforp.schemas import AutomationIncident, IncidentSeverity, IncidentStatus
from inforp.store import Store


def build_incident(
    *,
    title: str,
    detail: str,
    component: str = "automation",
    severity: IncidentSeverity | str = IncidentSeverity.ERROR,
    status: IncidentStatus | str = IncidentStatus.OPEN,
    detected_by: str = "inforp",
    occurred_at: datetime | None = None,
    incident_id: str | None = None,
    related_files: list[str] | None = None,
    context: dict | None = None,
    resolved_at: datetime | None = None,
    resolution: str | None = None,
) -> AutomationIncident:
    return AutomationIncident(
        incident_id=incident_id or f"inc-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}",
        occurred_at=occurred_at or datetime.now(timezone.utc),
        severity=IncidentSeverity(severity),
        status=IncidentStatus(status),
        component=component,
        title=title,
        detail=detail,
        detected_by=detected_by,
        resolved_at=resolved_at,
        resolution=resolution,
        related_files=related_files or [],
        context=context or {},
    )


def record_incident(settings: Settings, incident: AutomationIncident) -> AutomationIncident:
    _append_jsonl(settings.incident_log_file, incident)
    store = Store(settings.database_url)
    store.init_db()
    store.save_automation_incident(incident)
    return incident


def _append_jsonl(path: Path, incident: AutomationIncident) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = incident.model_dump(mode="json")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
