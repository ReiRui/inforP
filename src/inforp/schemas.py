from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TopicType(StrEnum):
    AI_TECH = "ai_tech"
    AI_HARDWARE = "ai_hardware"
    AI_INDUSTRY_POLICY = "ai_industry_policy"
    GLOBAL_POLITICS = "global_politics"
    ECONOMY = "economy"
    CHINA_POLICY = "china_policy"
    GENERAL_AI = "general_ai"
    MARKET_WATCH = "market_watch"
    OTHER = "other"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceKind(StrEnum):
    OFFICIAL = "official"
    MEDIA = "media"
    SOCIAL = "social"
    FORUM = "forum"
    PAPER = "paper"
    VIDEO = "video"
    NEWSLETTER = "newsletter"
    SEARCH = "search"
    OTHER = "other"


class CrawlMode(StrEnum):
    RSS = "rss"
    WEB = "web"
    LLM_SEARCH = "llm_search"
    BROWSER_PROFILE = "browser_profile"
    YOUTUBE = "youtube"
    MANUAL_LINK = "manual_link"


class ReportKind(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class IncidentSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


class SourceProfile(BaseModel):
    id: str
    name: str
    url: str | None = None
    kind: SourceKind = SourceKind.OTHER
    tier: str | None = None
    topic_hints: list[TopicType] = Field(default_factory=list)
    reliability_score: float = Field(default=0.5, ge=0.0, le=1.0)
    crawl_mode: CrawlMode = CrawlMode.WEB
    observe_enabled: bool = True
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchQuery(BaseModel):
    id: str
    topic: TopicType
    query: str


class RawItem(BaseModel):
    title: str
    url: str | None = None
    source_id: str
    source_name: str
    source_kind: SourceKind = SourceKind.OTHER
    topic_hint: TopicType | None = None
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    text: str
    support_urls: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class EventCandidate(BaseModel):
    title: str
    summary: str
    claims: list[str] = Field(default_factory=list)
    topic: TopicType = TopicType.OTHER
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: Confidence = Confidence.MEDIUM
    source_urls: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    verification_notes: str = ""
    contradictions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_watchlist: bool = False
    raw_item_ids: list[int] = Field(default_factory=list)


class LLMEventBatch(BaseModel):
    events: list[EventCandidate] = Field(default_factory=list)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ReportSection(BaseModel):
    title: str
    events: list[EventCandidate] = Field(default_factory=list)
    body: str | None = None


class Report(BaseModel):
    kind: ReportKind
    title: str
    generated_at: datetime
    sections: list[ReportSection] = Field(default_factory=list)
    markdown: str
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryResult(BaseModel):
    channel: str
    ok: bool
    detail: str = ""


class RunMetrics(BaseModel):
    run_id: str
    kind: ReportKind
    started_at: datetime
    finished_at: datetime | None = None
    raw_items: int = 0
    events_total: int = 0
    events_pushed: int = 0
    low_confidence_ai_filtered: int = 0
    watchlist_events: int = 0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    delivery_results: list[DeliveryResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AutomationIncident(BaseModel):
    incident_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: IncidentSeverity = IncidentSeverity.ERROR
    status: IncidentStatus = IncidentStatus.OPEN
    component: str = "automation"
    title: str
    detail: str
    detected_by: str = "inforp"
    resolved_at: datetime | None = None
    resolution: str | None = None
    related_files: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class SourceScore(BaseModel):
    source_id: str
    base_reliability: float = Field(default=0.5, ge=0.0, le=1.0)
    mentions: int = 0
    confirmed: int = 0
    corrected: int = 0
    false_reports: int = 0
    avg_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    score: float = Field(default=0.5, ge=0.0, le=1.0)
