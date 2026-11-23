from __future__ import annotations

from bs4 import BeautifulSoup
import httpx

from inforp.collectors.base import CollectionContext
from inforp.schemas import RawItem, SourceProfile


class WebPageCollector:
    def __init__(self) -> None:
        self.headers = {
            "User-Agent": "inforP/0.1 personal intelligence collector",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
                    metadata={"collector": "web", "error": True},
                )
            ]

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else source.name
        text = " ".join(soup.get_text(" ", strip=True).split())

        return [
            RawItem(
                title=title or source.name,
                url=str(response.url),
                source_id=source.id,
                source_name=source.name,
                source_kind=source.kind,
                topic_hint=source.topic_hints[0] if source.topic_hints else None,
                content=text[:12000],
                metadata={"collector": "web", "status_code": response.status_code},
            )
        ]

