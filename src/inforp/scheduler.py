from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from inforp.config import Settings
from inforp.pipeline import build_pipeline


def run_scheduler(settings: Settings) -> None:
    scheduler = BlockingScheduler(timezone=settings.timezone)

    def run_daily() -> None:
        build_pipeline(settings).run_daily()

    def run_weekly() -> None:
        build_pipeline(settings).run_weekly()

    scheduler.add_job(
        run_daily,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=settings.timezone),
        id="weekday_daily_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_weekly,
        CronTrigger(day_of_week="mon", hour=9, minute=8, timezone=settings.timezone),
        id="monday_weekly_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

