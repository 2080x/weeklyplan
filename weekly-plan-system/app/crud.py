from __future__ import annotations

import datetime as dt
import calendar
import json
from typing import Iterable, Optional

from sqlalchemy import and_, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import models
from app.security import hash_password, new_session_token, verify_password
from app.settings import settings
from app.utils import iso_week_period


def ensure_schema(db: Session) -> None:
    models.Base.metadata.create_all(bind=db.get_bind())
    _ensure_sqlite_migrations(db)


def _ensure_sqlite_migrations(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return

    def _table_columns(table_name: str) -> set[str]:
        rows = db.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
        return {r["name"] for r in rows}

    def _add_column_if_missing(table_name: str, column_name: str, ddl: str) -> None:
        if column_name in _table_columns(table_name):
            return
        db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
        db.commit()

    _add_column_if_missing("users", "team_id", "team_id INTEGER")
    _migrate_dept_to_team(db)
    _migrate_week_period_month(db)


def _migrate_dept_to_team(db: Session) -> None:
    """
    历史版本里 users.dept 作为“部门/备注”，后续以 team（团队）统一表示“部门”。
    将 team_id 为空的用户，把 dept 的值迁移为 team 并写入 team_id，同时清空 dept。
    """
    users = list(
        db.scalars(
            select(models.User).where(
                models.User.dept.is_not(None),
            )
        )
    )
    changed = False
    for u in users:
        dept = (u.dept or "").strip()
        if not dept:
            continue
        if u.team_id is None:
            team = db.scalar(select(models.Team).where(models.Team.name == dept))
            if not team:
                team = models.Team(name=dept, enabled=True)
                db.add(team)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    team = db.scalar(select(models.Team).where(models.Team.name == dept))
            if team:
                u.team_id = team.id
                changed = True
        # 不再使用 dept 字段，统一由 team 表示“部门”
        u.dept = None
        changed = True
    if changed:
        db.commit()


def _migrate_week_period_month(db: Session) -> None:
    """
    week_period.month 由“周内的周四”所属月份决定（用于跨月周的归属展示）。
    """
    rows = db.execute(select(models.WeekPeriod)).scalars().all()
    changed = False
    for p in rows:
        anchor = p.start_date + dt.timedelta(days=3)
        month = int(anchor.month)
        if p.month != month:
            p.month = month
            changed = True
    if changed:
        db.commit()


def ensure_initial_data(db: Session) -> None:
    ensure_admin_user(db)
    ensure_default_dicts(db)


def ensure_admin_user(db: Session) -> None:
    existing = db.scalar(select(models.User).where(models.User.username == settings.init_admin_username))
    if existing:
        return
    user = models.User(
        username=settings.init_admin_username,
        name=settings.init_admin_name,
        role="admin",
        password_hash=hash_password(settings.init_admin_password),
    )
    db.add(user)
    db.commit()


def ensure_default_dicts(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(models.CategoryDict)) > 0:
        return
    categories = [
        ("一键报警", ["系统维护"]),
        ("一键报警按钮", ["项目回款"]),
        ("警力上图", ["设备维护"]),
        ("安全网关", ["采集网安全接入", "通道费用跟进"]),
        ("极界安全用电管理项目", ["项目推进"]),
        ("松阳、龙泉问政", ["项目配合"]),
        ("松阳RFID智慧头盔项目", ["项目推进"]),
        ("莲都区视频全资产项目", ["项目测试"]),
        ("公司运维", ["日常工作"]),
    ]
    for sort_no, (cat_name, subs) in enumerate(categories, start=1):
        cat = models.CategoryDict(name=cat_name, sort_no=sort_no, enabled=True)
        db.add(cat)
        db.flush()
        for sub_sort, sub in enumerate(subs, start=1):
            db.add(models.SubProjectDict(category_id=cat.id, name=sub, sort_no=sub_sort, enabled=True))
    db.commit()


def authenticate_user(db: Session, username: str, password: str) -> Optional[models.User]:
    user = db.scalar(select(models.User).where(models.User.username == username))
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: Session, user: models.User, *, ttl_hours: int = 24 * 14) -> models.UserSession:
    token = new_session_token()
    session = models.UserSession(
        token=token, user_id=user.id, expires_at=dt.datetime.utcnow() + dt.timedelta(hours=ttl_hours)
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_user_by_session_token(db: Session, token: Optional[str]) -> Optional[models.User]:
    if not token:
        return None
    now = dt.datetime.utcnow()
    sess = db.scalar(select(models.UserSession).where(and_(models.UserSession.token == token, models.UserSession.expires_at > now)))
    if not sess:
        return None
    return sess.user


def delete_session(db: Session, token: Optional[str]) -> None:
    if not token:
        return
    sess = db.scalar(select(models.UserSession).where(models.UserSession.token == token))
    if not sess:
        return
    db.delete(sess)
    db.commit()


def list_teams(db: Session, *, include_disabled: bool = False) -> list[models.Team]:
    stmt = select(models.Team)
    if not include_disabled:
        stmt = stmt.where(models.Team.enabled == True)  # noqa: E712
    stmt = stmt.order_by(models.Team.name, models.Team.id)
    return list(db.scalars(stmt))


def create_team(db: Session, *, name: str) -> models.Team:
    name = name.strip()
    if not name:
        raise ValueError("team_name_required")
    existing = db.scalar(select(models.Team).where(models.Team.name == name))
    if existing:
        return existing
    team = models.Team(name=name, enabled=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def list_users(db: Session) -> list[models.User]:
    return list(db.scalars(select(models.User).order_by(models.User.id)))


def create_user(
    db: Session,
    *,
    username: str,
    name: str,
    password: str,
    role: str = "user",
    dept: Optional[str] = None,
    team_id: Optional[int] = None,
) -> models.User:
    username = username.strip()
    name = name.strip()
    if not username or not name or not password:
        raise ValueError("user_fields_required")
    if role not in {"user", "admin"}:
        raise ValueError("invalid_role")
    existing = db.scalar(select(models.User).where(models.User.username == username))
    if existing:
        raise ValueError("username_exists")
    user = models.User(
        username=username,
        name=name,
        dept=(dept.strip() if dept else None),
        team_id=team_id,
        role=role,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    *,
    user_id: int,
    name: Optional[str] = None,
    dept: Optional[str] = None,
    team_id: Optional[int] = None,
    role: Optional[str] = None,
    new_password: Optional[str] = None,
) -> Optional[models.User]:
    user = db.get(models.User, user_id)
    if not user:
        return None
    if name is not None:
        user.name = name.strip()
    if dept is not None:
        user.dept = dept.strip() if dept.strip() else None
    user.team_id = team_id
    if role is not None:
        if role not in {"user", "admin"}:
            raise ValueError("invalid_role")
        user.role = role
    if new_password:
        user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user


def add_operation_log(
    db: Session,
    *,
    user_id: Optional[int],
    action: str,
    object_type: Optional[str] = None,
    object_id: Optional[int] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra: Optional[dict] = None,
) -> models.OperationLog:
    log = models.OperationLog(
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        method=method,
        path=path,
        ip=ip,
        user_agent=user_agent,
        extra_json=(json.dumps(extra, ensure_ascii=False) if extra is not None else None),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def list_operation_logs(
    db: Session,
    *,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[models.OperationLog]:
    stmt = (
        select(models.OperationLog)
        .options(selectinload(models.OperationLog.user))
        .order_by(models.OperationLog.created_at.desc(), models.OperationLog.id.desc())
    )
    if user_id is not None:
        stmt = stmt.where(models.OperationLog.user_id == user_id)
    if action:
        stmt = stmt.where(models.OperationLog.action == action)
    if offset and offset > 0:
        stmt = stmt.offset(offset)
    stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def ensure_period(db: Session, for_date: dt.date) -> models.WeekPeriod:
    year, week_no, start_date, end_date = iso_week_period(for_date)
    existing = db.scalar(select(models.WeekPeriod).where(and_(models.WeekPeriod.year == year, models.WeekPeriod.week_no == week_no)))
    if existing:
        anchor = start_date + dt.timedelta(days=3)
        month = int(anchor.month)
        if existing.month != month:
            existing.month = month
            db.commit()
        return existing
    period = models.WeekPeriod(
        year=year,
        month=(start_date + dt.timedelta(days=3)).month,
        week_no=week_no,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(period)
    db.commit()
    db.refresh(period)
    return period


def ensure_month_periods(db: Session, *, year: int, month: int) -> list[models.WeekPeriod]:
    first_day = dt.date(year, month, 1)
    last_day = dt.date(year, month, calendar.monthrange(year, month)[1])
    monday = first_day - dt.timedelta(days=first_day.isoweekday() - 1)
    periods_by_id: dict[int, models.WeekPeriod] = {}
    while monday <= last_day:
        p = ensure_period(db, monday)
        periods_by_id[p.id] = p
        monday += dt.timedelta(days=7)
    return sorted(periods_by_id.values(), key=lambda x: x.start_date)


def get_user_plans_by_period_ids(
    db: Session, *, owner_user_id: int, period_ids: list[int]
) -> dict[int, models.WeeklyPlan]:
    if not period_ids:
        return {}
    rows = db.scalars(
        select(models.WeeklyPlan).where(
            and_(models.WeeklyPlan.owner_user_id == owner_user_id, models.WeeklyPlan.period_id.in_(period_ids))
        )
    )
    return {p.period_id: p for p in rows}


def ensure_weekly_plan(db: Session, *, period_id: int, owner_user_id: int) -> models.WeeklyPlan:
    existing = db.scalar(
        select(models.WeeklyPlan).where(
            and_(models.WeeklyPlan.period_id == period_id, models.WeeklyPlan.owner_user_id == owner_user_id)
        )
    )
    if existing:
        return existing
    plan = models.WeeklyPlan(period_id=period_id, owner_user_id=owner_user_id, status="draft")
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def list_my_plans(db: Session, owner_user_id: int, *, limit: int = 40) -> list[models.WeeklyPlan]:
    return list(
        db.scalars(
            select(models.WeeklyPlan)
            .where(models.WeeklyPlan.owner_user_id == owner_user_id)
            .order_by(models.WeeklyPlan.updated_at.desc())
            .limit(limit)
        )
    )


def get_plan(db: Session, plan_id: int) -> Optional[models.WeeklyPlan]:
    return db.get(models.WeeklyPlan, plan_id)


def list_recent_plans(db: Session, *, limit: int = 30) -> list[models.WeeklyPlan]:
    stmt = (
        select(models.WeeklyPlan)
        .order_by(models.WeeklyPlan.updated_at.desc().nulls_last(), models.WeeklyPlan.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def list_categories(db: Session) -> list[models.CategoryDict]:
    return list(db.scalars(select(models.CategoryDict).where(models.CategoryDict.enabled == True).order_by(models.CategoryDict.sort_no, models.CategoryDict.id)))


def list_subprojects(db: Session, *, category_id: Optional[int] = None) -> list[models.SubProjectDict]:
    stmt = select(models.SubProjectDict).where(models.SubProjectDict.enabled == True)
    if category_id is not None:
        stmt = stmt.where(models.SubProjectDict.category_id == category_id)
    stmt = stmt.order_by(models.SubProjectDict.sort_no, models.SubProjectDict.id)
    return list(db.scalars(stmt))


def create_category(db: Session, *, name: str) -> models.CategoryDict:
    name = name.strip()
    if not name:
        raise ValueError("category_name_required")
    existing = db.scalar(
        select(models.CategoryDict).where(models.CategoryDict.name == name, models.CategoryDict.enabled == True)  # noqa: E712
    )
    if existing:
        return existing
    sort_no = (db.scalar(select(func.max(models.CategoryDict.sort_no))) or 0) + 1
    cat = models.CategoryDict(name=name, sort_no=sort_no, enabled=True)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def create_subproject(db: Session, *, category_id: int, name: str) -> models.SubProjectDict:
    name = name.strip()
    if not name:
        raise ValueError("subproject_name_required")
    cat = db.get(models.CategoryDict, category_id)
    if not cat:
        raise ValueError("category_not_found")
    existing = db.scalar(
        select(models.SubProjectDict).where(
            models.SubProjectDict.category_id == category_id,
            models.SubProjectDict.name == name,
            models.SubProjectDict.enabled == True,  # noqa: E712
        )
    )
    if existing:
        return existing
    sort_no = (db.scalar(select(func.max(models.SubProjectDict.sort_no)).where(models.SubProjectDict.category_id == category_id)) or 0) + 1
    sub = models.SubProjectDict(category_id=category_id, name=name, sort_no=sort_no, enabled=True)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def list_plan_templates(db: Session) -> list[models.PlanTemplate]:
    return list(db.scalars(select(models.PlanTemplate).order_by(models.PlanTemplate.updated_at.desc(), models.PlanTemplate.id.desc())))


def get_plan_template(db: Session, template_id: int) -> Optional[models.PlanTemplate]:
    return db.get(models.PlanTemplate, template_id)


def create_plan_template_from_plan(
    db: Session, *, plan_id: int, name: str, created_by_user_id: Optional[int]
) -> models.PlanTemplate:
    name = name.strip()
    if not name:
        raise ValueError("template_name_required")
    existing = db.scalar(select(models.PlanTemplate).where(models.PlanTemplate.name == name))
    if existing:
        raise ValueError("template_name_exists")

    plan = db.get(models.WeeklyPlan, plan_id)
    if not plan:
        raise ValueError("plan_not_found")

    tpl = models.PlanTemplate(name=name, created_by_user_id=created_by_user_id)
    db.add(tpl)
    db.flush()

    for idx, item in enumerate(sorted(plan.items, key=lambda x: x.sort_no), start=1):
        t_item = models.PlanTemplateItem(
            template_id=tpl.id,
            category_id=item.category_id,
            sub_project_id=item.sub_project_id,
            weekly_goal=item.weekly_goal,
            progress_percent=item.progress_percent,
            progress_text=item.progress_text,
            detail_text=item.detail_text,
            estimated_hours=float(item.estimated_hours) if item.estimated_hours is not None else None,
            sort_no=idx,
        )
        db.add(t_item)
        db.flush()
        for d_idx, d in enumerate(sorted(item.details, key=lambda x: x.sort_no), start=1):
            db.add(
                models.PlanTemplateItemDetail(
                    item_id=t_item.id,
                    content=d.content,
                    hours=float(d.hours) if d.hours is not None else None,
                    sort_no=d_idx,
                )
            )

    db.commit()
    db.refresh(tpl)
    return tpl


def delete_plan_template(db: Session, *, template_id: int) -> None:
    tpl = db.get(models.PlanTemplate, template_id)
    if not tpl:
        return
    db.delete(tpl)
    db.commit()


def apply_plan_template_to_plan(db: Session, *, plan_id: int, template_id: int, mode: str = "append") -> None:
    plan = db.get(models.WeeklyPlan, plan_id)
    tpl = db.get(models.PlanTemplate, template_id)
    if not plan or not tpl:
        raise ValueError("not_found")
    if mode not in {"append", "replace"}:
        raise ValueError("invalid_mode")

    if mode == "replace":
        plan.items.clear()
        db.flush()

    max_sort = db.scalar(select(func.coalesce(func.max(models.PlanItem.sort_no), 0)).where(models.PlanItem.plan_id == plan.id)) or 0
    for offset, t_item in enumerate(sorted(tpl.items, key=lambda x: x.sort_no), start=1):
        item = models.PlanItem(
            plan_id=plan.id,
            category_id=t_item.category_id,
            sub_project_id=t_item.sub_project_id,
            weekly_goal=t_item.weekly_goal,
            progress_percent=t_item.progress_percent,
            progress_text=t_item.progress_text,
            detail_text=t_item.detail_text,
            estimated_hours=t_item.estimated_hours,
            sort_no=max_sort + offset,
        )
        db.add(item)
        db.flush()
        for d_idx, d in enumerate(sorted(t_item.details, key=lambda x: x.sort_no), start=1):
            item.details.append(models.PlanItemDetail(content=d.content, hours=d.hours, sort_no=d_idx))

    db.commit()


def add_item(
    db: Session,
    *,
    plan_id: int,
    category_id: Optional[int],
    sub_project_id: Optional[int],
    weekly_goal: str,
    progress_percent: Optional[int],
    progress_text: Optional[str],
    detail_text: Optional[str],
    estimated_hours: Optional[float],
) -> models.PlanItem:
    max_sort = db.scalar(select(func.coalesce(func.max(models.PlanItem.sort_no), 0)).where(models.PlanItem.plan_id == plan_id)) or 0
    item = models.PlanItem(
        plan_id=plan_id,
        category_id=category_id,
        sub_project_id=sub_project_id,
        weekly_goal=weekly_goal.strip(),
        progress_percent=progress_percent,
        progress_text=(progress_text.strip() if progress_text else None),
        detail_text=(detail_text.strip() if detail_text else None),
        estimated_hours=estimated_hours,
        sort_no=max_sort + 1,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(
    db: Session,
    *,
    item_id: int,
    category_id: Optional[int],
    sub_project_id: Optional[int],
    weekly_goal: str,
    progress_percent: Optional[int],
    progress_text: Optional[str],
    detail_text: Optional[str],
    estimated_hours: Optional[float],
) -> Optional[models.PlanItem]:
    item = db.get(models.PlanItem, item_id)
    if not item:
        return None
    item.category_id = category_id
    item.sub_project_id = sub_project_id
    item.weekly_goal = weekly_goal.strip()
    item.progress_percent = progress_percent
    item.progress_text = (progress_text.strip() if progress_text else None)
    item.detail_text = (detail_text.strip() if detail_text else None)
    item.estimated_hours = estimated_hours
    db.commit()
    db.refresh(item)
    return item


def delete_item(db: Session, *, item_id: int) -> None:
    item = db.get(models.PlanItem, item_id)
    if not item:
        return
    db.delete(item)
    db.commit()


def replace_item_details(
    db: Session,
    *,
    item_id: int,
    details: Iterable[tuple[str, Optional[float]]],
) -> list[models.PlanItemDetail]:
    item = db.get(models.PlanItem, item_id)
    if not item:
        return []
    item.details.clear()
    for idx, (content, hours) in enumerate(details, start=1):
        c = content.strip()
        if not c:
            continue
        item.details.append(models.PlanItemDetail(content=c, hours=hours, sort_no=idx))
    db.commit()
    db.refresh(item)
    return item.details


def set_plan_status(db: Session, *, plan_id: int, status: str) -> Optional[models.WeeklyPlan]:
    plan = db.get(models.WeeklyPlan, plan_id)
    if not plan:
        return None
    plan.status = status
    db.commit()
    db.refresh(plan)
    return plan


def sum_plan_hours(db: Session, *, plan_id: int) -> float:
    plan = db.get(models.WeeklyPlan, plan_id)
    if not plan:
        return 0.0
    total = 0.0
    for item in plan.items:
        if item.details:
            for d in item.details:
                if d.hours is None:
                    continue
                total += float(d.hours)
        else:
            total += float(item.estimated_hours or 0)
    return float(total)


def get_plan_item_stats(db: Session, plan_ids: list[int]) -> dict[int, dict[str, float | int]]:
    """
    返回每个周计划的工时与条目数统计。
    - estimated：预计工时合计（直接累加 estimated_hours，为空按 0）
    - actual：实际工时合计（有明细则累加明细 hours，明细为空则 fallback 到 estimated_hours）
    - items：条目数量
    """
    if not plan_ids:
        return {}
    stats: dict[int, dict[str, float | int]] = {pid: {"estimated": 0.0, "actual": 0.0, "items": 0} for pid in plan_ids}
    items = db.scalars(
        select(models.PlanItem)
        .where(models.PlanItem.plan_id.in_(plan_ids))
        .options(selectinload(models.PlanItem.details))
    ).all()
    for it in items:
        s = stats.setdefault(it.plan_id, {"estimated": 0.0, "actual": 0.0, "items": 0})
        est = float(it.estimated_hours or 0)
        s["estimated"] += est
        actual = 0.0
        if it.details:
            for d in it.details:
                if d.hours is None:
                    continue
                actual += float(d.hours)
        else:
            actual = est
        s["actual"] += actual
        s["items"] += 1
    # 四舍五入到 1 位
    for v in stats.values():
        v["estimated"] = round(float(v["estimated"]), 1)
        v["actual"] = round(float(v["actual"]), 1)
    return stats


def list_team_plans(
    db: Session,
    *,
    year: Optional[int] = None,
    week_no: Optional[int] = None,
    team_id: Optional[int] = None,
    owner_user_id: Optional[int] = None,
    category_id: Optional[int] = None,
    sub_project_id: Optional[int] = None,
    limit: int = 200,
) -> list[models.WeeklyPlan]:
    stmt = select(models.WeeklyPlan).join(models.WeekPeriod).join(models.User)
    if year is not None:
        stmt = stmt.where(models.WeekPeriod.year == year)
    if week_no is not None:
        stmt = stmt.where(models.WeekPeriod.week_no == week_no)
    if team_id is not None:
        stmt = stmt.where(models.User.team_id == team_id)
    if owner_user_id is not None:
        stmt = stmt.where(models.WeeklyPlan.owner_user_id == owner_user_id)
    if category_id is not None or sub_project_id is not None:
        stmt = stmt.join(models.PlanItem, models.PlanItem.plan_id == models.WeeklyPlan.id)
        if category_id is not None:
            stmt = stmt.where(models.PlanItem.category_id == category_id)
        if sub_project_id is not None:
            stmt = stmt.where(models.PlanItem.sub_project_id == sub_project_id)
        stmt = stmt.distinct()
    stmt = stmt.order_by(models.WeekPeriod.year.desc(), models.WeekPeriod.week_no.desc(), models.WeeklyPlan.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt))
