from __future__ import annotations

import os
import smtplib
import imaplib
from email import policy
from email.message import EmailMessage
from pathlib import Path
import re

import httpx
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

from inforp.config import Settings
from inforp.recipients import active_recipient_emails, parse_email_list
from inforp.schemas import DeliveryResult, EventCandidate, Report, ReportKind

NO_OUTBOUND_ENV = "INFORP_NO_OUTBOUND"
_TRUTHY = {"1", "true", "yes", "on"}


def outbound_disabled() -> bool:
    """Hard switch that stops every network delivery.

    This is deliberately read from the environment on each call rather than from Settings:
    it must also cover CLI paths that construct a delivery directly, and test runs must be
    able to switch it on without editing .env.
    """
    return os.environ.get(NO_OUTBOUND_ENV, "").strip().lower() in _TRUTHY


def _skipped(channel: str) -> DeliveryResult:
    return DeliveryResult(channel=channel, ok=True, detail=f"skipped: outbound disabled ({NO_OUTBOUND_ENV})")


class Delivery:
    channel = "base"

    def send(self, report: Report) -> DeliveryResult:
        raise NotImplementedError


class CodexFileDelivery(Delivery):
    channel = "codex_file"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def send(self, report: Report) -> DeliveryResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{report.generated_at:%Y-%m-%d}_{report.kind.value}.md"
        path = self.output_dir / filename
        path.write_text(report.markdown, encoding="utf-8")
        return DeliveryResult(channel=self.channel, ok=True, detail=str(path))


class WeComDelivery(Delivery):
    channel = "wecom"

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, report: Report) -> DeliveryResult:
        if outbound_disabled():
            return _skipped(self.channel)
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": _prepare_wecom_markdown(report.markdown)},
        }
        response = httpx.post(self.webhook_url, json=payload, timeout=20)
        if response.is_success:
            return DeliveryResult(channel=self.channel, ok=True, detail=response.text)
        return DeliveryResult(channel=self.channel, ok=False, detail=f"{response.status_code}: {response.text}")


WECOM_WEEKLY_SUMMARY_FOOTER = "完整周报已发邮箱，可联系项目负责人获取。"
WECOM_MARKDOWN_LIMIT_BYTES = 3900
WECOM_WEEKLY_SUMMARY_TARGET_BYTES = 3860


class WeComWeeklySummaryDelivery(Delivery):
    channel = "wecom_weekly_summary"

    def __init__(self, webhook_url: str, top_items: int = 10):
        self.webhook_url = webhook_url
        self.top_items = max(1, min(top_items, 10))

    def send(self, report: Report) -> DeliveryResult:
        if report.kind != ReportKind.WEEKLY:
            return DeliveryResult(channel=self.channel, ok=True, detail="skipped: not weekly")
        if outbound_disabled():
            return _skipped(self.channel)
        markdown = build_wecom_weekly_summary_markdown(report, max_items=self.top_items)
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": _prepare_wecom_markdown(markdown)},
        }
        response = httpx.post(self.webhook_url, json=payload, timeout=20)
        if response.is_success:
            return DeliveryResult(channel=self.channel, ok=True, detail=response.text)
        return DeliveryResult(channel=self.channel, ok=False, detail=f"{response.status_code}: {response.text}")


