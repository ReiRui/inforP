from inforp.delivery import _markdown_to_email_html
from inforp.delivery import _prepare_wecom_markdown
from inforp.delivery import _subject_for_report
from inforp.delivery import _truncate_wecom_markdown
from inforp.delivery import WeComWeeklySummaryDelivery
from inforp.delivery import build_wecom_weekly_summary_markdown
from inforp.recipients import active_recipient_emails
from inforp.schemas import EventCandidate, Report, ReportKind, ReportSection, TokenUsage

from datetime import datetime, timezone


def test_markdown_email_html_preserves_chinese_and_linkifies_urls():
    html = _markdown_to_email_html("# 标题\n\n来源：https://example.com/path。\n")

    assert "标题" in html
    assert '<a href="https://example.com/path"' in html
    assert 'charset="utf-8"' in html


def test_subject_falls_back_to_markdown_heading_when_title_is_corrupted():
    report = Report(
        kind=ReportKind.DAILY,
        title="inforP ??????- 2026-08-07",
        generated_at=datetime.now(timezone.utc),
        sections=[],
        markdown="# inforP 日报（新版格式示例）- 2026-08-07\n\n正文",
        token_usage=TokenUsage(),
    )

    assert _subject_for_report(report) == "inforP 日报（新版格式示例）- 2026-08-07"


def test_wecom_weekly_summary_uses_top_events_only():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[
            ReportSection(
                title="上周最重要 10 件事",
                events=[
                    EventCandidate(title="重点一", summary="这是一条重要影响说明。"),
                    EventCandidate(title="重点二", summary="这是另一条重要影响说明。"),
                    EventCandidate(title="重点三", summary="这条不应该出现。"),
                ],
            )
        ],
        markdown="# weekly",
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=2)

    assert "inforP 周报重点" in markdown
    assert "重点一" in markdown
    assert "重点二" in markdown
    assert "重点三" not in markdown
    assert "完整周报已发邮箱，可联系项目负责人获取" in markdown


def test_wecom_summary_can_extract_items_from_markdown_report():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[],
        markdown=(
            "# weekly\n\n"
            "## 上周最重要 10 件事\n\n"
            "1. **模型发布**\n"
            "   - 重要性：影响产品选型和成本。\n"
            "2. **芯片供给**\n"
            "   - 影响：影响算力交付周期。\n"
        ),
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=5)

    assert "模型发布" in markdown
    assert "影响产品选型和成本" in markdown
    assert "芯片供给" in markdown
    assert "要点：" in markdown


def test_wecom_summary_extracts_inline_numbered_weekly_items():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        sections=[],
        markdown=(
            "# weekly\n\n"
            "## 上周最重要 10 件事\n\n"
            "1. **OpenAI 暂停训练** [安全]：多家可靠媒体报道称训练暂停，"
            "企业需要复查 agent 权限边界和人工接管。事实置信度：中高；影响判断置信度：高。[S1]\n"
            "2. **NVIDIA 强调 AI 工厂** [硬件]：供电、冷却和整站认证成为交付关键路径。"
            "事实置信度：高；影响判断置信度：中高。[S2]\n"
        ),
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=2)

    assert "OpenAI 暂停训练" in markdown
    assert "企业需要复查 agent 权限边界和人工接管" in markdown
    assert "事实置信度" not in markdown
    assert "NVIDIA 强调 AI 工厂" in markdown
    assert "供电、冷却和整站认证成为交付关键路径" in markdown
    assert "详见邮件周报" not in markdown


def test_wecom_summary_extracts_plain_numbered_weekly_items():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
        sections=[],
        markdown=(
            "# weekly\n\n"
            "## 1. 上周最重要 10 件事\n\n"
            "1. NVIDIA 宣布 CEO transition，Jensen Huang 辞任 CEO，"
            "Colette Kress 任临时 CEO；这是 AI 硬件供应链和资本市场的最高优先级事件。[S1]\n"
            "2. 中国技术出口目录征求意见结束，EDA/IP 跨境交付需要重点复查。[S2]\n"
        ),
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=2)

    assert "NVIDIA 宣布 CEO transition" in markdown
    assert "AI 硬件供应链和资本市场的最高优先级事件" in markdown
    assert "中国技术出口目录征求意见结束" in markdown
    assert "EDA/IP 跨境交付需要重点复查" in markdown
    assert "本周暂无可推送重点" not in markdown


