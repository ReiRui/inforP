from __future__ import annotations

import json

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from inforp.config import Settings
from inforp.llm import (
    DeepSeekResearchClient,
    DeepSeekSearchClient,
    NoopResearchClient,
    build_research_client,
)
from inforp.schemas import Confidence, EventCandidate, SearchQuery, SourceKind, TopicType


def make_settings(**overrides):
    env = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "sk-test-key",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL": "deepseek-flash",
        "DEEPSEEK_VERIFIER_MODEL": "deepseek-flash",
        "DEEPSEEK_SEARCH_COUNT": "5",
        "DEEPSEEK_SEARCH_PER_QUERY": "2",
        "DEEPSEEK_SEARCH_CONCURRENCY": "2",
        "DEEPSEEK_VERIFY_SEARCH_TOP_N": "1",
        "ENABLE_LLM": "true",
    }
    env.update(overrides)
    for key, value in env.items():
        import os

        os.environ[key] = value
    return Settings(_env_file=None)


class FakeSearchClient:
    def __init__(self, results=None):
        self.calls: list[tuple[str, int]] = []
        self.results = results if results is not None else [
            {
                "title": "NVIDIA announces Q3 results",
                "url": "https://nvidianews.nvidia.com/news/q3",
                "summary": "NVIDIA reported record data-center revenue.",
                "date": "2026-11-20T00:00:00+00:00",
                "authority": 4,
                "site_name": "NVIDIA Newsroom",
            },
            {
                "title": "Duplicate copy of the same story",
                "url": "https://nvidianews.nvidia.com/news/q3",
                "summary": "Duplicate",
                "date": None,
                "authority": 2,
                "site_name": "Mirror",
            },
        ]

    def search(self, query, count=10, need_content=False, ori_question=None):
        self.calls.append((query, count))
        return list(self.results)

    def close(self):
        pass


class FakeResponses:
    def __init__(self):
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        name = kwargs["text"]["format"]["name"]
        if name == "search_plan":
            payload = {"queries": ["NVIDIA Q3 results", "NVIDIA data center revenue"]}
        else:
            events = kwargs["input"][1]["content"]
            payload = {
                "events": [
                    {
                        "title": "英伟达公布第三季度财报",
                        "summary": "英伟达数据中心业务推动营收创新高。" * 6,
                        "claims": ["数据中心营收创纪录"],
                        "topic": "ai_hardware",
                        "importance_score": 0.9,
                        "confidence": "high",
                        "source_urls": ["https://nvidianews.nvidia.com/news/q3"],
                        "verification_notes": "官方新闻稿支持。",
                        "tags": ["earnings"],
                    }
                ],
                "_input_chars": len(events),
            }
            payload = {"events": payload["events"]}
        return SimpleNamespace(
            output_text=json.dumps(payload, ensure_ascii=False),
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )


def make_client(settings, fake_search=None):
    client = DeepSeekResearchClient.__new__(DeepSeekResearchClient)
    client.settings = settings
    client.client = SimpleNamespace(responses=FakeResponses())
    client.search = fake_search or FakeSearchClient()
    client._closed = False
    return client


def test_build_research_client_returns_noop_when_disabled():
    settings = make_settings(ENABLE_LLM="false")
    assert isinstance(build_research_client(settings), NoopResearchClient)


def test_build_research_client_rejects_unknown_provider():
    settings = make_settings(LLM_PROVIDER="openai")
    with pytest.raises(ValueError):
        build_research_client(settings)


def test_collect_and_extract_builds_raw_items_from_search_results():
    settings = make_settings()
    search = FakeSearchClient()
    client = make_client(settings, search)

    result = client.collect_and_extract(
        [SearchQuery(id="ai_hw", topic=TopicType.AI_HARDWARE, query="find NVIDIA news")],
        report_kind="daily",
    )

    assert len(result.events) == 1
    assert result.events[0].title == "英伟达公布第三季度财报"
    # duplicate URL is dropped
    assert len(result.raw_items) == 1
    item = result.raw_items[0]
    assert item.url == "https://nvidianews.nvidia.com/news/q3"
    assert item.source_kind == SourceKind.SEARCH
    assert item.source_id == "deepseek_search:ai_hw"
    assert item.published_at == datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert result.token_usage.total_tokens == 300  # planning + extraction