class EmailDelivery(Delivery):
    channel = "email"

    def __init__(
        self,
        settings: Settings,
        recipients: list[str] | str | None = None,
        cc_recipients: list[str] | str | None = None,
    ):
        self.settings = settings
        self.recipients = parse_email_list(recipients) if recipients is not None else parse_email_list(settings.email_to)
        self.cc_recipients = parse_email_list(cc_recipients)

    def send(self, report: Report) -> DeliveryResult:
        if outbound_disabled():
            return _skipped(self.channel)
        required = [
            self.settings.smtp_host,
            self.settings.smtp_user,
            self.settings.smtp_password,
            self.settings.email_from,
            self.recipients,
        ]
        if not all(required):
            return DeliveryResult(channel=self.channel, ok=False, detail="SMTP settings are incomplete")

        message = EmailMessage(policy=policy.SMTP)
        message["Subject"] = _subject_for_report(report)
        message["From"] = self.settings.email_from
        message["To"] = ", ".join(self.recipients)
        if self.cc_recipients:
            message["Cc"] = ", ".join(self.cc_recipients)
        message.set_content(report.markdown, subtype="plain", charset="utf-8")
        message.add_alternative(_markdown_to_email_html(report.markdown), subtype="html", charset="utf-8")

        try:
            if self.settings.smtp_port == 465:
                smtp_context = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port)
            else:
                smtp_context = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port)

            with smtp_context as smtp:
                if self.settings.smtp_port != 465:
                    smtp.starttls()
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
                refused = smtp.send_message(message)
                if refused:
                    return DeliveryResult(channel=self.channel, ok=False, detail=f"refused recipients: {refused}")
            sent_copy_detail = self._append_sent_copy(message)
        except (OSError, smtplib.SMTPException) as exc:
            return DeliveryResult(channel=self.channel, ok=False, detail=f"{type(exc).__name__}: {exc}")
        return DeliveryResult(channel=self.channel, ok=True, detail=sent_copy_detail or "")

    def _append_sent_copy(self, message: EmailMessage) -> str | None:
        if not self.settings.imap_sent_copy_enabled:
            return None
        host = self.settings.imap_host or _infer_imap_host(self.settings.smtp_host)
        user = self.settings.imap_user or self.settings.smtp_user
        password = self.settings.imap_password or self.settings.smtp_password
        if not host or not user or not password:
            return "sent copy skipped: IMAP settings are incomplete"

        try:
            with imaplib.IMAP4_SSL(host, self.settings.imap_port) as imap:
                imap.login(user, password)
                mailbox = self.settings.imap_sent_mailbox or _find_sent_mailbox(imap)
                if not mailbox:
                    return "sent copy skipped: IMAP Sent mailbox not found"
                status, response = imap.append(mailbox, "\\Seen", None, message.as_bytes(policy=policy.SMTP))
                if status != "OK":
                    return f"sent copy failed: {response!r}"
        except (OSError, imaplib.IMAP4.error) as exc:
            return f"sent copy failed: {type(exc).__name__}: {exc}"
        return "sent copy saved"


def build_deliveries(settings: Settings) -> list[Delivery]:
    deliveries: list[Delivery] = []
    for channel in settings.channel_list:
        if channel == "codex_file":
            deliveries.append(CodexFileDelivery(settings.report_output_dir))
        elif channel == "wecom":
            if settings.wecom_webhook_url:
                deliveries.append(WeComDelivery(settings.wecom_webhook_url))
        elif channel == "email":
            if settings.email_recipient_set:
                recipients = active_recipient_emails(settings.recipients_file, settings.email_recipient_set)
                deliveries.append(EmailDelivery(settings, recipients=recipients))
            else:
                deliveries.append(EmailDelivery(settings))
    return deliveries


def deliver_report(report: Report, deliveries: list[Delivery]) -> list[DeliveryResult]:
    return [delivery.send(report) for delivery in deliveries]


def deliver_weekly_wecom_summary(report: Report, settings: Settings) -> list[DeliveryResult]:
    if report.kind != ReportKind.WEEKLY:
        return []
    if not settings.wecom_weekly_summary_enabled or not settings.wecom_webhook_url:
        return []
    delivery = WeComWeeklySummaryDelivery(
        webhook_url=settings.wecom_webhook_url,
        top_items=settings.wecom_weekly_summary_top_items,
    )
    return [delivery.send(report)]


def build_wecom_weekly_summary_markdown(report: Report, max_items: int = 10) -> str:
    items = _extract_weekly_summary_items(report, max_items=max_items)
    date_text = report.generated_at.strftime("%Y-%m-%d")
    heading = f"# inforP 周报重点 - {date_text}"
    if not items:
        return _remove_local_file_references(
            "\n".join([heading, "", "本周暂无可推送重点。", "", WECOM_WEEKLY_SUMMARY_FOOTER])
        )

    return _remove_local_file_references(_format_wecom_weekly_summary(heading, items))


def _format_wecom_weekly_summary(heading: str, items: list[dict[str, str]]) -> str:
    lines = [heading, ""]
    titles = [
        _compact_plain_text_bytes(item["title"], _wecom_title_budget(index, len(items)))
        for index, item in enumerate(items, start=1)
    ]
    title_lines = [f"{index}. **{title}**" for index, title in enumerate(titles, start=1)]
    summaries = [_focus_wecom_summary(item["summary"]) for item in items]

    fixed_lines: list[str] = [heading, ""]
    for title_line in title_lines:
        fixed_lines.extend([title_line, "   要点："])
    fixed_lines.extend(["", WECOM_WEEKLY_SUMMARY_FOOTER])
    remaining_bytes = max(
        WECOM_WEEKLY_SUMMARY_TARGET_BYTES - len("\n".join(fixed_lines).encode("utf-8")),
        len(items) * 48,
    )
    summary_budgets = _allocate_wecom_summary_budgets(
        summaries=summaries,
        remaining_bytes=remaining_bytes,
    )

    for index, (title, summary, budget) in enumerate(zip(titles, summaries, summary_budgets), start=1):
        lines.extend(
            [
                f"{index}. **{title}**",
                f"   要点：{_compact_plain_text_bytes(summary, budget)}",
            ]
        )
    lines.extend(["", WECOM_WEEKLY_SUMMARY_FOOTER])
    return _truncate_wecom_markdown("\n".join(lines), limit=WECOM_MARKDOWN_LIMIT_BYTES)