def test_wecom_summary_allows_ten_top_items():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[
            ReportSection(
                title="上周最重要 10 件事",
                events=[
                    EventCandidate(title=f"重点{i}", summary=f"第{i}条影响说明。")
                    for i in range(1, 12)
                ],
            )
        ],
        markdown="# weekly",
        token_usage=TokenUsage(),
    )

    delivery = WeComWeeklySummaryDelivery("https://example.com/webhook", top_items=99)
    markdown = build_wecom_weekly_summary_markdown(report, max_items=delivery.top_items)

    assert delivery.top_items == 10
    assert "重点10" in markdown
    assert "重点11" not in markdown
    assert "完整周报已发邮箱，可联系项目负责人获取" in markdown


def test_wecom_summary_prefers_weekly_top_items_when_brief_comes_first():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[],
        markdown=(
            "# weekly\n\n"
            "## 30 秒读完\n\n"
            "- 本周有几条高优先级消息。\n\n"
            "## 上周最重要 10 件事\n\n"
            "1. **OpenAI 扩大 GPT-5.6 覆盖**\n"
            "   - 影响：影响模型选型和默认助手体验。\n"
            "2. **Firebird 启动 AI factory**\n"
            "   - 影响：影响跨境算力和 GPU 供给观察。\n"
        ),
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=5)

    assert "本周暂无可推送重点" not in markdown
    assert "OpenAI 扩大 GPT-5.6 覆盖" in markdown
    assert "Firebird 启动 AI factory" in markdown


def test_wecom_summary_does_not_include_local_report_paths():
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[
            ReportSection(
                title="上周最重要 10 件事",
                events=[
                    EventCandidate(
                        title="EDA/IP 政策",
                        summary="影响布图设计保护。完整周报已发邮箱；本地文件为 reports/2026-08-10_weekly_remote.md。",
                    )
                ],
            )
        ],
        markdown="# weekly",
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=1)

    assert "EDA/IP 政策" in markdown
    assert "完整周报已发邮箱，可联系项目负责人获取" in markdown
    assert "本地文件" not in markdown
    assert "reports/" not in markdown


def test_wecom_summary_uses_dynamic_budget_before_ellipsis():
    long_summary = (
        "AI 基建需求从 GPU 继续外溢到 custom silicon、EDA/IP、HBM 和先进封装，"
        "对研发团队最直接的动作是把高速接口 IP、验证、多物理场和合规风险放进同一张跟踪表。"
    )
    report = Report(
        kind=ReportKind.WEEKLY,
        title="weekly",
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        sections=[
            ReportSection(
                title="上周最重要 10 件事",
                events=[
                    EventCandidate(title=f"重点{i}", summary=long_summary)
                    for i in range(1, 11)
                ],
            )
        ],
        markdown="# weekly",
        token_usage=TokenUsage(),
    )

    markdown = build_wecom_weekly_summary_markdown(report, max_items=10)

    assert len(markdown.encode("utf-8")) <= 3900
    assert markdown.count("…") < 10
    assert "高速接口 IP、验证、多物理场和合规风险" in markdown


def test_wecom_preparation_removes_windows_local_paths():
    markdown = _prepare_wecom_markdown(
        "完整周报已发邮箱；本地文件为 C:\\path\\to\\inforP\\reports\\2026-08-10_weekly_remote.md。"
    )

    assert markdown == "完整周报已发邮箱。"
    assert "D:\\" not in markdown
    assert "本地文件" not in markdown


def test_wecom_truncation_is_utf8_byte_safe():
    content = "标题\n" + "中文内容" * 2000
    truncated = _truncate_wecom_markdown(content, limit=300)

    assert len(truncated.encode("utf-8")) <= 300
    assert "完整周报已发邮箱，可联系项目负责人获取" in truncated


def test_recipient_set_uses_active_recipients_by_default(tmp_path):
    path = tmp_path / "recipients.yaml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "recipient_sets:",
                "  company_colleagues:",
                "    name: 公司同事情报邮件组",
                "    recipients:",
                "      - email: alice@example.com",
                "        active: true",
                "      - email: bob@example.com",
                "        active: false",
            ]
        ),
        encoding="utf-8",
    )

    assert active_recipient_emails(path, "company_colleagues") == ["alice@example.com"]


def test_recipient_set_only_can_select_inactive_recipient(tmp_path):
    path = tmp_path / "recipients.yaml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "recipient_sets:",
                "  company_colleagues:",
                "    name: 公司同事情报邮件组",
                "    recipients:",
                "      - email: alice@example.com",
                "        active: true",
                "      - email: bob@example.com",
                "        active: false",
            ]
        ),
        encoding="utf-8",
    )

    assert active_recipient_emails(
        path,
        "company_colleagues",
        only=["bob@example.com"],
    ) == ["bob@example.com"]
