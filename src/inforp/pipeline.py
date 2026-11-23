from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from inforp.collectors.base import CollectionContext
from inforp.collectors.rss import RSSCollector
from inforp.collectors.web import WebPageCollector
from inforp.config import Settings, load_source_config
from inforp.delivery import build_deliveries, deliver_report, deliver_weekly_wecom_summary
from inforp.llm import build_research_client
from inforp.report import build_report
from inforp.schemas import CrawlMode, RawItem, Report, ReportKind, RunMetrics, SourceProfile, TokenUsage
from inforp.source_manager import compute_source_scores
from inforp.store import Store
from inforp.verification import (
    apply_confidence_policy,
    attach_source_signals,
    normalize_domain,
    promote_important_watchlist,
    rank_events,
    select_report_events,
)


class IntelligencePipeline:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.research = build_research_client(settings)
        self.rss = RSSCollector()
        self.web = WebPageCollector()

    def run_daily(self) -> Report:
        return self.run(ReportKind.DAILY)

    def run_weekly(self) -> Report:
        return self.run(ReportKind.WEEKLY)

    def run(self, kind: ReportKind) -> Report:
        started_at = datetime.now(timezone.utc)
        metrics = RunMetrics(run_id=uuid4().hex, kind=kind, started_at=started_at)
        source_config = load_source_config(self.settings.sources_file)
        self.store.upsert_sources(source_config.sources)

        context = CollectionContext(started_at=started_at, report_kind=kind.value)
        raw_items = self._collect_fixed_sources(source_config.sources, context)
        tiers_by_domain = self._tiers_by_domain(source_config.sources)

        queries = source_config.daily_queries if kind == ReportKind.DAILY else source_config.weekly_queries
        llm_search_result = self.research.collect_and_extract(queries, kind.value) if source_config.llm_enabled else None
        if llm_search_result:
            raw_items.extend(llm_search_result.raw_items)
            metrics.token_usage += llm_search_result.token_usage

        raw_item_ids = self.store.save_raw_items(raw_items)
        metrics.raw_items = len(raw_items)

        extracted = self.research.extract_from_raw_items(raw_items, kind.value)
        metrics.token_usage += extracted.token_usage

        events = []
        if llm_search_result:
            events.extend(llm_search_result.events)
        events.extend(extracted.events)
        metrics.events_total = len(events)

        verified = self.research.verify_events(events)
        metrics.token_usage += verified.token_usage
        events = verified.events

        for event in events:
            event.raw_item_ids = raw_item_ids[:20]

        accepted, filtered_ai_low = apply_confidence_policy(events)
        promote_important_watchlist(accepted, threshold=self.settings.watchlist_promote_importance)
        attach_source_signals(accepted, tiers_by_domain)
        ranked = rank_events(accepted, tiers_by_domain)
        metrics.low_confidence_ai_filtered = filtered_ai_low

        body_limit = (
            self.settings.max_report_events
            if kind == ReportKind.DAILY
            else self.settings.max_weekly_report_events
        )
        report_events, overflow = select_report_events(
            ranked, max_body=body_limit, max_watchlist=self.settings.max_watchlist_events
        )
        metrics.events_pushed = sum(1 for event in report_events if not event.is_watchlist)
        metrics.watchlist_events = sum(1 for event in report_events if event.is_watchlist)

        # The database keeps every ranked event; only the report is bounded.
        self.store.save_events(ranked, created_at=started_at)
        self.store.upsert_source_scores(compute_source_scores(source_config.sources, ranked))

        metadata = {
            "raw_items": metrics.raw_items,
            "events_total": metrics.events_total,
            "events_pushed": metrics.events_pushed,
            "events_overflow": overflow,
            "low_confidence_ai_filtered": metrics.low_confidence_ai_filtered,
            "watchlist_events": metrics.watchlist_events,
        }
        report = build_report(
            kind=kind,
            events=report_events,
            generated_at=datetime.now(timezone.utc),
            timezone_name=self.settings.timezone,
            token_usage=metrics.token_usage,
            metadata=metadata,
        )
        self.store.save_report(report)

        metrics.delivery_results = deliver_report(report, build_deliveries(self.settings))
        metrics.delivery_results.extend(deliver_weekly_wecom_summary(report, self.settings))
        metrics.finished_at = datetime.now(timezone.utc)
        self.store.save_run_metrics(metrics)
        return report

    def _collect_fixed_sources(self, sources, context: CollectionContext) -> list[RawItem]:
        """Collect fixed sources concurrently.

        Sequential collection made a full run take minutes because every slow host paid its
        own timeout back to back. Errors are still per-source and never abort the run.
        """
        targets = [
            source
            for source in sources
            if source.observe_enabled and source.crawl_mode in (CrawlMode.RSS, CrawlMode.WEB)
        ]

        def collect_one(source: SourceProfile) -> list[RawItem]:
            try:
                if source.crawl_mode == CrawlMode.RSS:
                    return self.rss.collect(source, context)
                return self.web.collect(source, context)
            except Exception as exc:  # a single broken source must not fail the run
                return [
                    RawItem(
                        title=f"Collector crashed for {source.name}",
                        url=source.url,
                        source_id=source.id,
                        source_name=source.name,
                        source_kind=source.kind,
                        content=str(exc),
                        metadata={"collector": source.crawl_mode.value, "error": True},
                    )
                ]

        if not targets:
            return []

        workers = max(1, min(self.settings.collect_concurrency, len(targets)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(pool.map(collect_one, targets))

        items = [item for batch in batches for item in batch]
        return self._trim_collected(items)

    def _trim_collected(self, items: list[RawItem]) -> list[RawItem]:
        """Keep the collection window usable for extraction.

        RSS feeds can return 50 entries each, which drowns the 36-hour brief. Keep only
        recent items plus undated ones, cap per source, and sort newest first so the
        extraction call sees the freshest material.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.collect_lookback_hours)
        per_source: dict[str, list[RawItem]] = {}
        dropped_old = 0
        for item in items:
            if item.metadata.get("error"):
                per_source.setdefault(item.source_id, []).append(item)
                continue
            if item.published_at is not None:
                published = item.published_at
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    dropped_old += 1
                    continue
            per_source.setdefault(item.source_id, []).append(item)

        kept: list[RawItem] = []
        cap = max(1, self.settings.collect_max_items_per_source)
        for source_items in per_source.values():
            source_items.sort(
                key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            kept.extend(source_items[:cap])
        kept.sort(
            key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return kept

    @staticmethod
    def _tiers_by_domain(sources) -> dict[str, str]:
        tiers: dict[str, str] = {}
        for source in sources:
            domain = normalize_domain(source.url)
            if domain and source.tier:
                tiers.setdefault(domain, source.tier)
        return tiers


def build_pipeline(settings: Settings) -> IntelligencePipeline:
    store = Store(settings.database_url)
    store.init_db()
    return IntelligencePipeline(settings=settings, store=store)
