from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import crud, models
from app.db import SessionLocal
from app.emailer import iter_user_email_configs, save_user_email_config, send_plan_email


def _parse_hhmm(value: str) -> dt.time | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parts = raw.split(":", 1)
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return dt.time(hour=h, minute=m)
    except Exception:
        return None


def try_auto_send_week_plans() -> None:
    """
    在配置的发送时间点，按用户配置自动发送“本周”已提交的周计划到各自默认收件人。
    - 每个用户独立配置与去重（last_auto_sent_key）
    - 发送时间：schedule_weekday + schedule_time（本地时间）
    """
    now = dt.datetime.now()

    with SessionLocal() as db:
        period = crud.ensure_period(db, now.date())
        key = f"{period.year}-W{period.week_no}"
        for user_id, cfg in iter_user_email_configs():
            if not cfg.get("schedule_enabled"):
                continue

            weekday = int(cfg.get("schedule_weekday") or 1)  # 1..7 (ISO)
            send_at = _parse_hhmm(str(cfg.get("schedule_time") or "09:00"))
            if send_at is None:
                continue
            if now.isoweekday() != weekday:
                continue
            if now.time() < send_at:
                continue
            if cfg.get("last_auto_sent_key") == key:
                continue

            plan = db.scalars(
                select(models.WeeklyPlan)
                .where(
                    models.WeeklyPlan.period_id == period.id,
                    models.WeeklyPlan.owner_user_id == user_id,
                    models.WeeklyPlan.status == "submitted",
                )
                .options(
                    selectinload(models.WeeklyPlan.owner),
                    selectinload(models.WeeklyPlan.period),
                    selectinload(models.WeeklyPlan.items).selectinload(models.PlanItem.details),
                    selectinload(models.WeeklyPlan.items).selectinload(models.PlanItem.category),
                    selectinload(models.WeeklyPlan.items).selectinload(models.PlanItem.sub_project),
                )
            ).first()

            try:
                if plan:
                    send_plan_email(plan, cfg)
                    crud.add_operation_log(
                        db,
                        user_id=None,
                        action="email_auto_send",
                        object_type="weekly_plan",
                        object_id=plan.id,
                        method="SCHED",
                        path="auto",
                        extra={"period": key, "owner_user_id": user_id},
                    )
                cfg["last_auto_sent_key"] = key
                cfg["last_auto_sent_at"] = now.isoformat(timespec="seconds")
                save_user_email_config(user_id, cfg)
            except Exception as e:
                try:
                    crud.add_operation_log(
                        db,
                        user_id=None,
                        action="email_auto_send_failed",
                        object_type="weekly_plan",
                        object_id=(plan.id if plan else None),
                        method="SCHED",
                        path="auto",
                        extra={"period": key, "owner_user_id": user_id, "error": str(e)},
                    )
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                cfg["last_auto_sent_key"] = key
                cfg["last_auto_sent_at"] = now.isoformat(timespec="seconds")
                save_user_email_config(user_id, cfg)


async def email_scheduler_loop(*, interval_seconds: int = 30) -> None:
    while True:
        try:
            await asyncio.to_thread(try_auto_send_week_plans)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 定时任务不要影响主服务；错误可通过日志查看
            pass
        await asyncio.sleep(interval_seconds)
