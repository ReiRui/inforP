from __future__ import annotations

import httpx
import pytest

from inforp.collectors.rss import RSSCollector
from inforp.schemas import (
    Confidence,
    EventCandidate,
    Report,
    ReportKind,
    SourceKind,
    SourceProfile,
    TokenUsage,
    TopicType,
)
from inforp.verification import (
    attach_source_signals,
    best_tier,
    independent_sources,
    normalize_domain,
    promote_important_watchlist,
    rank_events,
    select_report_events,
)

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example feed</title>
<item><title>First item</title><link>https://example.com/a</link>
<pubDate>Mon, 20 Nov 2026 08:00:00 GMT</pubDate><description>Body A</description></item>
<item><title>Second item</title><link>https://example.com/b</link>
<pubDate>Tue, 21 Nov 2026 08:00:00 GMT</pubDate><description>Body B</description></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def source(**kwargs):
    base = dict(id="s", name="S", url="https://example.com/feed", kind=SourceKind.OFFICIAL, tier="T1")
    base.update(kwargs)
    return SourceProfile(**base)


def test_rss_collector_fetches_with_httpx_and_parses_entries(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(RSS_XML)

    monkeypatch.setattr(httpx, "get", fake_get)
    items = RSSCollector().collect(source(), context=None)

    assert calls and calls[0][0] == "https://example.com/feed"
    assert len(items) == 2
    assert items[0].title == "First item"
    assert items[0].url == "https://example.com/a"
    assert items[0].published_at is not None
    assert items[0].metadata["collector"] == "rss"


def test_rss_collector_reports_transport_failure_as_error_item(monkeypatch):
    def fake_get(url, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(httpx, "get", fake_get)
    items = RSSCollector().collect(source(), context=None)

    assert len(items) == 1
    assert items[0].metadata["error"] is True


def test_normalize_domain_strips_www_and_port():
    assert normalize_domain("https://www.Example.com:443/path") == "example.com"
    assert normalize_domain(None) is None


def test_independent_sources_counts_domains_not_urls():
    event = EventCandidate(
        title="t", summary="s",
        source_urls=[
            "https://www.nvidia.com/a", "https://nvidia.com/b",  # same publisher
            "https://reuters.com/x",
        ],
    )
    assert independent_sources(event) == 2


def test_best_tier_picks_strongest_known_source():
    event = EventCandidate(title="t", summary="s",
                           source_urls=["https://reuters.com/x", "https://nvidianews.nvidia.com/y"])
    tiers = {"reuters.com": "T2", "nvidianews.nvidia.com": "T1"}
    assert best_tier(event, tiers) == "T1"


def test_single_media_source_high_confidence_is_demoted():
    event = EventCandidate(
        title="t", summary="s", confidence=Confidence.HIGH,
        source_urls=["https://someblog.com/post"],
    )
    attach_source_signals([event], {"someblog.com": "T2"})

    assert event.confidence == Confidence.MEDIUM
    assert "单一来源" in event.tags
    assert "high 下调为 medium" in event.verification_notes


def test_multi_source_event_gets_confirmation_tag():
    event = EventCandidate(
        title="t", summary="s",
        source_urls=["https://a.com/1", "https://b.com/2", "https://c.com/3"],
    )
    attach_source_signals([event], {})

    assert "多源确认" in event.tags


def test_rank_prefers_multi_source_over_single_source():
    single = EventCandidate(title="single", summary="s", importance_score=0.6,
                            source_urls=["https://a.com/1"])
    multi = EventCandidate(title="multi", summary="s", importance_score=0.6,
                           source_urls=["https://a.com/1", "https://b.com/2", "https://c.com/3"])

    ranked = rank_events([single, multi])
    assert ranked[0].title == "multi"


def test_rank_prefers_official_source_on_equal_scores():
    official = EventCandidate(title="official", summary="s", importance_score=0.6,
                              source_urls=["https://nvidianews.nvidia.com/x"])
    media = EventCandidate(title="media", summary="s", importance_score=0.6,
                           source_urls=["https://someblog.com/x"])
    tiers = {"nvidianews.nvidia.com": "T1", "someblog.com": "T2"}

    ranked = rank_events([media, official], tiers)
    assert ranked[0].title == "official"


def test_select_report_events_caps_body_and_keeps_watchlist():
    body = [EventCandidate(title=f"b{i}", summary="s", importance_score=0.9 - i * 0.01) for i in range(20)]
    watch = [EventCandidate(title=f"w{i}", summary="s", is_watchlist=True) for i in range(5)]

    kept, overflow = select_report_events(body + watch, max_body=15, max_watchlist=8)

    assert sum(1 for e in kept if not e.is_watchlist) == 15
    assert sum(1 for e in kept if e.is_watchlist) == 5
    assert overflow == 5


def test_select_report_events_caps_watchlist():
    watch = [EventCandidate(title=f"w{i}", summary="s", is_watchlist=True) for i in range(12)]
    kept, overflow = select_report_events(watch, max_body=5, max_watchlist=8)

    assert len(kept) == 8
    assert overflow == 4

def test_important_watchlist_event_is_promoted_to_body():
    event = EventCandidate(
        title="重大但结论待定的发现",
        summary="s",
        importance_score=0.95,
        confidence=Confidence.HIGH,
        is_watchlist=True,
        verification_notes="已分层核验。",
    )

    promoted = promote_important_watchlist([event], threshold=0.85)

    assert event.is_watchlist is False
    assert "待观察" in event.tags
    assert "保留在正文" in event.verification_notes
    assert promoted == [event]


def test_low_confidence_or_minor_watchlist_stays_on_watchlist():
    low = EventCandidate(title="a", summary="s", importance_score=0.95,
                         confidence=Confidence.LOW, is_watchlist=True)
    minor = EventCandidate(title="b", summary="s", importance_score=0.4,
                           confidence=Confidence.HIGH, is_watchlist=True)

    promote_important_watchlist([low, minor], threshold=0.85)

    assert low.is_watchlist is True
    assert minor.is_watchlist is True


def test_promoted_event_reaches_the_top_section_of_the_report():
    from datetime import datetime, timezone

    from inforp.report import build_report

    event = EventCandidate(title="黎曼猜想级别的大事", summary="正文摘要", importance_score=0.95,
                           confidence=Confidence.HIGH, is_watchlist=True)
    promote_important_watchlist([event], threshold=0.85)

    report = build_report(
        kind=ReportKind.DAILY,
        events=[event],
        generated_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        token_usage=TokenUsage(),
        metadata={"raw_items": 1, "events_total": 1, "events_pushed": 1},
    )

    assert "黎曼猜想级别的大事" in report.markdown
    assert "待核验线索" in report.markdown
