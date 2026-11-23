from datetime import datetime, timezone

from inforp.report import build_report, _render_event
from inforp.schemas import EventCandidate, ReportKind, TokenUsage, TopicType


def test_render_event_uses_detailed_summary_and_more_claims():
    event = EventCandidate(
        title="测试事件",
        summary="这是一段包含背景、影响和下一步观察点的详细摘要。",
        claims=["断言1", "断言2", "断言3", "断言4", "断言5", "断言6"],
    )

    rendered = "\n".join(_render_event(1, event))

    assert "详细摘要" in rendered
    assert "断言5" in rendered
    assert "断言6" not in rendered


def test_daily_report_uses_three_main_boards_and_ai_hardware_topic():
    events = [
        EventCandidate(title="模型更新", summary="模型能力更新。", topic=TopicType.AI_TECH, source_urls=["https://example.com/model"]),
        EventCandidate(title="GPU 供给", summary="GPU 与服务器供给更新。", topic=TopicType.AI_HARDWARE, source_urls=["https://example.com/gpu"]),
        EventCandidate(title="宏观政策", summary="政策变化影响产业预期。", topic=TopicType.CHINA_POLICY, source_urls=["https://example.com/policy"]),
    ]

    report = build_report(
        kind=ReportKind.DAILY,
        events=events,
        generated_at=datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        token_usage=TokenUsage(),
        metadata={},
    )

    assert [section.title for section in report.sections] == [
        "30 秒读完",
        "AI 技术与产品应用",
        "AI 基础设施与硬件供应链",
        "政策、产业资本与宏观地缘",
        "待核验线索 / 观察池",
        "周末需盯 / 下周一回看",
        "覆盖范围与遗漏风险",
        "来源索引",
    ]
    assert "GPU 供给" in report.markdown
    assert "Asia/Shanghai / UTC+08:00" in report.markdown
    assert "CST" not in report.markdown
    assert "## 来源索引" in report.markdown
    assert "[S1] https://example.com/model" in report.markdown
    assert "来源：[S2]" in report.markdown
    assert "本次未覆盖或弱覆盖区域" in report.markdown


def test_friday_daily_report_includes_weekend_watch_body():
    event = EventCandidate(
        title="EDA 工具链更新",
        summary="EDA/IP 相关事件。",
        topic=TopicType.AI_HARDWARE,
        is_watchlist=True,
    )

    report = build_report(
        kind=ReportKind.DAILY,
        events=[event],
        generated_at=datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        token_usage=TokenUsage(),
        metadata={},
    )

    assert "## 周末需盯 / 下周一回看" in report.markdown
    assert "EDA 工具链更新" in report.markdown
    assert "周末重点复查" in report.markdown
