from __future__ import annotations

import asyncio
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zoneinfo import ZoneInfo

from app.services.pipeline import PipelineRunner


def _parse_cron(expr: str) -> dict[str, str]:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError("Cron 表达式必须是 5 段，例如: 0 8 * * *")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


class SchedulerService:
    def __init__(self, runner: PipelineRunner) -> None:
        self.runner = runner
        self.scheduler: AsyncIOScheduler | None = None

    def start(self, settings: dict[str, Any]) -> None:
        timezone = str(settings.get("timezone", "Asia/Shanghai"))
        try:
            zone = ZoneInfo(timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        self.scheduler = AsyncIOScheduler(timezone=zone)
        self.reschedule(settings)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    async def _run_scheduled(self) -> None:
        await self.runner.run_once(trigger="scheduled")

    def reschedule(self, settings: dict[str, Any]) -> None:
        if not self.scheduler:
            return

        cron = str(settings.get("schedule_cron", "0 8 * * *"))
        trigger_args = _parse_cron(cron)
        timezone = str(settings.get("timezone", "Asia/Shanghai"))
        try:
            zone = ZoneInfo(timezone)
        except Exception:
            zone = ZoneInfo("UTC")

        self.scheduler.remove_all_jobs()
        self.scheduler.add_job(
            func=lambda: asyncio.create_task(self._run_scheduled()),
            trigger="cron",
            id="daily-podcast-job",
            replace_existing=True,
            timezone=zone,
            **trigger_args,
        )