def test_collect_and_extract_applies_date_filter_for_daily():
    settings = make_settings()
    search = FakeSearchClient()
    client = make_client(settings, search)

    client.collect_and_extract(
        [SearchQuery(id="q", topic=TopicType.AI_TECH, query="brief")], report_kind="daily"
    )

    assert search.calls, "search API should be called"
    assert all(" after:" in query for query, _ in search.calls)


def test_collect_and_extract_returns_early_without_results():
    settings = make_settings()
    client = make_client(settings, FakeSearchClient(results=[]))

    result = client.collect_and_extract(
        [SearchQuery(id="q", topic=TopicType.AI_TECH, query="brief")], report_kind="daily"
    )

    assert result.events == []
    assert result.raw_items == []


def test_verify_events_includes_cross_check_results():
    settings = make_settings()
    client = make_client(settings)
    event = EventCandidate(
        title="英伟达公布第三季度财报",
        summary="数据中心业务推动营收创新高。",
        confidence=Confidence.MEDIUM,
        importance_score=0.9,
        source_urls=["https://nvidianews.nvidia.com/news/q3"],
    )

    result = client.verify_events([event])

    assert len(result.events) == 1
    verification_request = client.client.responses.requests[-1]
    payload = json.loads(verification_request["input"][1]["content"])
    assert payload["cross_check_results"], "cross-check digest must be attached"
    assert payload["cross_check_results"][0]["results"][0]["url"]


def test_search_client_truncates_long_queries_and_clamps_count():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return __import__("httpx").Response(200, json={"results": []})

    client = DeepSeekSearchClient("sk-test", "https://api.deepseek.com")
    client._client = __import__("httpx").Client(transport=__import__("httpx").MockTransport(handler))

    client.search("x" * 500, count=999)

    assert len(captured["body"]["query"]) == DeepSeekSearchClient.MAX_QUERY_CHARS
    assert captured["body"]["count"] == 50


def test_search_client_returns_empty_list_on_failure():
    import httpx

    def handler(request):
        return httpx.Response(400, json={"error": {"message": "bad"}})

    client = DeepSeekSearchClient("sk-test", "https://api.deepseek.com")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.search("anything") == []


def test_plan_falls_back_to_query_id_when_planner_returns_nothing():
    settings = make_settings()
    client = make_client(settings)
    client.client.responses.create = lambda **kwargs: SimpleNamespace(
        output_text=json.dumps({"queries": []}),
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )

    client.collect_and_extract(
        [SearchQuery(id="fallback_id", topic=TopicType.AI_TECH, query="brief")], report_kind="daily"
    )

    assert any("fallback_id" in q for q, _ in client.search.calls)

from inforp.llm import repair_json_text


def test_repair_json_text_escapes_raw_control_chars_in_strings():
    broken = '{"events": [{"title": "a\nb", "summary": "x\u0000y"}]}'
    fixed = repair_json_text(broken)
    assert json.loads(fixed)["events"][0]["title"] == "a\nb"
    assert json.loads(fixed)["events"][0]["summary"] == "x\u0000y"


def test_repair_json_text_strips_markdown_fence():
    fenced = '```json\n{"events": []}\n```'
    assert json.loads(repair_json_text(fenced)) == {"events": []}


def test_repair_json_text_keeps_escaped_quotes_intact():
    payload = '{"events": [{"title": "say \\"hi\\"", "summary": "s"}]}'
    assert json.loads(repair_json_text(payload))["events"][0]["title"] == 'say "hi"'
