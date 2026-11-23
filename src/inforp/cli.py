from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from inforp.collectors.browser_profile import BrowserProfileConnector
from inforp.config import get_settings
from inforp.delivery import (
    NO_OUTBOUND_ENV,
    EmailDelivery,
    WeComWeeklySummaryDelivery,
    build_wecom_weekly_summary_markdown,
)
from inforp.incidents import build_incident, record_incident
from inforp.pipeline import build_pipeline
from inforp.recipients import active_recipient_emails, load_recipient_config
from inforp.schemas import IncidentSeverity, IncidentStatus, Report, ReportKind, TokenUsage
from inforp.scheduler import run_scheduler
from inforp.store import Store

from datetime import datetime, timezone


app = typer.Typer(help="inforP personal intelligence reporting pipeline")
console = Console()


@app.command("init-db")
def init_db() -> None:
    settings = get_settings()
    store = Store(settings.database_url)
    store.init_db()
    console.print(f"Initialized database: {settings.database_url}")


@app.command("run-daily")
def run_daily(dry_run: bool = False) -> None:
    settings = get_settings()
    if dry_run:
        _enable_no_outbound()
    try:
        report = build_pipeline(settings).run_daily()
        console.print(report.markdown)
    except Exception as exc:
        _record_command_exception(settings, "日报管线运行失败", "pipeline.daily", exc)
        raise


@app.command("run-weekly")
def run_weekly(dry_run: bool = False) -> None:
    settings = get_settings()
    if dry_run:
        _enable_no_outbound()
    try:
        report = build_pipeline(settings).run_weekly()
        console.print(report.markdown)
    except Exception as exc:
        _record_command_exception(settings, "周报管线运行失败", "pipeline.weekly", exc)
        raise


@app.command("schedule")
def schedule() -> None:
    settings = get_settings()
    console.print(f"Starting scheduler in timezone {settings.timezone}")
    run_scheduler(settings)


@app.command("metrics")
def metrics(limit: int = 10) -> None:
    settings = get_settings()
    store = Store(settings.database_url)
    store.init_db()
    rows = store.latest_run_metrics(limit=limit)

    table = Table(title="Recent runs")
    table.add_column("Run ID")
    table.add_column("Kind")
    table.add_column("Started")
    table.add_column("Raw")
    table.add_column("Events")
    table.add_column("Pushed")
    table.add_column("Tokens")
    for row in rows:
        table.add_row(
            row.run_id[:8],
            row.kind,
            row.started_at.isoformat(),
            str(row.raw_items),
            str(row.events_total),
            str(row.events_pushed),
            str(row.token_total),
        )
    console.print(table)


@app.command("incidents")
def incidents(limit: int = 10) -> None:
    settings = get_settings()
    store = Store(settings.database_url)
    store.init_db()
    rows = store.latest_automation_incidents(limit=limit)

    table = Table(title="Automation incidents")
    table.add_column("Incident ID")
    table.add_column("Occurred")
    table.add_column("Severity")
    table.add_column("Status")
    table.add_column("Component")
    table.add_column("Title")
    for row in rows:
        table.add_row(
            row.incident_id[:18],
            row.occurred_at.isoformat(),
            row.severity,
            row.status,
            row.component,
            row.title,
        )
    console.print(table)


@app.command("log-incident")
def log_incident(
    title: str,
    detail: str,
    component: str = "automation",
    severity: str = "error",
    status: str = "open",
    related_file: list[Path] | None = typer.Option(None, "--related-file"),
) -> None:
    settings = get_settings()
    try:
        incident = build_incident(
            title=title,
            detail=detail,
            component=component,
            severity=IncidentSeverity(severity),
            status=IncidentStatus(status),
            detected_by="manual",
            related_files=[str(path) for path in related_file or []],
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    record_incident(settings, incident)
    console.print(f"Recorded incident: {incident.incident_id}")


@app.command("list-recipients")
def list_recipients(include_inactive: bool = True) -> None:
    """List recipient sets and their members.

    The recipient file is gitignored, so this is the quickest way to check who would
    actually receive a report before sending it.
    """
    settings = get_settings()
    config = load_recipient_config(settings.recipients_file)
    if not config.recipient_sets:
        console.print(
            f"No recipient sets found in {settings.recipients_file}. "
            "Copy configs/recipients.example.yaml to configs/recipients.yaml first."
        )
        return

    console.print(f"Recipient file: {settings.recipients_file}")
    for set_id, recipient_set in config.recipient_sets.items():
        members = [r for r in recipient_set.recipients if include_inactive or r.active]
        active_count = sum(1 for r in recipient_set.recipients if r.active)
        console.print(
            f"\n[bold]{set_id}[/bold] — {recipient_set.name} "
            f"({active_count}/{len(recipient_set.recipients)} active)"
        )
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("Email", no_wrap=True)
        table.add_column("Name")
        table.add_column("Active", justify="center")
        table.add_column("Notes")
        for member in members:
            table.add_row(
                member.email,
                member.name or "",
                "yes" if member.active else "no",
                (member.notes or "").replace("\n", " ")[:60],
            )
        console.print(table)


@app.command("render-weekly-wecom-summary")
def render_weekly_wecom_summary(report_path: Path, top_items: int = 10, out: Path | None = None) -> None:
    """Render the WeCom weekly summary text without sending anything.

    Generation and delivery are separate concerns: this command never touches the network.
    Use it to review the message before letting the pipeline post it.
    """
    markdown = report_path.read_text(encoding="utf-8")
    content = Report(
        kind=ReportKind.WEEKLY,
        title=_first_heading(markdown) or f"inforP 周报重点 - {datetime.now(timezone.utc):%Y-%m-%d}",
        generated_at=datetime.now(timezone.utc),
        sections=[],
        markdown=markdown,
        token_usage=TokenUsage(),
        metadata={"source_path": str(report_path)},
    )
    text = build_wecom_weekly_summary_markdown(content, max_items=max(1, min(top_items, 10)))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"Wrote {len(text.encode('utf-8'))} bytes to {out}")
    console.print(text)


