from __future__ import annotations

from datetime import datetime, timezone

from inforp.delivery import build_wecom_weekly_summary_markdown
from inforp.schemas import Report, ReportKind, TokenUsage

WEEKLY_MARKDOWN = """# inforP 周报 - 2026-11-23

## 上周最重要 10 件事

1. **英伟达 Q3 财报** [NVIDIA / 财报 / 多源确认]
   - 详细摘要：英伟达第三季度营收 1158 亿美元，同比增长约 103%，数据中心业务占九成以上。
   - 重要性：0.90；置信度：high
2. **Anthropic 上市** [Anthropic / IPO / 多源确认]
   - 详细摘要：Anthropic 以每股 83 美元定价在纳斯达克挂牌，基础募资约 813 亿美元。
   - 重要性：0.85；置信度：high
"""


def _report(markdown: str) -> Report:
    return Report(
        kind=ReportKind.WEEKLY,
        title="inforP 周报 - 2026-11-23",
        generated_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
        sections=[],
        markdown=markdown,
        token_usage=TokenUsage(),
        metadata={},
    )


def test_wecom_summary_uses_detail_block_not_tag_bracket():
    text = build_wecom_weekly_summary_markdown(_report(WEEKLY_MARKDOWN), max_items=2)

    assert "英伟达 Q3 财报" in text
    assert "1158 亿美元" in text
    assert "813 亿美元" in text
    # tag brackets must not be mistaken for the summary
    assert "多源确认]" not in text


def test_wecom_summary_falls_back_when_no_sections_or_items():
    text = build_wecom_weekly_summary_markdown(_report("# empty\n"), max_items=10)
    assert "本周暂无可推送重点" in text
