from __future__ import annotations

import json

from inforp.config import Settings
from inforp.incidents import build_incident, record_incident
from inforp.schemas import IncidentSeverity, IncidentStatus
from inforp.store import Store


def test_record_incident_writes_jsonl_and_database(tmp_path):
    log_path = tmp_path / "logs" / "automation_incidents.jsonl"
    db_path = tmp_path / "inforp.db"
    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
        INCIDENT_LOG_FILE=log_path,
        _env_file=None,
    )
    incident = build_incident(
        title="自动化线程失败",
        detail="Invalid Value: input.call_id",
        component="codex.automation",
        severity=IncidentSeverity.ERROR,
        status=IncidentStatus.MITIGATED,
        detected_by="test",
        related_files=["reports/2026-09-01_daily_remote.md"],
        context={"thread_id": "01test"},
    )

    record_incident(settings, incident)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["title"] == "自动化线程失败"
    assert payload["severity"] == "error"
    assert payload["status"] == "mitigated"
    assert payload["context"]["thread_id"] == "01test"

    store = Store(settings.database_url)
    store.init_db()
    rows = store.latest_automation_incidents(limit=5)
    assert len(rows) == 1
    assert rows[0].incident_id == incident.incident_id
    assert rows[0].title == "自动化线程失败"
