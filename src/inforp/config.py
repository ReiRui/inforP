from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from inforp.schemas import SourceProfile, SearchQuery


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")

    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-flash", alias="DEEPSEEK_MODEL")
    deepseek_verifier_model: str = Field(default="deepseek-flash", alias="DEEPSEEK_VERIFIER_MODEL")
    deepseek_timeout_seconds: float = Field(default=300.0, alias="DEEPSEEK_TIMEOUT_SECONDS")

    deepseek_search_count: int = Field(default=10, alias="DEEPSEEK_SEARCH_COUNT")
    deepseek_search_per_query: int = Field(default=3, alias="DEEPSEEK_SEARCH_PER_QUERY")
    deepseek_search_need_content: bool = Field(default=False, alias="DEEPSEEK_SEARCH_NEED_CONTENT")
    deepseek_search_concurrency: int = Field(default=4, alias="DEEPSEEK_SEARCH_CONCURRENCY")
    deepseek_search_date_filter: bool = Field(default=True, alias="DEEPSEEK_SEARCH_DATE_FILTER")
    deepseek_results_per_query: int = Field(default=10, alias="DEEPSEEK_RESULTS_PER_QUERY")
    deepseek_verify_search_top_n: int = Field(default=6, alias="DEEPSEEK_VERIFY_SEARCH_TOP_N")
    deepseek_max_raw_items_per_call: int = Field(default=120, alias="DEEPSEEK_MAX_RAW_ITEMS_PER_CALL")
    deepseek_raw_item_chars: int = Field(default=2500, alias="DEEPSEEK_RAW_ITEM_CHARS")

    timezone: str = Field(default="Asia/Shanghai", alias="TIMEZONE")
    database_url: str = Field(default="sqlite:///data/inforp.db", alias="DATABASE_URL")
    report_output_dir: Path = Field(default=Path("reports"), alias="REPORT_OUTPUT_DIR")
    sources_file: Path = Field(default=Path("configs/sources.yaml"), alias="SOURCES_FILE")
    recipients_file: Path = Field(default=Path("configs/recipients.yaml"), alias="RECIPIENTS_FILE")
    incident_log_file: Path = Field(default=Path("logs/automation_incidents.jsonl"), alias="INCIDENT_LOG_FILE")
    max_daily_read_minutes: int = Field(default=5, alias="MAX_DAILY_READ_MINUTES")
    max_report_events: int = Field(default=15, alias="MAX_REPORT_EVENTS")
    max_weekly_report_events: int = Field(default=25, alias="MAX_WEEKLY_REPORT_EVENTS")
    max_watchlist_events: int = Field(default=8, alias="MAX_WATCHLIST_EVENTS")
    watchlist_promote_importance: float = Field(default=0.85, alias="WATCHLIST_PROMOTE_IMPORTANCE")
    collect_concurrency: int = Field(default=8, alias="COLLECT_CONCURRENCY")
    collect_lookback_hours: int = Field(default=48, alias="COLLECT_LOOKBACK_HOURS")
    collect_max_items_per_source: int = Field(default=20, alias="COLLECT_MAX_ITEMS_PER_SOURCE")

    delivery_channels: str = Field(default="codex_file", alias="DELIVERY_CHANNELS")
    wecom_webhook_url: str | None = Field(default=None, alias="WECOM_WEBHOOK_URL")
    wecom_weekly_summary_enabled: bool = Field(default=False, alias="WECOM_WEEKLY_SUMMARY_ENABLED")
    wecom_weekly_summary_top_items: int = Field(default=10, alias="WECOM_WEEKLY_SUMMARY_TOP_ITEMS")

    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    imap_host: str | None = Field(default=None, alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_user: str | None = Field(default=None, alias="IMAP_USER")
    imap_password: str | None = Field(default=None, alias="IMAP_PASSWORD")
    imap_sent_copy_enabled: bool = Field(default=True, alias="IMAP_SENT_COPY_ENABLED")
    imap_sent_mailbox: str | None = Field(default=None, alias="IMAP_SENT_MAILBOX")
    email_from: str | None = Field(default=None, alias="EMAIL_FROM")
    email_to: str | None = Field(default=None, alias="EMAIL_TO")
    email_recipient_set: str | None = Field(default=None, alias="EMAIL_RECIPIENT_SET")

    enable_llm: bool = Field(default=True, alias="ENABLE_LLM")
    enable_browser_connectors: bool = Field(default=False, alias="ENABLE_BROWSER_CONNECTORS")
    token_budget_daily: int | None = Field(default=None, alias="TOKEN_BUDGET_DAILY")
    token_budget_weekly: int | None = Field(default=None, alias="TOKEN_BUDGET_WEEKLY")

    @property
    def channel_list(self) -> list[str]:
        return [item.strip() for item in self.delivery_channels.split(",") if item.strip()]


class SourceConfig:
    def __init__(self, path: Path):
        self.path = self._resolve_path(path)
        self.raw = self._read_yaml(self.path)
        self.sources = [SourceProfile.model_validate(item) for item in self.raw.get("sources", [])]
        llm_search = self.raw.get("llm_search", {})
        self.llm_enabled = bool(llm_search.get("enabled", True))
        self.daily_queries = [SearchQuery.model_validate(item) for item in llm_search.get("daily_queries", [])]
        self.weekly_queries = [SearchQuery.model_validate(item) for item in llm_search.get("weekly_queries", [])]

    @staticmethod
    def _resolve_path(path: Path) -> Path:
        if path.exists():
            return path
        example_path = path.with_name(f"{path.stem}.example{path.suffix}")
        if example_path.exists():
            return example_path
        return path

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Source config not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Source config must be a mapping: {path}")
        return payload


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_source_config(path: Path | None = None) -> SourceConfig:
    settings = get_settings()
    return SourceConfig(path or settings.sources_file)