@app.command("send-weekly-wecom-summary")
def send_weekly_wecom_summary(report_path: Path, top_items: int = 10) -> None:
    settings = get_settings()
    if not settings.wecom_webhook_url:
        raise typer.BadParameter("WECOM_WEBHOOK_URL is not configured")
    markdown = report_path.read_text(encoding="utf-8")
    title = _first_heading(markdown) or f"inforP 周报重点 - {datetime.now(timezone.utc):%Y-%m-%d}"
    report = Report(
        kind=ReportKind.WEEKLY,
        title=title,
        generated_at=datetime.now(timezone.utc),
        sections=[],
        markdown=markdown,
        token_usage=TokenUsage(),
        metadata={"source_path": str(report_path)},
    )
    result = WeComWeeklySummaryDelivery(settings.wecom_webhook_url, top_items=top_items).send(report)
    if not result.ok:
        _record_incident(
            settings,
            title="企微周报摘要发送失败",
            detail=result.detail,
            component="delivery.wecom_weekly_summary",
            related_files=[str(report_path)],
            context={"report_path": str(report_path), "top_items": top_items},
        )
        raise typer.Exit(code=1)
    console.print(f"Sent weekly WeCom summary: {result.detail}")


@app.command("send-report-email")
def send_report_email(
    report_path: Path,
    recipient_set: str = "company_colleagues",
    only: str | None = None,
) -> None:
    settings = get_settings()
    markdown = report_path.read_text(encoding="utf-8")
    title = _first_heading(markdown) or report_path.stem
    kind = _report_kind_from_path(report_path)
    only_emails = [email.strip() for email in only.split(",")] if only else None
    recipients = active_recipient_emails(settings.recipients_file, recipient_set, only=only_emails)
    if not recipients:
        _record_incident(
            settings,
            title="邮件发送未选中任何活跃收件人",
            detail=f"No active recipients selected from set: {recipient_set}",
            component="delivery.email",
            related_files=[str(report_path), str(settings.recipients_file)],
            context={"recipient_set": recipient_set, "only": only_emails},
        )
        raise typer.BadParameter(f"No active recipients selected from set: {recipient_set}")
    report = Report(
        kind=kind,
        title=title,
        generated_at=datetime.now(timezone.utc),
        sections=[],
        markdown=markdown,
        token_usage=TokenUsage(),
        metadata={"source_path": str(report_path), "recipient_set": recipient_set},
    )
    result = EmailDelivery(settings, recipients=recipients).send(report)
    if result.ok:
        detail = f" ({result.detail})" if result.detail else ""
        console.print(f"Sent {kind.value} email to: {', '.join(recipients)}{detail}")
    else:
        failed = [f"{', '.join(recipients)}: {result.detail}"]
        console.print(f"Failed to send {kind.value} email to {', '.join(recipients)}: {result.detail}")
        _record_incident(
            settings,
            title=f"{kind.value} 邮件发送存在失败收件人",
            detail="; ".join(failed),
            component="delivery.email",
            related_files=[str(report_path), str(settings.recipients_file)],
            context={"recipient_set": recipient_set, "only": only_emails, "failed": failed},
        )
        raise typer.Exit(code=1)


@app.command("bootstrap-browser")
def bootstrap_browser(platform: str, login_url: str, profiles_dir: Path = Path("profiles")) -> None:
    connector = BrowserProfileConnector(profiles_dir=profiles_dir)
    connector.bootstrap_login(platform=platform, login_url=login_url)
    console.print(f"Saved browser profile for {platform}: {connector.profile_path(platform)}")


def _enable_no_outbound() -> None:
    os.environ[NO_OUTBOUND_ENV] = "1"
    console.print(
        f"[yellow]dry-run: {NO_OUTBOUND_ENV}=1[/yellow] — reports are written locally, "
        "email and WeCom delivery are skipped."
    )


def _first_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return None


def _report_kind_from_path(report_path: Path) -> ReportKind:
    name = report_path.name.lower()
    if "weekly" in name:
        return ReportKind.WEEKLY
    return ReportKind.DAILY


def _record_command_exception(settings, title: str, component: str, exc: Exception) -> None:
    _record_incident(
        settings,
        title=title,
        detail=f"{type(exc).__name__}: {exc}",
        component=component,
        context={"exception_type": type(exc).__name__},
    )


def _record_incident(
    settings,
    *,
    title: str,
    detail: str,
    component: str,
    related_files: list[str] | None = None,
    context: dict | None = None,
) -> None:
    incident = build_incident(
        title=title,
        detail=detail,
        component=component,
        severity=IncidentSeverity.ERROR,
        status=IncidentStatus.OPEN,
        detected_by="cli",
        related_files=related_files or [],
        context=context or {},
    )
    record_incident(settings, incident)


if __name__ == "__main__":
    app()
