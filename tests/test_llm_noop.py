from inforp.llm import NoopResearchClient
from inforp.schemas import RawItem, SearchQuery, SourceKind, TopicType


def test_noop_research_client_does_not_emit_query_placeholders():
    client = NoopResearchClient()
    result = client.collect_and_extract(
        [SearchQuery(id="ai", topic=TopicType.AI_TECH, query="find AI news")],
        report_kind="daily",
    )

    assert result.events == []
    assert result.raw_items == []


def test_noop_research_client_uses_chinese_verification_note():
    client = NoopResearchClient()
    result = client.extract_from_raw_items(
        [
            RawItem(
                title="Source title",
                source_id="source",
                source_name="Source",
                source_kind=SourceKind.OFFICIAL,
                topic_hint=TopicType.AI_TECH,
                content="Some content",
            )
        ],
        report_kind="daily",
    )

    assert len(result.events) == 1
    assert "本地 LLM 未启用" in result.events[0].verification_notes
    assert "LLM disabled" not in result.events[0].verification_notes

