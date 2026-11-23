from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from inforp.schemas import EventCandidate, Report, ReportKind, ReportSection, TokenUsage, TopicType


SectionSelector = TopicType | tuple[TopicType, ...] | str


DAILY_SECTIONS = [
    ("30 秒读完", "brief"),
    ("AI 技术与产品应用", (TopicType.AI_TECH, TopicType.GENERAL_AI)),
    ("AI 基础设施与硬件供应链", (TopicType.AI_HARDWARE,)),
    (
        "政策、产业资本与宏观地缘",
        (
            TopicType.AI_INDUSTRY_POLICY,
            TopicType.GLOBAL_POLITICS,
            TopicType.ECONOMY,
            TopicType.CHINA_POLICY,
            TopicType.MARKET_WATCH,
        ),
    ),
    ("待核验线索 / 观察池", "watchlist"),
    ("周末需盯 / 下周一回看", "weekend_watch"),
    ("覆盖范围与遗漏风险", "coverage"),
    ("来源索引", "sources"),
]

WEEKLY_SECTIONS = [
    ("上周最重要 10 件事", "top"),
    ("AI 技术与产品应用主线", (TopicType.AI_TECH, TopicType.GENERAL_AI)),
    ("AI 基础设施与硬件供应链主线", (TopicType.AI_HARDWARE,)),
    (
        "政策、产业资本与宏观地缘主线",
        (
            TopicType.AI_INDUSTRY_POLICY,
            TopicType.GLOBAL_POLITICS,
            TopicType.ECONOMY,
            TopicType.CHINA_POLICY,
            TopicType.MARKET_WATCH,
        ),
    ),
    ("待核验线索复查 / 周末补充", "watchlist"),
    ("下周观察清单", "next_week"),
    ("覆盖范围与遗漏风险", "coverage"),
    ("来源索引", "sources"),
]


def build_report(
    kind: ReportKind,
    events: list[EventCandidate],
    generated_at: datetime,
    timezone_name: str,
    token_usage: TokenUsage,
    metadata: dict,
) -> Report:
    local_now = generated_at.astimezone(ZoneInfo(timezone_name))
    title = f"inforP {'日报' if kind == ReportKind.DAILY else '周报'} - {local_now:%Y-%m-%d}"
    section_defs = DAILY_SECTIONS if kind == ReportKind.DAILY else WEEKLY_SECTIONS
    sections = _build_sections(section_defs, events, kind, local_now, metadata)
    markdown = _render_markdown(title, local_now, sections, token_usage, metadata, events)
    return Report(
        kind=kind,
        title=title,
        generated_at=generated_at,
        sections=sections,
        markdown=markdown,
        token_usage=token_usage,
        metadata=metadata,
    )


def _build_sections(
    section_defs: list[tuple[str, SectionSelector]],
    events: list[EventCandidate],
    kind: ReportKind,
    generated_at: datetime,
    metadata: dict,
) -> list[ReportSection]:
    sections: list[ReportSection] = []
    used: set[str] = set()
    top_limit = 10 if kind == ReportKind.WEEKLY else 8

    for title, selector in section_defs:
        body = None
        if selector == "brief":
            selected = [event for event in events if not event.is_watchlist][:5]
        elif selector == "top":
            selected = [event for event in events if not event.is_watchlist][:top_limit]
        elif selector == "watchlist":
            selected = [event for event in events if event.is_watchlist][:8]
        elif selector == "weekend_watch":
            selected = []
            body = _build_weekend_watch_body(kind, generated_at, events)
        elif selector == "next_week":
            selected = []
            body = _build_next_week_body(events)
        elif selector == "coverage":
            selected = []
            body = _build_coverage_body(kind, generated_at, metadata)
        elif selector == "sources":
            selected = []
        elif isinstance(selector, tuple):
            selected = [event for event in events if event.topic in selector and not event.is_watchlist][:8]
        else:
            selected = [event for event in events if event.topic == selector and not event.is_watchlist][:8]
        deduped = []
        for event in selected:
            key = event.title.strip().lower()
            if selector not in {"brief", "top"} and key in used:
                continue
            deduped.append(event)
            if selector not in {"brief", "top"}:
                used.add(key)
        sections.append(ReportSection(title=title, events=deduped, body=body))

    return sections


def _render_markdown(
    title: str,
    generated_at: datetime,
    sections: list[ReportSection],
    token_usage: TokenUsage,
    metadata: dict,
    events: list[EventCandidate],
) -> str:
    source_index = _build_source_index(events)
    lines: list[str] = [
        f"# {title}",
        "",
        f"- 生成时间：{generated_at:%Y-%m-%d %H:%M:%S} {_timezone_label(generated_at)}",
        f"- 原始条目：{metadata.get('raw_items', 0)}",
        f"- 候选事件：{metadata.get('events_total', 0)}",
        f"- 推送事件：{metadata.get('events_pushed', 0)}",
        f"- 观察池：{metadata.get('watchlist_events', 0)}",
        f"- AI 低置信度过滤：{metadata.get('low_confidence_ai_filtered', 0)}",
        f"- 未展开（已入库，超出正文上限）：{metadata.get('events_overflow', 0)}",
        f"- Token：input={token_usage.input_tokens}, output={token_usage.output_tokens}, total={token_usage.total_tokens}",
        "",
    ]

    for section in sections:
        lines.extend([f"## {section.title}", ""])
        if section.title == "来源索引":
            lines.extend(_render_source_index(source_index))
            lines.append("")
            continue
        if section.body:
            lines.extend([section.body, ""])
        if not section.events:
            if not section.body:
                lines.extend(["暂无。", ""])
            continue
        for index, event in enumerate(section.events, start=1):
            if section.title == "30 秒读完":
                lines.extend(_render_brief_event(index, event, source_index))
            else:
                lines.extend(_render_event(index, event, source_index))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _build_source_index(events: list[EventCandidate]) -> dict[str, str]:
    source_index: dict[str, str] = {}
    for event in events:
        for url in event.source_urls:
            clean_url = url.strip()
            if not clean_url or clean_url in source_index:
                continue
            source_index[clean_url] = f"S{len(source_index) + 1}"
    return source_index