def _allocate_wecom_summary_budgets(summaries: list[str], remaining_bytes: int) -> list[int]:
    if not summaries:
        return []
    weights = [1.55, 1.35, 1.2, 1.05, 0.95]
    if len(summaries) > len(weights):
        weights.extend([0.82] * (len(summaries) - len(weights)))
    weights = weights[: len(summaries)]
    total_weight = sum(weights)
    budgets = [max(54, int(remaining_bytes * weight / total_weight)) for weight in weights]

    unused = 0
    for index, summary in enumerate(summaries):
        needed = len(summary.encode("utf-8"))
        if needed < budgets[index]:
            unused += budgets[index] - needed
            budgets[index] = needed
    if unused <= 0:
        return budgets

    for index, summary in enumerate(summaries):
        needed = len(summary.encode("utf-8"))
        if needed <= budgets[index]:
            continue
        extra = min(needed - budgets[index], unused)
        budgets[index] += extra
        unused -= extra
        if unused <= 0:
            break
    return budgets


def _wecom_title_budget(index: int, total_items: int) -> int:
    if total_items <= 5:
        return 240
    return 204 if index <= 5 else 162


def _extract_weekly_summary_items(report: Report, max_items: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for section in report.sections:
        if "最重要" not in section.title and "30 秒" not in section.title:
            continue
        for event in section.events:
            if event.is_watchlist:
                continue
            items.append(_event_to_wecom_item(event))
            if len(items) >= max_items:
                return items
    if items:
        return items
    return _extract_markdown_summary_items(report.markdown, max_items=max_items)


def _event_to_wecom_item(event: EventCandidate) -> dict[str, str]:
    return {
        "title": _strip_markdown(event.title),
        "summary": _compact_plain_text(event.summary, 150),
    }


def _extract_markdown_summary_items(markdown: str, max_items: int) -> list[dict[str, str]]:
    for headings in (["上周最重要"], ["30 秒读完"]):
        section = _find_markdown_section(markdown, headings)
        items = _extract_numbered_summary_items(section, max_items=max_items)
        if items:
            return items
    return _extract_numbered_summary_items(markdown, max_items=max_items)


def _extract_numbered_summary_items(markdown: str, max_items: int) -> list[dict[str, str]]:
    pattern = re.compile(r"(?m)^(\d+)\.\s+(.+)$")
    matches = list(pattern.finditer(markdown))
    items: list[dict[str, str]] = []
    for index, match in enumerate(matches[:max_items]):
        title, inline_tail = _split_numbered_item_heading(match.group(2))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block = markdown[start:end]
        items.append({"title": title, "summary": _summary_from_markdown_item(inline_tail, block)})
    return items


def _find_markdown_section(markdown: str, headings: list[str]) -> str:
    lines = markdown.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("## ") and any(heading in line for heading in headings):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _summary_from_markdown_block(block: str) -> str:
    fields: dict[str, str] = {}
    candidates: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        content = stripped.removeprefix("- ").strip()
        match = re.match(r"^(事件|事实|重要性|影响|详细摘要|摘要)[：:]\s*(.+)$", content)
        if match:
            fields[match.group(1)] = match.group(2).strip()
            candidates.append(content)
    if not candidates:
        candidates = [line.strip("- ").strip() for line in block.splitlines() if line.strip()]

    # Prefer real prose. 重要性 is a score line and must never stand in for the summary
    # unless it is the only field present.
    if "详细摘要" in fields:
        summary = fields["详细摘要"]
    elif "摘要" in fields:
        summary = fields["摘要"]
    elif "影响" in fields:
        summary = fields["影响"]
    elif "事件" in fields:
        summary = fields["事件"]
    elif "事实" in fields:
        summary = fields["事实"]
    elif "重要性" in fields:
        summary = fields["重要性"]
    else:
        summary = _strip_markdown(candidates[0]) if candidates else "详见邮件周报。"
        summary = re.sub(r"^(重要性|影响|事件|事实|详细摘要|摘要)[：:]\s*", "", summary)
    return _focus_wecom_summary(summary)


def _summary_from_markdown_item(inline_tail: str, block: str) -> str:
    summary = _summary_from_inline_numbered_tail(inline_tail)
    if summary:
        return _focus_wecom_summary(summary)
    return _summary_from_markdown_block(block)


def _summary_from_inline_numbered_tail(inline_tail: str) -> str:
    clean = _strip_markdown(inline_tail).strip()
    # A bare tag bracket ("[OpenAI / 财报 / 多源确认]") is metadata, not a summary. When the
    # item line carries only that, fall back to the block below it, which holds 详细摘要.
    if not clean or re.fullmatch(r"(?:\[[^\]]*\]\s*)+", clean):
        return ""
    clean = re.sub(r"^\s*(?:\[[^\]]+\]\s*)*[：:]\s*", "", clean)
    clean = re.sub(r"^\s*\[[^\]]+\]\s*[：:]\s*", "", clean)
    clean = clean.strip()
    if not clean:
        return ""
    clean = re.sub(r"\s*\[[Ss]\d+\](?:\s*\[[Ss]\d+\])*\s*$", "", clean)
    clean = re.sub(r"事实置信度[：:].*$", "", clean)
    clean = re.sub(r"影响判断置信度[：:].*$", "", clean)
    return clean.strip("。；;，, ")


def _split_numbered_item_heading(line: str) -> tuple[str, str]:
    clean = line.strip()
    bold = re.match(r"^\*\*(.+?)\*\*(.*)$", clean)
    if bold:
        return _strip_markdown(bold.group(1)), bold.group(2)

    without_sources = re.sub(r"\s*\[[Ss]\d+\](?:\s*\[[Ss]\d+\])*\s*$", "", clean).strip()
    split = re.search(r"[：:；;。]", without_sources)
    if split:
        title = without_sources[: split.start()].strip()
        inline_tail = without_sources[split.start() :]
    else:
        title = without_sources
        inline_tail = f"：{without_sources}"
    return _strip_markdown(title), inline_tail


def _wecom_summary_limit(index: int, max_items: int) -> int:
    if max_items <= 5:
        return 150
    return 105 if index <= 5 else 55


def _focus_wecom_summary(text: str) -> str:
    clean = _strip_markdown(text)
    clean = re.sub(r"^(重要性|影响|事件|详细摘要|摘要)[：:]\s*", "", clean)
    clean = re.sub(
        r"[。；;]?\s*(?:建议|建议动作|事实置信度|影响判断置信度|核验状态|来源)[：:]?.*$",
        "",
        clean,
    )
    return clean.strip("。；;，, ") or "详见邮件周报"


def _prepare_wecom_markdown(markdown: str, limit: int = 3900) -> str:
    return _truncate_wecom_markdown(_remove_local_file_references(markdown), limit=limit)


def _remove_local_file_references(markdown: str) -> str:
    cleaned_lines: list[str] = []
    for line in markdown.splitlines():
        cleaned = _remove_local_file_reference_from_line(line)
        if cleaned is not None:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def _remove_local_file_reference_from_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return line
    if _is_standalone_local_file_reference(stripped):
        return None

    indent = line[: len(line) - len(line.lstrip())]
    content = line.lstrip()
    content = re.sub(
        r"[；;，,]?\s*(?:本地文件|本地路径)(?:为|是)?[:：]?\s*[^。；;\n]*[。.]?",
        "",
        content,
    )
    content = re.sub(
        r"(?<!https://)(?<!http://)(?:[A-Za-z]:[\\/][^\s，。；;）)]+|(?:\.{1,2}[\\/])?reports[\\/][^\s，。；;）)]+)",
        "",
        content,
    )
    content = re.sub(r"\s+([。；;，,])", r"\1", content)
    content = re.sub(r"[；;，,]\s*。", "。", content)
    content = content.strip()
    if content == "完整周报已发邮箱":
        content = "完整周报已发邮箱。"
    return f"{indent}{content}" if content else None


def _is_standalone_local_file_reference(text: str) -> bool:
    if "http://" in text or "https://" in text:
        return False
    if re.fullmatch(r"(?:本地文件|本地路径)(?:为|是)?[:：]?\s*.*", text):
        return True
    return bool(
        re.fullmatch(
            r"(?:[A-Za-z]:[\\/].+|(?:\.{1,2}[\\/])?reports[\\/].+)",
            text,
        )
    )


def _truncate_wecom_markdown(markdown: str, limit: int = 3900) -> str:
    if len(markdown.encode("utf-8")) <= limit:
        return markdown
    suffix = f"\n\n内容较长，已截断；{WECOM_WEEKLY_SUMMARY_FOOTER}"
    suffix_bytes = suffix.encode("utf-8")
    allowed = max(limit - len(suffix_bytes), 0)
    truncated = markdown.encode("utf-8")[:allowed].decode("utf-8", errors="ignore").rstrip()
    return truncated + suffix


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`>#]", "", text)
    return " ".join(text.split())


def _compact_plain_text(text: str, limit: int) -> str:
    clean = " ".join(_strip_markdown(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _compact_plain_text_bytes(text: str, limit: int) -> str:
    clean = " ".join(_strip_markdown(text).split())
    if len(clean.encode("utf-8")) <= limit:
        return clean
    suffix = "…"
    allowed = max(limit - len(suffix.encode("utf-8")), 0)
    return clean.encode("utf-8")[:allowed].decode("utf-8", errors="ignore").rstrip() + suffix


def _subject_for_report(report: Report) -> str:
    title = report.title.strip()
    heading = _first_markdown_heading(report.markdown)
    if heading and _looks_corrupted_subject(title):
        return heading
    return title


def _first_markdown_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        if line.startswith("# "):
            heading = line.removeprefix("# ").strip()
            if heading:
                return heading
    return None


def _looks_corrupted_subject(title: str) -> bool:
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", title))
    return "??" in title or (title.count("?") >= 2 and not has_cjk)


def _infer_imap_host(smtp_host: str | None) -> str | None:
    if not smtp_host:
        return None
    if smtp_host == "smtp.mxhichina.com":
        return "imap.mxhichina.com"
    if smtp_host == "smtp.qiye.aliyun.com":
        return "imap.qiye.aliyun.com"
    if smtp_host == "smtp.exmail.qq.com":
        return "imap.exmail.qq.com"
    if smtp_host.startswith("smtp."):
        return "imap." + smtp_host.removeprefix("smtp.")
    return None


def _find_sent_mailbox(imap: imaplib.IMAP4_SSL) -> str | None:
    status, boxes = imap.list()
    if status != "OK" or not boxes:
        return None
    fallback: str | None = None
    for box in boxes:
        if not box:
            continue
        flags, name = _parse_imap_list_item(box)
        if not name:
            continue
        decoded_name = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name
        if "\\Sent" in flags:
            return decoded_name
        if fallback is None and decoded_name.upper() in {"SENT", "SENT MESSAGES"}:
            fallback = decoded_name
    return fallback


def _parse_imap_list_item(item: bytes | str) -> tuple[str, bytes | str | None]:
    raw = item if isinstance(item, bytes) else item.encode("utf-8", errors="replace")
    match = re.match(br'\((?P<flags>.*?)\)\s+"(?P<delimiter>.*?)"\s+(?P<name>.+)$', raw)
    if not match:
        return "", None
    flags = match.group("flags").decode("ascii", errors="ignore")
    name = match.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = name[1:-1]
    return flags, name.decode("ascii", errors="ignore")


def _markdown_to_email_html(markdown: str) -> str:
    parser = MarkdownIt("commonmark", {"html": False})
    body = _linkify_bare_urls(parser.render(markdown))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f6f7f9;
      color: #1f2937;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.62;
    }}
    .report {{
      max-width: 860px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #e5e7eb;
      padding: 28px;
    }}
    h1 {{ font-size: 24px; margin: 0 0 18px; }}
    h2 {{ font-size: 18px; margin: 28px 0 12px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
    p, li {{ font-size: 14px; }}
    li {{ margin: 8px 0; }}
    a {{ color: #0f5bd7; text-decoration: none; }}
    code {{ background: #f3f4f6; padding: 1px 4px; }}
    strong {{ color: #111827; }}
  </style>
</head>
<body>
  <div class="report">
{body}
  </div>
</body>
</html>
"""


def _linkify_bare_urls(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(r"https?://[^\s<>()]+")
    for text_node in list(soup.find_all(string=pattern)):
        if text_node.parent and text_node.parent.name == "a":
            continue
        text = str(text_node)
        pieces = []
        last = 0
        for match in pattern.finditer(text):
            pieces.append(NavigableString(text[last : match.start()]))
            matched = match.group(0)
            url = matched.rstrip(".,;，。；")
            suffix = matched[len(url) :]
            link = soup.new_tag("a", href=url)
            link.string = url
            pieces.append(link)
            if suffix:
                pieces.append(NavigableString(suffix))
            last = match.end()
        pieces.append(NavigableString(text[last:]))
        for piece in pieces:
            text_node.insert_before(piece)
        text_node.extract()
    return str(soup)
