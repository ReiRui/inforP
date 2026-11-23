from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from inforp.schemas import AutomationIncident, EventCandidate, RawItem, Report, RunMetrics, SourceProfile, SourceScore


class Base(DeclarativeBase):
    pass


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    crawl_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.5)
    observe_enabled: Mapped[bool] = mapped_column(default=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class RawItemRecord(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_name: Mapped[str] = mapped_column(String(256))
    source_kind: Mapped[str] = mapped_column(String(64))
    topic_hint: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[str] = mapped_column(String(32), index=True)
    importance_score: Mapped[float] = mapped_column(Float, index=True)
    is_watchlist: Mapped[bool] = mapped_column(default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ReportRecord(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class RunMetricRecord(Base):
    __tablename__ = "run_metrics"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_items: Mapped[int] = mapped_column(Integer, default=0)
    events_total: Mapped[int] = mapped_column(Integer, default=0)
    events_pushed: Mapped[int] = mapped_column(Integer, default=0)
    low_confidence_ai_filtered: Mapped[int] = mapped_column(Integer, default=0)
    watchlist_events: Mapped[int] = mapped_column(Integer, default=0)
    token_input: Mapped[int] = mapped_column(Integer, default=0)
    token_output: Mapped[int] = mapped_column(Integer, default=0)
    token_total: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AutomationIncidentRecord(Base):
    __tablename__ = "automation_incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    component: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    detected_by: Mapped[str] = mapped_column(String(128), default="inforp")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SourceMetricRecord(Base):
    __tablename__ = "source_metrics"

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    base_reliability: Mapped[float] = mapped_column(Float, default=0.5)
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[int] = mapped_column(Integer, default=0)
    corrected: Mapped[int] = mapped_column(Integer, default=0)
    false_reports: Mapped[int] = mapped_column(Integer, default=0)
    avg_importance: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.5)


class Store:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._ensure_sqlite_parent(database_url)
        self.engine = create_engine(database_url, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @staticmethod
    def _ensure_sqlite_parent(database_url: str) -> None:
        if not database_url.startswith("sqlite:///"):
            return
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if db_path.parent != Path("."):
            db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.SessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def upsert_sources(self, sources: list[SourceProfile]) -> None:
        with self.session() as db:
            for source in sources:
                record = db.get(SourceRecord, source.id)
                payload = source.model_dump(mode="json")
                if record is None:
                    record = SourceRecord(id=source.id)
                    db.add(record)
                record.name = source.name
                record.url = source.url
                record.kind = source.kind.value
                record.crawl_mode = source.crawl_mode.value
                record.reliability_score = source.reliability_score
                record.observe_enabled = source.observe_enabled
                record.payload = payload

    def save_raw_items(self, items: list[RawItem]) -> list[int]:
        ids: list[int] = []
        with self.session() as db:
            for item in items:
                record = RawItemRecord(
                    title=item.title,
                    url=item.url,
                    source_id=item.source_id,
                    source_name=item.source_name,
                    source_kind=item.source_kind.value,
                    topic_hint=item.topic_hint.value if item.topic_hint else None,
                    published_at=item.published_at,
                    collected_at=item.collected_at,
                    content=item.content,
                    payload=item.model_dump(mode="json"),
                )
                db.add(record)
                db.flush()
                ids.append(record.id)
        return ids

    def save_events(self, events: list[EventCandidate], created_at: datetime) -> list[int]:
        ids: list[int] = []
        with self.session() as db:
            for event in events:
                record = EventRecord(
                    title=event.title,
                    topic=event.topic.value,
                    confidence=event.confidence.value,
                    importance_score=event.importance_score,
                    is_watchlist=event.is_watchlist,
                    published_at=event.published_at,
                    created_at=created_at,
                    payload=event.model_dump(mode="json"),
                )
                db.add(record)
                db.flush()
                ids.append(record.id)
        return ids

    def save_report(self, report: Report) -> int:
        with self.session() as db:
            record = ReportRecord(
                kind=report.kind.value,
                title=report.title,
                generated_at=report.generated_at,
                markdown=report.markdown,
                payload=report.model_dump(mode="json"),
            )
            db.add(record)
            db.flush()
            return record.id

    def save_run_metrics(self, metrics: RunMetrics) -> None:
        usage = metrics.token_usage
        with self.session() as db:
            record = RunMetricRecord(
                run_id=metrics.run_id,
                kind=metrics.kind.value,
                started_at=metrics.started_at,
                finished_at=metrics.finished_at,
                raw_items=metrics.raw_items,
                events_total=metrics.events_total,
                events_pushed=metrics.events_pushed,
                low_confidence_ai_filtered=metrics.low_confidence_ai_filtered,
                watchlist_events=metrics.watchlist_events,
                token_input=usage.input_tokens,
                token_output=usage.output_tokens,
                token_total=usage.total_tokens,
                payload=metrics.model_dump(mode="json"),
            )
            db.merge(record)

    def save_automation_incident(self, incident: AutomationIncident) -> None:
        with self.session() as db:
            record = AutomationIncidentRecord(
                incident_id=incident.incident_id,
                occurred_at=incident.occurred_at,
                severity=incident.severity.value,
                status=incident.status.value,
                component=incident.component,
                title=incident.title,
                detail=incident.detail,
                detected_by=incident.detected_by,
                resolved_at=incident.resolved_at,
                resolution=incident.resolution,
                payload=incident.model_dump(mode="json"),
            )
            db.merge(record)

    def latest_automation_incidents(self, limit: int = 10) -> list[AutomationIncidentRecord]:
        with self.session() as db:
            stmt = select(AutomationIncidentRecord).order_by(AutomationIncidentRecord.occurred_at.desc()).limit(limit)
            return list(db.scalars(stmt))

    def upsert_source_scores(self, scores: list[SourceScore]) -> None:
        with self.session() as db:
            for score in scores:
                record = db.get(SourceMetricRecord, score.source_id)
                if record is None:
                    record = SourceMetricRecord(source_id=score.source_id)
                    db.add(record)
                record.base_reliability = score.base_reliability
                record.mentions = score.mentions
                record.confirmed = score.confirmed
                record.corrected = score.corrected
                record.false_reports = score.false_reports
                record.avg_importance = score.avg_importance
                record.score = score.score

    def latest_run_metrics(self, limit: int = 10) -> list[RunMetricRecord]:
        with self.session() as db:
            stmt = select(RunMetricRecord).order_by(RunMetricRecord.started_at.desc()).limit(limit)
            return list(db.scalars(stmt))
