from __future__ import annotations

import asyncio
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models import AdminUser
from app.services.auth import is_admin_username
from app.services.pipeline import PipelineRunner
from app.services.settings import get_settings


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
    def __init__(self, runner: PipelineRunner, session_factory=SessionLocal) -> None:
        self.runner = runner
        self.session_factory = session_factory
        self.scheduler: AsyncIOScheduler | None = None

    def start(self, settings: dict[str, Any] | None = None) -> None:
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo("UTC"))
        self.reschedule_all()
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    async def _run_scheduled_for_user(self, username: str) -> None:
        await self.runner.run_once(trigger="scheduled", owner_username=username)

    def _list_usernames(self) -> list[str]:
        with self.session_factory() as db:
            rows = db.scalars(select(AdminUser.username).order_by(AdminUser.username)).all()
            return [str(item).strip() for item in rows if str(item).strip()]

    def reschedule_all(self) -> None:
        if not self.scheduler:
            return
        self.scheduler.remove_all_jobs()
        usernames = self._list_usernames()

        with self.session_factory() as db:
            global_settings = get_settings(db)
            blocked = {
                str(item).strip()
                for item in global_settings.get("auth_blocked_usernames", [])
                if str(item).strip()
            }
            for username in usernames:
                settings = get_settings(db, username=username)
                if username in blocked and not is_admin_username(username):
                    continue

                enabled = bool(settings.get("schedule_enabled", True))
                if not enabled:
                    continue

                cron = str(settings.get("schedule_cron", "0 8 * * *"))
                trigger_args = _parse_cron(cron)
                timezone = str(settings.get("timezone", "Asia/Shanghai"))
                try:
                    zone = ZoneInfo(timezone)
                except Exception:
                    zone = ZoneInfo("UTC")

                self.scheduler.add_job(
                    func=lambda username=username: asyncio.create_task(self._run_scheduled_for_user(username)),
                    trigger="cron",
                    id=f"daily-podcast-job:{username}",
                    replace_existing=True,
                    timezone=zone,
                    **trigger_args,
                )