def _timezone_label(generated_at: datetime) -> str:
    offset = generated_at.strftime("%z")
    if len(offset) == 5:
        offset = f"UTC{offset[:3]}:{offset[3:]}"
    else:
        offset = generated_at.tzname() or "UTC"
    zone_name = getattr(generated_at.tzinfo, "key", None) or generated_at.tzname() or "local"
    return f"{zone_name} / {offset}"


def _render_brief_event(index: int, event: EventCandidate, source_index: dict[str, str] | None = None) -> list[str]:
    tag_text = f" [{' / '.join(event.tags[:4])}]" if event.tags else ""
    summary = _compact_text(event.summary, 180)
    refs = _source_refs(event, source_index)
    ref_text = f" {refs}" if refs else ""
    return [
        f"{index}. **{event.title}**{tag_text}：{summary}（重要性 {event.importance_score:.2f}；置信度 {event.confidence.value}）{ref_text}"
    ]


def _render_event(index: int, event: EventCandidate, source_index: dict[str, str] | None = None) -> list[str]:
    tag_text = f" [{' / '.join(event.tags)}]" if event.tags else ""
    lines = [
        f"{index}. **{event.title}**{tag_text}",
        f"   - 详细摘要：{event.summary}",
        f"   - 重要性：{event.importance_score:.2f}；置信度：{event.confidence.value}",
    ]
    if event.verification_notes:
        lines.append(f"   - 核验：{event.verification_notes}")
    if event.claims:
        lines.append(f"   - 关键断言：{'；'.join(event.claims[:5])}")
    if event.contradictions:
        lines.append(f"   - 分歧/反证：{'；'.join(event.contradictions[:5])}")
    if event.source_urls:
        lines.append(f"   - 来源：{_source_refs(event, source_index) or '；'.join(event.source_urls[:5])}")
    return lines


def _source_refs(event: EventCandidate, source_index: dict[str, str] | None) -> str:
    if not source_index:
        return ""
    refs = []
    for url in event.source_urls:
        source_id = source_index.get(url.strip())
        if source_id:
            refs.append(f"[{source_id}]")
    return " ".join(refs[:5])


def _render_source_index(source_index: dict[str, str]) -> list[str]:
    if not source_index:
        return ["暂无可索引来源。"]
    lines: list[str] = []
    for url, source_id in source_index.items():
        lines.extend([f"[{source_id}] {url}", ""])
    return lines[:-1]


def _build_weekend_watch_body(kind: ReportKind, generated_at: datetime, events: list[EventCandidate]) -> str:
    if kind != ReportKind.DAILY:
        return _build_next_week_body(events)
    if generated_at.weekday() != 4:
        return "今天不是周五；保留本节用于记录临近周末或下周一需要复查的高优先级事项。"

    candidates = [event for event in events if event.is_watchlist][:3]
    if not candidates:
        candidates = [event for event in events if not event.is_watchlist][:3]
    if not candidates:
        return "周末重点复查：暂无可从本次候选事件自动提取的事项。"

    lines = ["周末重点复查："]
    for event in candidates:
        lines.append(f"- {event.title}：关注官方公告、监管原文、财报/公告、技术文档或两家可靠独立来源是否补齐。")
    return "\n".join(lines)


def _build_next_week_body(events: list[EventCandidate]) -> str:
    candidates = [event for event in events if event.importance_score >= 0.65 and not event.is_watchlist][:5]
    if not candidates:
        candidates = [event for event in events if not event.is_watchlist][:5]
    if not candidates:
        return "下周观察清单：暂无可从本次候选事件自动提取的事项。"
    lines = ["下周观察清单："]
    for event in candidates:
        lines.append(f"- {event.title}：复查后续公告、落地节奏、产业影响和 EDA/IP 相关传导。")
    return "\n".join(lines)


def _build_coverage_body(kind: ReportKind, generated_at: datetime, metadata: dict) -> str:
    window = metadata.get("search_window") or ("过去一个自然周" if kind == ReportKind.WEEKLY else "最近 36 小时")
    source_pool = metadata.get(
        "source_pool",
        "sources.yaml 中的 official、paper、media、newsletter、social、forum 分层信源，以及启用时的 Codex/LLM 远程搜索。",
    )
    weak_coverage = metadata.get(
        "weak_coverage",
        "付费金融终端、需要登录的社群/论坛、未被搜索引擎及时收录的中文公众号、闭门会议材料和客户私发通知。",
    )
    low_confidence_rule = metadata.get(
        "low_confidence_rule",
        "AI 低置信传闻不进入正文；综合/市场低置信线索只进入观察池，并说明缺失证据与升级条件。",
    )
    omission_risk = metadata.get(
        "omission_risk",
        "可能遗漏盘后公告、地区性政策细则、付费 transcript、未公开技术文档和搜索索引延迟内容。",
    )
    return "\n".join(
        [
            f"- 搜索时间窗：{window}；生成时间：{generated_at:%Y-%m-%d %H:%M:%S} {_timezone_label(generated_at)}。",
            f"- 主要信源池：{source_pool}",
            f"- 本次未覆盖或弱覆盖区域：{weak_coverage}",
            f"- 低置信来源处理规则：{low_confidence_rule}",
            f"- 本次可能遗漏的风险：{omission_risk}",
        ]
    )


def _compact_text(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"
