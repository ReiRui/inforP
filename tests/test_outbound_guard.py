from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from inforp.delivery import (
    NO_OUTBOUND_ENV,
    EmailDelivery,
    WeComDelivery,
    WeComWeeklySummaryDelivery,
    outbound_disabled,
)
from inforp.schemas import Confidence, EventCandidate, Report, ReportKind, TokenUsage


def _report(kind=ReportKind.WEEKLY) -> Report:
    return Report(
        kind=kind,
        title="inforp 周报 - 2026-11-23",
        generated_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
        sections=[],
        markdown="# inforP 周报 - 2026-11-23\n\n1. **标题** [标签]\n   - 详细摘要：这是一条摘要。\n",
        token_usage=TokenUsage(),
        metadata={},
    )


def _settings():
    from inforp.config import Settings

    import os

    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
    return Settings(_env_file=None)


def test_outbound_disabled_reads_env(monkeypatch):
    monkeypatch.delenv(NO_OUTBOUND_ENV, raising=False)
    assert outbound_disabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(NO_OUTBOUND_ENV, value)
        assert outbound_disabled() is True
    monkeypatch.setenv(NO_OUTBOUND_ENV, "0")
    assert outbound_disabled() is False


def test_wecom_never_posts_when_outbound_disabled(monkeypatch):
    monkeypatch.setenv(NO_OUTBOUND_ENV, "1")
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append((a, k)))

    result = WeComDelivery("https://example.com/hook").send(_report())

    assert result.ok is True
    assert "skipped" in result.detail
    assert called == [], "no HTTP request may leave the process"


def test_wecom_weekly_summary_never_posts_when_outbound_disabled(monkeypatch):
    monkeypatch.setenv(NO_OUTBOUND_ENV, "1")
    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append((a, k)))

    result = WeComWeeklySummaryDelivery("https://example.com/hook", top_items=10).send(_report())

    assert result.ok is True
    assert "skipped" in result.detail
    assert called == []


def test_email_never_connects_when_outbound_disabled(monkeypatch):
    monkeypatch.setenv(NO_OUTBOUND_ENV, "1")
    import smtplib

    def explode(*args, **kwargs):
        raise AssertionError("SMTP must not be used during a dry run")

    monkeypatch.setattr(smtplib, "SMTP_SSL", explode)
    monkeypatch.setattr(smtplib, "SMTP", explode)

    settings = _settings()
    result = EmailDelivery(settings, recipients=["someone@example.com"]).send(_report())

    assert result.ok is True
    assert "skipped" in result.detail
