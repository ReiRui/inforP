from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from inforp.config import Settings
from inforp.schemas import EventCandidate, LLMEventBatch, RawItem, SearchQuery, SourceKind, TokenUsage

CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def repair_json_text(text: str) -> str:
    """Make a model's JSON payload parseable without changing its data.

    Two failure modes seen in practice: the payload arrives wrapped in a Markdown code
    fence, and one of its strings contains a raw control character (a literal newline or
    NUL), which strict JSON forbids. Both are fixed here; a genuinely malformed payload
    still fails loudly.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    out: list[str] = []
    in_string = False
    escaped = False
    for char in cleaned:
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\":
                out.append(char)
                escaped = True
                continue
            if char == '"':
                out.append(char)
                in_string = False
                continue
            if ord(char) < 0x20:
                out.append(CONTROL_ESCAPES.get(char, f"\\u{ord(char):04x}"))
                continue
            out.append(char)
        else:
            out.append(char)
            if char == '"':
                in_string = True
    # Any raw control character still present is outside a string, where only tab/newline/
    # carriage return are legal JSON whitespace; NUL and friends must go.
    return "".join(ch for ch in "".join(out) if ch in "\t\n\r" or ord(ch) >= 0x20)


@dataclass
class ResearchResult:
    events: list[EventCandidate]
    raw_items: list[RawItem]
    token_usage: TokenUsage


class ResearchClient:
    profile: dict[str, Any] | None = None

    def set_profile(self, profile: dict[str, Any] | None) -> None:
        """Attach the operator-configured focus (from sources.yaml `profile`)."""
        self.profile = profile or {}

    def collect_and_extract(self, queries: list[SearchQuery], report_kind: str) -> ResearchResult:
        raise NotImplementedError

    def extract_from_raw_items(self, items: list[RawItem], report_kind: str) -> ResearchResult:
        raise NotImplementedError

    def verify_events(self, events: list[EventCandidate]) -> ResearchResult:
        raise NotImplementedError


class NoopResearchClient(ResearchClient):
    def collect_and_extract(self, queries: list[SearchQuery], report_kind: str) -> ResearchResult:
        return ResearchResult(events=[], raw_items=[], token_usage=TokenUsage())

    def extract_from_raw_items(self, items: list[RawItem], report_kind: str) -> ResearchResult:
        events = [
            EventCandidate(
                title=item.title,
                summary=(item.content or item.title)[:1200],
                topic=item.topic_hint or item.metadata.get("topic_hint") or "other",
                confidence="medium",
                importance_score=0.3,
                source_urls=[item.url] if item.url else [],
                source_ids=[item.source_id],
                published_at=item.published_at,
                verification_notes="本地 LLM 未启用，仅按固定源原文生成占位条目；未完成远程搜索、交叉核验和反证检查。",
            )
            for item in items
            if not item.metadata.get("error")
        ]
        return ResearchResult(events=events, raw_items=[], token_usage=TokenUsage())

    def verify_events(self, events: list[EventCandidate]) -> ResearchResult:
        return ResearchResult(events=events, raw_items=[], token_usage=TokenUsage())


class SearchPlan(BaseModel):
    queries: list[str] = Field(default_factory=list)


class DeepSeekSearchClient:
    """Thin wrapper around the DeepSeek Search API (POST /v1/tools/search)."""

    MAX_QUERY_CHARS = 128
    RETRY_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def search(
        self,
        query: str,
        count: int = 10,
        need_content: bool = False,
        ori_question: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": query.strip()[: self.MAX_QUERY_CHARS],
            "count": max(1, min(50, int(count))),
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = ori_question.strip()[: self.MAX_QUERY_CHARS]

        for attempt in range(3):
            try:
                response = self._client.post(f"{self.base_url}/v1/tools/search", json=payload)
                if response.status_code in self.RETRY_STATUS and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                results = response.json().get("results", [])
                return results if isinstance(results, list) else []
            except Exception:  # network hiccup: retry, then give up on this query
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        return []


class DeepSeekResearchClient(ResearchClient):
    """DeepSeek-backed research client.

    Retrieval is delegated to the deterministic Search API, and the model is used only for
    query planning, structured extraction and verification. The DeepSeek web-search *tool*
    is deliberately not used together with structured output because the API rejects
    ``text.format`` alongside ``deepseek_web_search``.
    """

    def __init__(self, settings: Settings, search_client: DeepSeekSearchClient | None = None):
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when ENABLE_LLM=true")

        from openai import OpenAI

        self.settings = settings
        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )
        self.search = search_client or DeepSeekSearchClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=min(settings.deepseek_timeout_seconds, 120.0),
        )
        self._closed = False

    # -- retrieval -------------------------------------------------------
    def _plan_queries(self, search_query: SearchQuery, report_kind: str) -> tuple[SearchPlan, TokenUsage]:
        prompt = {
            "report_kind": report_kind,
            "research_brief": search_query.query,
            "max_queries": self.settings.deepseek_search_per_query,
        }
        plan, usage = self._request_json(
            system_prompt=(
                "You turn a research brief into short web-search queries for a keyword search engine. "
                "Return 1-4 queries, each under 100 characters, using concrete entity names, product "
                "names and dates instead of long instructions. Prefer English for international "
                "subjects and Chinese for China policy subjects. Never include search operators."
            ),
            user_payload=prompt,
            model=self.settings.deepseek_model,
            schema=SearchPlan,
            schema_name="search_plan",
        )
        queries = [item.strip() for item in plan.queries if item and item.strip()]
        return SearchPlan(queries=queries[: self.settings.deepseek_search_per_query]), usage

    def _search_one(self, query: str, ori_question: str) -> list[dict[str, Any]]:
        return self.search.search(
            query=query,
            count=self.settings.deepseek_search_count,
            need_content=self.settings.deepseek_search_need_content,
            ori_question=ori_question,
        )

    def _date_filter(self, report_kind: str) -> str:
        if not self.settings.deepseek_search_date_filter:
            return ""
        lookback = timedelta(hours=36) if report_kind == "daily" else timedelta(days=9)
        cutoff = (datetime.now(timezone.utc) - lookback).date().isoformat()
        return f" after:{cutoff}"

    @staticmethod
    def _to_raw_item(item: dict[str, Any], query: SearchQuery, site_kind: SourceKind) -> RawItem | None:
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title:
            return None
        published_at = None
        raw_date = item.get("date")
        if raw_date:
            try:
                published_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        body = (item.get("content") or "").strip() or (item.get("summary") or "").strip() or (item.get("snippet") or "")
        return RawItem(
            title=title,
            url=url,
            source_id=f"deepseek_search:{query.id}",
            source_name=item.get("site_name") or "DeepSeek Search",
            source_kind=site_kind,
            topic_hint=query.topic,
            published_at=published_at,
            content=body or None,
            metadata={
                "authority": item.get("authority"),
                "rerank_score": item.get("rerank_score"),
                "query_id": query.id,
            },
        )

    def collect_and_extract(self, queries: list[SearchQuery], report_kind: str) -> ResearchResult:
        if not queries:
            return ResearchResult(events=[], raw_items=[], token_usage=TokenUsage())

        usage = TokenUsage()
        date_suffix = self._date_filter(report_kind)
        keyword_plan: list[tuple[SearchQuery, str]] = []
        for query in queries:
            plan, plan_usage = self._plan_queries(query, report_kind)
            usage += plan_usage
            for keyword in plan.queries or [query.id]:
                keyword_plan.append((query, f"{keyword}{date_suffix}"))

        unique_keywords = list(dict.fromkeys(keyword for _, keyword in keyword_plan))

        def fetch(keyword: str) -> list[dict[str, Any]]:
            return self._search_one(keyword, ori_question=keyword.split(" after:")[0])

        with ThreadPoolExecutor(max_workers=max(1, self.settings.deepseek_search_concurrency)) as pool:
            search_results = list(pool.map(fetch, unique_keywords))

        by_keyword = dict(zip(unique_keywords, search_results))

        raw_items: list[RawItem] = []
        bundle: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        per_query_budget = max(1, self.settings.deepseek_results_per_query)

        for query in queries:
            keywords = [kw for q, kw in keyword_plan if q.id == query.id]
            collected = 0
            for keyword in keywords:
                for item in by_keyword.get(keyword, []):
                    url = (item.get("url") or "").strip()
                    if not url or url in seen_urls or collected >= per_query_budget:
                        continue
                    seen_urls.add(url)
                    raw_item = self._to_raw_item(item, query, SourceKind.SEARCH)
                    if raw_item is None:
                        continue
                    raw_item.metadata["report_kind"] = report_kind
                    raw_items.append(raw_item)
                    bundle.append(
                        {
                            "query_id": query.id,
                            "topic": query.topic.value,
                            "title": str(item.get("title") or "")[:300],
                            "url": url,
                            "summary": str(item.get("summary") or item.get("snippet") or item.get("content") or "")[:1200],
                            "date": item.get("date"),
                            "authority": item.get("authority"),
                            "site_name": item.get("site_name"),
                        }
                    )
                    collected += 1

        if not bundle:
            return ResearchResult(events=[], raw_items=raw_items, token_usage=usage)

        batch, batch_usage = self._request_json(
            system_prompt=(
                "You are an intelligence researcher turning search results into verified structured event "
                "candidates. All user-facing fields must be written in Simplified Chinese. "
                "The report is read directly in email; do not force the reader to open source links for "
                "basic context."
            ),
            user_payload={
                "report_kind": report_kind,
                "queries": [{"id": q.id, "topic": q.topic.value, "brief": q.query} for q in queries],
                "search_results": bundle,
                "rules": self._extraction_rules(),
            },
            model=self.settings.deepseek_model,
            schema=LLMEventBatch,
            schema_name="llm_event_batch",
        )
        usage += batch_usage
        return ResearchResult(events=batch.events, raw_items=raw_items, token_usage=usage)

    def extract_from_raw_items(self, items: list[RawItem], report_kind: str) -> ResearchResult:
        if not items:
            return ResearchResult(events=[], raw_items=[], token_usage=TokenUsage())

        deduped: list[RawItem] = []
        seen_titles: set[str] = set()
        for item in items:
            key = " ".join(item.title.lower().split())[:120]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            deduped.append(item)

        char_limit = max(500, self.settings.deepseek_raw_item_chars)
        compact_items = [
            {
                "title": item.title,
                "url": item.url,
                "source_id": item.source_id,
                "source_name": item.source_name,
                "source_kind": item.source_kind.value,
                "topic_hint": item.topic_hint.value if item.topic_hint else None,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "content": (item.content or "")[:char_limit],
            }
            for item in deduped[: self.settings.deepseek_max_raw_items_per_call]
        ]
        batch, usage = self._request_json(
            system_prompt=(
                "Extract important, deduplicated event candidates from raw source items. "
                "All user-facing fields must be written in Simplified Chinese. "
                "Translate English source content into Chinese. "
                "Summaries must include enough context, details, and implications for direct reading."
            ),
            user_payload={"report_kind": report_kind, "raw_items": compact_items},
            model=self.settings.deepseek_model,
            schema=LLMEventBatch,
            schema_name="llm_event_batch",
        )
        return ResearchResult(events=batch.events, raw_items=[], token_usage=usage)

    def verify_events(self, events: list[EventCandidate]) -> ResearchResult:
        if not events:
            return ResearchResult(events=[], raw_items=[], token_usage=TokenUsage())

        usage = TokenUsage()
        cross_checks: list[dict[str, Any]] = []
        top_n = max(0, self.settings.deepseek_verify_search_top_n)
        if top_n:
            ranked = sorted(events, key=lambda e: e.importance_score, reverse=True)[:top_n]

            def check(event: EventCandidate) -> dict[str, Any]:
                query = event.title.strip()[:100]
                results = self.search.search(query=query, count=5, need_content=False)
                return {
                    "event_title": event.title,
                    "query": query,
                    "results": [
                        {
                            "title": str(r.get("title") or "")[:200],
                            "url": r.get("url"),
                            "summary": str(r.get("summary") or r.get("snippet") or "")[:500],
                            "date": r.get("date"),
                            "authority": r.get("authority"),
                        }
                        for r in results[:5]
                    ],
                }

            with ThreadPoolExecutor(max_workers=max(1, self.settings.deepseek_search_concurrency)) as pool:
                cross_checks = list(pool.map(check, ranked))

        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "cross_check_results": cross_checks,
            "verification_rules": [
                "Write all verification notes and corrected summaries in Simplified Chinese.",
                "Do not shrink the summary during verification; preserve or expand useful factual detail.",
                "Verification notes should explain which source type supports the claim and what remains uncertain.",
                "Raise confidence only when source quality supports it.",
                "Add contradictions if reputable sources disagree or if cross_check_results conflict with the event.",
                "Do not invent new events: return the same events you were given. You may merge duplicates, "
                "but never add an event that is absent from the input list.",
                "If two input events describe the same thing, merge them and keep the union of their source URLs.",
                "Keep rumors low confidence so downstream filters can remove them.",
                "Mark non-AI low-confidence but important items as watchlist.",
                "Do not mark an important event as watchlist just because its acceptance is still "
                "pending. Report the event in the main body and put the open questions in "
                "contradictions and verification_notes. Reserve is_watchlist for low-importance, "
                "thin, single-source leads; a contested but major story is still a major story.",
                "Every event must keep at least one real source URL from its input.",
            ],
        }
        batch, batch_usage = self._request_json(
            system_prompt=(
                "Verify event candidates, adjust confidence, and find contradictions using the supplied "
                "cross-check search results. All user-facing fields must be written in Simplified Chinese. "
                "Keep enough factual detail in the final event for direct email reading."
            ),
            user_payload=payload,
            model=self.settings.deepseek_verifier_model,
            schema=LLMEventBatch,
            schema_name="llm_event_batch",
        )
        usage += batch_usage
        return ResearchResult(events=batch.events, raw_items=[], token_usage=usage)

    # -- helpers ---------------------------------------------------------
    def _focus_rule(self) -> str:
        """Build the industry rule from config instead of hardcoding one.

        The operator sets `profile.focus_label` and `profile.focus_directions` in
        `configs/sources.yaml`; nothing about a particular industry lives in the code.
        """
        profile = self.profile or {}
        label = str(profile.get("focus_label") or "").strip()
        directions = [str(item).strip() for item in (profile.get("focus_directions") or []) if str(item).strip()]
        if not directions:
            return (
                "Raise importance for the operator's configured focus areas while preserving "
                "the same verification threshold; do not lower the verification bar for them."
            )
        focus = "; ".join(directions)
        if label:
            return (
                f"Operator-configured focus industry: {label}. Raise importance for {focus} "
                "while preserving the same verification threshold."
            )
        return (
            f"Raise importance for {focus} while preserving the same verification threshold."
        )

    def _extraction_rules(self) -> list[str]:
        return [
            "Write every title, summary, claim, tag, and verification note in Simplified Chinese.",
            "Translate English source material into Chinese. Keep proper nouns and model names unchanged when appropriate.",
            "Each event summary must be information-dense and directly readable without opening links.",
            "Classify AI hardware, GPU, accelerator, server, HBM, packaging, foundry, EDA tools, semiconductor IP, chip design flows, verification, physical design, PDK/design rules, data-center, power, cooling, networking, and optical communication items as ai_hardware.",
            self._focus_rule(),
            "Use three report board semantics: AI technology/product application, AI infrastructure/hardware supply chain, and policy/industrial capital/macro/geopolitics.",
            "For important events, write 250-500 Chinese characters covering: who/what/when/where, key numbers or concrete details, background, why it matters, expected impact, and what to watch next.",
            "For ordinary events, write at least 120-220 Chinese characters unless the source itself is too thin.",
            "Claims must be concrete factual points, not generic restatements.",
            "Prefer primary sources, official announcements, papers, code repositories, and regulator pages.",
            "Forum and social posts are leads only unless independently confirmed.",
            "For AI topics, exclude low-confidence rumors from the final events.",
            "For non-AI topics, low-confidence items may be marked as watchlist if important.",
            "Merge duplicates that describe the same event and keep the strongest source URLs.",
            "Only use URLs that appear in search_results; never invent a URL.",
        ]

    def _request_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        model: str,
        schema: type[BaseModel],
        schema_name: str,
        attempts: int = 2,
    ) -> tuple[Any, TokenUsage]:
        json_schema = schema.model_json_schema()
        request: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                }
            },
        }

        usage = TokenUsage()
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            response = self.client.responses.create(**request)
            output_text = self._extract_output_text(response)
            usage += self._extract_usage(response)
            try:
                payload = json.loads(repair_json_text(output_text))
                return schema.model_validate(payload), usage
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                if attempt + 1 >= max(1, attempts):
                    break
        raise RuntimeError(
            f"Model returned unparseable JSON for schema {schema_name} after {attempts} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text
        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    chunks.append(text)
        if not chunks:
            raise ValueError("DeepSeek response did not contain output_text")
        return "".join(chunks)

    @staticmethod
    def _extract_usage(response: Any) -> TokenUsage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return TokenUsage()
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)

    def close(self) -> None:
        if not self._closed:
            self.search.close()
            self._closed = True


def build_research_client(settings: Settings) -> ResearchClient:
    if not settings.enable_llm:
        return NoopResearchClient()
    provider = (settings.llm_provider or "deepseek").strip().lower()
    if provider != "deepseek":
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}. Only 'deepseek' is implemented.")
    return DeepSeekResearchClient(settings)
