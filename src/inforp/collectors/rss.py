from __future__ import annotations

from dateutil import parser as date_parser
import feedparser
import httpx

from inforp.collectors.base import CollectionContext
from inforp.schemas import RawItem, SourceProfile


class RSSCollector:
    """Fetch the feed with httpx, then hand the bytes to feedparser.

    feedparser's own URL fetching goes through urllib, which fails TLS verification for
    some hosts in this environment (arxiv) and silently returns zero entries. Fetching
    with httpx first keeps one HTTP client/header set across collectors and makes feed
    failures visible instead of looking like an empty feed.
    """

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; inforP/0.1; personal intelligence collector)",
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        }

    def collect(self, source: SourceProfile, context: CollectionContext) -> list[RawItem]:
        if not source.url:
            return []
        try:
            response = httpx.get(source.url, headers=self.headers, follow_redirects=True, timeout=20)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return [
                RawItem(
                    title=f"Failed to fetch {source.name}",
                    url=source.url,
                    source_id=source.id,
                    source_name=source.name,
                    source_kind=source.kind,
                    topic_hint=source.topic_hints[0] if source.topic_hints else None,
                    content=str(exc),
                    metadata={"collector": "rss", "error": True},
                )
            ]

        feed = feedparser.parse(response.content)
        items: list[RawItem] = []
        for entry in feed.entries[:50]:
            published_at = None
            date_value = getattr(entry, "published", None) or getattr(entry, "updated", None)
            if date_value:
                try:
                    published_at = date_parser.parse(date_value)
                except (ValueError, TypeError, OverflowError):
                    published_at = None

            content = getattr(entry, "summary", None)
            if not content and getattr(entry, "content", None):
                content = entry.content[0].get("value")

            items.append(
                RawItem(
                    title=getattr(entry, "title", source.name),
                    url=getattr(entry, "link", source.url),
                    source_id=source.id,
                    source_name=source.name,
                    source_kind=source.kind,
                    topic_hint=source.topic_hints[0] if source.topic_hints else None,
                    published_at=published_at,
                    content=content,
                    metadata={"collector": "rss"},
                )
            )
        return items
