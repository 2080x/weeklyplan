from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import crud, models
from app.db import get_db
from app.emailer import load_user_email_config, save_user_email_config, send_plan_email
from app.holidays import load_calendar
from app.utils import workday_range


def _require_user(request: Request, db: Session) -> models.User:
    token = request.cookies.get("session")
    user = crud.get_user_by_session_token(db, token)
    if not user:
        raise HTTPException(status_code=401)
    return user


def _require_admin(user: models.User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403)


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    return int(value)

def _log_event(
    db: Session,
    request: Request,
    user: Optional[models.User],
    *,
    action: str,
    object_type: Optional[str] = None,
    object_id: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    try:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        crud.add_operation_log(
            db,
            user_id=(user.id if user else None),
            action=action,
            object_type=object_type,
            object_id=object_id,
            method=request.method,
            path=request.url.path,
            ip=ip,
            user_agent=ua,
            extra=extra,
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _redirect_plan(plan_id: int, *, embed: bool) -> RedirectResponse:
    url = f"/plans/{plan_id}"
    if embed:
        url += "?embed=1"
    return RedirectResponse(url=url, status_code=302)


def register_web_routes(templates: Jinja2Templates) -> APIRouter:
    r = APIRouter()
    base_dir = Path(__file__).resolve().parent.parent  # .../weekly-plan-system

    def _parse_date_lines(value: str) -> list[str]:
        lines: list[str] = []
        for raw in (value or "").splitlines():
            s = raw.split("#", 1)[0].strip()
            if not s:
                continue
            lines.append(s)
        # 去重保持顺序
        seen = set()
        out: list[str] = []
        for x in lines:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    @r.get("/", response_class=HTMLResponse)
    def root(request: Request, db: Session = Depends(get_db)):
        user = crud.get_user_by_session_token(db, request.cookies.get("session"))
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        return RedirectResponse(url="/my", status_code=302)

    @r.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse("login.html", {"request": request})

    @r.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
        user = crud.authenticate_user(db, username=username, password=password)
        if not user:
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "账号或密码错误"}, status_code=400
            )
        sess = crud.create_session(db, user)
        _log_event(db, request, user, action="login", object_type="user", object_id=user.id)
        resp = RedirectResponse(url="/my", status_code=302)
        resp.set_cookie("session", sess.token, httponly=True, samesite="lax")
        return resp

    @r.post("/logout")
    def logout(request: Request, db: Session = Depends(get_db)):
        token = request.cookies.get("session")
        user = crud.get_user_by_session_token(db, token)
        if user:
            _log_event(db, request, user, action="logout", object_type="user", object_id=user.id)
        crud.delete_session(db, token)
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("session")
        return resp

    @r.get("/my", response_class=HTMLResponse)
    def my_plans(
        request: Request,
        ym: Optional[str] = None,
        d: Optional[str] = None,
        year: Optional[str] = None,
        month: Optional[str] = None,
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        today = dt.date.today()
        current_date = today
        if d:
            try:
                current_date = dt.date.fromisoformat(d)
            except ValueError:
                current_date = today

        current_period = crud.ensure_period(db, current_date)
        current_plan = crud.ensure_weekly_plan(db, period_id=current_period.id, owner_user_id=user.id)

        selected_year = today.year
        selected_month = today.month
        if ym:
            try:
                y_s, m_s = ym.split("-", 1)
                selected_year = int(y_s)
                selected_month = int(m_s)
            except ValueError:
                pass
        elif d:
            selected_year = current_date.year
            selected_month = current_date.month
        else:
            selected_year = _parse_int(year) or today.year
            selected_month = _parse_int(month) or today.month

        if selected_month < 1 or selected_month > 12:
            selected_month = today.month
        if selected_year < 1970 or selected_year > 2100:
            selected_year = today.year

        prev_year, prev_month = selected_year, selected_month - 1
        if prev_month == 0:
            prev_year, prev_month = selected_year - 1, 12
        next_year, next_month = selected_year, selected_month + 1
        if next_month == 13:
            next_year, next_month = selected_year + 1, 1

        month_periods_all = crud.ensure_month_periods(db, year=selected_year, month=selected_month)
        month_periods = [p for p in month_periods_all if p.month == selected_month]
        plans_by_period = crud.get_user_plans_by_period_ids(db, owner_user_id=user.id, period_ids=[p.id for p in month_periods])
        holidays, workdays = load_calendar(base_dir=base_dir)

        month_start = dt.date(selected_year, selected_month, 1)
        if selected_month == 12:
            month_end = dt.date(selected_year + 1, 1, 1) - dt.timedelta(days=1)
        else:
            month_end = dt.date(selected_year, selected_month + 1, 1) - dt.timedelta(days=1)

        period_workdays = {}
        for p in month_periods:
            # 工作日范围按 ISO 周周期计算（start_date~end_date），并根据“休息日/补班日”过滤
            ws, we, cnt = workday_range(p.start_date, p.end_date, holidays=holidays, workdays=workdays)
            period_workdays[p.id] = {"start": ws, "end": we, "count": cnt}

        plan_summaries = crud.get_plan_item_stats(db, [p.id for p in plans_by_period.values()])
        return templates.TemplateResponse(
            "my.html",
            {
                "request": request,
                "user": user,
                "current_plan": current_plan,
                "current_period": current_period,
                "month_periods": month_periods,
                "plans_by_period": plans_by_period,
                "period_workdays": period_workdays,
                "plan_summaries": plan_summaries,
                "selected_year": selected_year,
                "selected_month": selected_month,
                "selected_ym": f"{selected_year:04d}-{selected_month:02d}",
                "prev_ym": f"{prev_year:04d}-{prev_month:02d}",
                "next_ym": f"{next_year:04d}-{next_month:02d}",
                "this_ym": f"{today.year:04d}-{today.month:02d}",
                "current_date": current_date,
                "current_date_str": current_date.isoformat(),
            },
        )

    @r.get("/my/open")
    def open_my_plan(request: Request, period_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        plan = crud.ensure_weekly_plan(db, period_id=period_id, owner_user_id=user.id)
        embed = request.query_params.get("embed")
        url = f"/plans/{plan.id}"
        if embed == "1":
            url += "?embed=1"
        return RedirectResponse(url=url, status_code=302)

    @r.get("/plans/{plan_id}", response_class=HTMLResponse)
    def plan_detail(request: Request, plan_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        embed = request.query_params.get("embed") == "1"
        categories = crud.list_categories(db)
        sub_projects = crud.list_subprojects(db)
        total_hours = crud.sum_plan_hours(db, plan_id=plan.id)
        holidays, workdays = load_calendar(base_dir=base_dir)
        wd_ws, wd_we, wd_cnt = workday_range(
            plan.period.start_date, plan.period.end_date, holidays=holidays, workdays=workdays
        )
        item_hours = {}
        for it in plan.items:
            if it.details:
                sum_hours = 0.0
                for d in it.details:
                    if d.hours is None:
                        continue
                    sum_hours += float(d.hours)
            else:
                sum_hours = float(it.estimated_hours or 0)
            item_hours[it.id] = round(float(sum_hours), 1)
        return templates.TemplateResponse(
            "plan.html",
            {
                "request": request,
                "user": user,
                "plan": plan,
                "period": plan.period,
                "embed": embed,
                "categories": categories,
                "sub_projects": sub_projects,
                "total_hours": total_hours,
                "workday_start": wd_ws,
                "workday_end": wd_we,
                "workday_count": wd_cnt,
                "item_hours": item_hours,
            },
        )

    @r.post("/plans/{plan_id}/items")
    def add_item(
        request: Request,
        plan_id: int,
        category_id: Optional[int] = Form(None),
        sub_project_id: Optional[int] = Form(None),
        weekly_goal: str = Form(""),
        progress_mode: str = Form("text"),
        progress_percent: Optional[int] = Form(None),
        progress_text: Optional[str] = Form(None),
        detail_text: Optional[str] = Form(None),
        estimated_hours: Optional[float] = Form(None),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        embed = request.query_params.get("embed") == "1"
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        if progress_mode == "percent":
            progress_text = None
        else:
            progress_percent = None
        item = crud.add_item(
            db,
            plan_id=plan_id,
            category_id=category_id,
            sub_project_id=sub_project_id,
            weekly_goal=weekly_goal,
            progress_percent=progress_percent,
            progress_text=progress_text,
            detail_text=detail_text,
            estimated_hours=estimated_hours,
        )
        _log_event(
            db,
            request,
            user,
            action="item_add",
            object_type="plan_item",
            object_id=item.id,
            extra={"plan_id": plan_id},
        )
        return _redirect_plan(plan_id, embed=embed)

    @r.post("/items/{item_id}/update")
    def update_item(
        request: Request,
        item_id: int,
        plan_id: int = Form(...),
        category_id: Optional[int] = Form(None),
        sub_project_id: Optional[int] = Form(None),
        weekly_goal: str = Form(""),
        progress_mode: str = Form("text"),
        progress_percent: Optional[int] = Form(None),
        progress_text: Optional[str] = Form(None),
        detail_text: Optional[str] = Form(None),
        estimated_hours: Optional[float] = Form(None),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        embed = request.query_params.get("embed") == "1"
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        if progress_mode == "percent":
            progress_text = None
        else:
            progress_percent = None
        item = crud.update_item(
            db,
            item_id=item_id,
            category_id=category_id,
            sub_project_id=sub_project_id,
            weekly_goal=weekly_goal,
            progress_percent=progress_percent,
            progress_text=progress_text,
            detail_text=detail_text,
            estimated_hours=estimated_hours,
        )
        if not item:
            raise HTTPException(status_code=404)
        _log_event(
            db,
            request,
            user,
            action="item_update",
            object_type="plan_item",
            object_id=item_id,
            extra={"plan_id": plan_id},
        )
        return _redirect_plan(plan_id, embed=embed)

    @r.post("/items/{item_id}/delete")
    def delete_item(request: Request, item_id: int, plan_id: int = Form(...), db: Session = Depends(get_db)):
        user = _require_user(request, db)
        embed = request.query_params.get("embed") == "1"
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        crud.delete_item(db, item_id=item_id)
        _log_event(
            db,
            request,
            user,
            action="item_delete",
            object_type="plan_item",
            object_id=item_id,
            extra={"plan_id": plan_id},
        )
        return _redirect_plan(plan_id, embed=embed)

    @r.post("/plans/{plan_id}/submit")
    def submit_plan(request: Request, plan_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        embed = request.query_params.get("embed") == "1"
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        crud.set_plan_status(db, plan_id=plan_id, status="submitted")
        _log_event(db, request, user, action="plan_submit", object_type="weekly_plan", object_id=plan_id)
        return _redirect_plan(plan_id, embed=embed)

    @r.post("/plans/{plan_id}/copy-prev")
    def copy_prev_plan(request: Request, plan_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        embed = request.query_params.get("embed") == "1"
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)

        prev_date = plan.period.start_date - dt.timedelta(days=7)
        prev_period = crud.ensure_period(db, prev_date)
        prev_plan = db.scalar(
            select(models.WeeklyPlan).where(
                models.WeeklyPlan.period_id == prev_period.id,
                models.WeeklyPlan.owner_user_id == plan.owner_user_id,
            )
        )
        if not prev_plan or not prev_plan.items:
            _log_event(
                db,
                request,
                user,
                action="plan_copy_prev_empty",
                object_type="weekly_plan",
                object_id=plan_id,
                extra={"prev_period_id": prev_period.id, "prev_plan_id": (prev_plan.id if prev_plan else None)},
            )
            return _redirect_plan(plan_id, embed=embed)

        def _flatten_details_text(item: models.PlanItem) -> str:
            if item.details:
                lines = []
                for idx, d in enumerate(sorted(item.details, key=lambda x: x.sort_no), start=1):
                    lines.append(f"{idx}. {d.content}")
                return "\n".join(lines)
            return item.detail_text or ""

        base_sort = (
            db.scalar(select(func.coalesce(func.max(models.PlanItem.sort_no), 0)).where(models.PlanItem.plan_id == plan_id))
            or 0
        )
        for offset, it in enumerate(sorted(prev_plan.items, key=lambda x: x.sort_no), start=1):
            db.add(
                models.PlanItem(
                    plan_id=plan_id,
                    category_id=it.category_id,
                    sub_project_id=it.sub_project_id,
                    weekly_goal=it.weekly_goal,
                    progress_percent=it.progress_percent,
                    progress_text=it.progress_text,
                    detail_text=_flatten_details_text(it) or None,
                    estimated_hours=float(it.estimated_hours) if it.estimated_hours is not None else None,
                    sort_no=base_sort + offset,
                )
            )
        db.commit()
        _log_event(
            db,
            request,
            user,
            action="plan_copy_prev",
            object_type="weekly_plan",
            object_id=plan_id,
            extra={"prev_plan_id": prev_plan.id, "count": len(prev_plan.items)},
        )
        return _redirect_plan(plan_id, embed=embed)

    @r.get("/plans/{plan_id}/export.xlsx")
    def export_xlsx(request: Request, plan_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        from app.exporter import export_plan_xlsx
        from urllib.parse import quote

        content = export_plan_xlsx(plan)
        filename_utf8 = f"周计划_{plan.owner.name}_{plan.period.year}_W{plan.period.week_no}.xlsx"
        filename_ascii = f"weekly_plan_{plan.id}_{plan.period.year}_W{plan.period.week_no}.xlsx"
        content_disposition = f"attachment; filename=\"{filename_ascii}\"; filename*=UTF-8''{quote(filename_utf8)}"
        headers = {"Content-Disposition": content_disposition}
        _log_event(db, request, user, action="plan_export", object_type="weekly_plan", object_id=plan_id)
        return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

    @r.post("/plans/{plan_id}/send-email")
    def send_email_endpoint(request: Request, plan_id: int, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        plan = crud.get_plan(db, plan_id)
        if not plan:
            raise HTTPException(status_code=404)
        if user.role != "admin" and plan.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        cfg = load_user_email_config(user.id)
        try:
            send_plan_email(plan, cfg)
        except Exception as e:
            _log_event(
                db,
                request,
                user,
                action="email_send_failed",
                object_type="weekly_plan",
                object_id=plan_id,
                extra={"error": str(e)},
            )
            hint = "；请先到「邮箱配置」填写 SMTP 信息" if "邮件配置不完整" in str(e) else ""
            return {"ok": False, "message": f"发送失败：{e}{hint}"}
        _log_event(db, request, user, action="email_send", object_type="weekly_plan", object_id=plan_id)
        return {"ok": True, "message": "邮件已发送"}

    @r.get("/team", response_class=HTMLResponse)
    def team_view(
        request: Request,
        d: Optional[str] = None,
        year: Optional[str] = None,
        week_no: Optional[str] = None,
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        today = dt.date.today()
        selected_date = today
        if d:
            try:
                selected_date = dt.date.fromisoformat(d)
            except ValueError:
                selected_date = today

        year_i = _parse_int(year)
        week_no_i = _parse_int(week_no)
        if year_i and week_no_i:
            try:
                selected_date = dt.date.fromisocalendar(year_i, week_no_i, 1)
            except ValueError:
                selected_date = today

        period = crud.ensure_period(db, selected_date)
        holidays, workdays = load_calendar(base_dir=base_dir)
        wd_ws, wd_we, wd_cnt = workday_range(period.start_date, period.end_date, holidays=holidays, workdays=workdays)
        prev_week_date = period.start_date - dt.timedelta(days=7)
        next_week_date = period.start_date + dt.timedelta(days=7)

        teams = crud.list_teams(db, include_disabled=False)
        team_ids = [t.id for t in teams]
        members = (
            db.scalars(select(models.User).where(models.User.team_id.in_(team_ids)).order_by(models.User.team_id, models.User.id)).all()
            if team_ids
            else []
        )
        users_by_team: dict[int, list[models.User]] = {}
        for m in members:
            if not m.team_id:
                continue
            users_by_team.setdefault(int(m.team_id), []).append(m)

        plans: list[models.WeeklyPlan] = []
        if team_ids:
            plans = (
                db.scalars(
                    select(models.WeeklyPlan)
                    .join(models.User, models.WeeklyPlan.owner_user_id == models.User.id)
                    .where(models.WeeklyPlan.period_id == period.id, models.User.team_id.in_(team_ids))
                    .options(
                        selectinload(models.WeeklyPlan.owner).selectinload(models.User.team),
                        selectinload(models.WeeklyPlan.period),
                    )
                    .order_by(models.User.team_id, models.WeeklyPlan.updated_at.desc())
                )
                .all()
            )
        stats = crud.get_plan_item_stats(db, [p.id for p in plans])

        team_cards = []
        for t in teams:
            team_users = users_by_team.get(t.id, [])
            user_cnt = len(team_users)
            team_plans = [p for p in plans if p.owner and p.owner.team_id == t.id]
            planned_user_ids = {p.owner_user_id for p in team_plans}
            registered_cnt = 0
            est_sum = 0.0
            act_sum = 0.0
            last_updated = None
            for p in team_plans:
                s = stats.get(p.id) or {}
                if int(s.get("items", 0) or 0) > 0:
                    registered_cnt += 1
                est_sum += float(s.get("estimated", 0.0) or 0.0)
                act_sum += float(s.get("actual", 0.0) or 0.0)
                if p.updated_at and (last_updated is None or p.updated_at > last_updated):
                    last_updated = p.updated_at
            missing_cnt = max(user_cnt - len(planned_user_ids), 0)
            team_cards.append(
                {
                    "team": t,
                    "user_cnt": user_cnt,
                    "registered_cnt": registered_cnt,
                    "missing_cnt": missing_cnt,
                    "estimated_sum": round(est_sum, 1),
                    "actual_sum": round(act_sum, 1),
                    "last_updated": last_updated,
                }
            )
        return templates.TemplateResponse(
            "team.html",
            {
                "request": request,
                "user": user,
                "period": period,
                "workday_start": wd_ws,
                "workday_end": wd_we,
                "workday_count": wd_cnt,
                "selected_date_str": selected_date.isoformat(),
                "today_str": today.isoformat(),
                "prev_week_date_str": prev_week_date.isoformat(),
                "next_week_date_str": next_week_date.isoformat(),
                "team_cards": team_cards,
            },
        )

    @r.get("/team/{team_id}", response_class=HTMLResponse)
    def team_detail(
        request: Request,
        team_id: int,
        period_id: Optional[int] = None,
        d: Optional[str] = None,
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        team = db.get(models.Team, team_id)
        if not team or not team.enabled:
            raise HTTPException(status_code=404)

        today = dt.date.today()
        selected_date = today
        if d:
            try:
                selected_date = dt.date.fromisoformat(d)
            except ValueError:
                selected_date = today

        if period_id:
            period = db.get(models.WeekPeriod, period_id)
            if not period:
                raise HTTPException(status_code=404)
            selected_date = period.start_date
        else:
            period = crud.ensure_period(db, selected_date)
            selected_date = period.start_date

        holidays, workdays = load_calendar(base_dir=base_dir)
        wd_ws, wd_we, wd_cnt = workday_range(period.start_date, period.end_date, holidays=holidays, workdays=workdays)

        members = db.scalars(select(models.User).where(models.User.team_id == team.id).order_by(models.User.id)).all()
        member_ids = [m.id for m in members]
        plans: list[models.WeeklyPlan] = []
        if member_ids:
            plans = (
                db.scalars(
                    select(models.WeeklyPlan)
                    .where(models.WeeklyPlan.period_id == period.id, models.WeeklyPlan.owner_user_id.in_(member_ids))
                    .options(selectinload(models.WeeklyPlan.owner), selectinload(models.WeeklyPlan.period))
                )
                .all()
            )
        plans_by_user = {p.owner_user_id: p for p in plans}

        created = False
        for m in members:
            if m.id in plans_by_user:
                continue
            p = models.WeeklyPlan(period_id=period.id, owner_user_id=m.id, status="draft")
            db.add(p)
            db.flush()
            plans_by_user[m.id] = p
            created = True
        if created:
            db.commit()

        all_plans = list(plans_by_user.values())
        stats = crud.get_plan_item_stats(db, [p.id for p in all_plans])

        return templates.TemplateResponse(
            "team_detail.html",
            {
                "request": request,
                "user": user,
                "team": team,
                "period": period,
                "workday_start": wd_ws,
                "workday_end": wd_we,
                "workday_count": wd_cnt,
                "members": members,
                "plans_by_user": plans_by_user,
                "plan_summaries": stats,
                "selected_date_str": selected_date.isoformat(),
            },
        )

    @r.get("/admin/users", response_class=HTMLResponse)
    def users_page(request: Request, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        users = crud.list_users(db)
        teams = crud.list_teams(db, include_disabled=True)
        return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": users, "teams": teams})

    @r.get("/admin/logs", response_class=HTMLResponse)
    def logs_page(
        request: Request,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        page: Optional[str] = None,
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        user_id_i = _parse_int(user_id)
        page_i = _parse_int(page) or 1
        if page_i < 1:
            page_i = 1
        page_size = 50
        offset = (page_i - 1) * page_size
        fetched = crud.list_operation_logs(
            db,
            user_id=user_id_i,
            action=(action.strip() if action else None),
            limit=page_size + 1,
            offset=offset,
        )
        has_next = len(fetched) > page_size
        logs = fetched[:page_size]
        users = crud.list_users(db)
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "user": user,
                "logs": logs,
                "users": users,
                "filters": {"user_id": user_id_i, "action": (action.strip() if action else ""), "page": page_i},
                "pagination": {"page": page_i, "page_size": page_size, "has_next": has_next},
            },
        )

    @r.get("/admin/email-config", response_class=HTMLResponse)
    def email_config_page(request: Request, db: Session = Depends(get_db)):
        _require_user(request, db)
        return RedirectResponse(url="/email-config", status_code=302)

    @r.get("/email-config", response_class=HTMLResponse)
    def user_email_config_page(request: Request, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        cfg = load_user_email_config(user.id)
        return templates.TemplateResponse(
            "email_config.html",
            {
                "request": request,
                "user": user,
                "cfg": cfg,
                "page_title": "邮箱配置",
            },
        )

    @r.get("/admin/holidays", response_class=HTMLResponse)
    def holidays_page(request: Request, message: Optional[str] = None, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        cal_path = base_dir / "data" / "calendar.json"
        holidays_text = ""
        workdays_text = ""
        year = dt.date.today().year
        try:
            if cal_path.exists():
                import json

                data = json.loads(cal_path.read_text(encoding="utf-8"))
                holidays_text = "\n".join(data.get("holidays") or [])
                workdays_text = "\n".join(data.get("workdays") or [])
        except Exception:
            pass
        return templates.TemplateResponse(
            "holidays.html",
            {
                "request": request,
                "user": user,
                "holidays_text": holidays_text,
                "workdays_text": workdays_text,
                "message": message,
                "year": year,
            },
        )

    @r.post("/admin/holidays", response_class=HTMLResponse)
    def holidays_save(
        request: Request,
        holidays_text: str = Form(""),
        workdays_text: str = Form(""),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        cal_path = base_dir / "data" / "calendar.json"
        cal_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "holidays": _parse_date_lines(holidays_text),
            "workdays": _parse_date_lines(workdays_text),
            "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
        }
        import json

        cal_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _log_event(db, request, user, action="calendar_save", object_type="calendar", extra={"holidays": len(payload["holidays"]), "workdays": len(payload["workdays"])})
        return RedirectResponse(url="/admin/holidays", status_code=302)

    @r.post("/admin/holidays/sync", response_class=HTMLResponse)
    def holidays_sync(request: Request, year: str = Form(...), db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        year_i = _parse_int(year) or dt.date.today().year
        cal_path = base_dir / "data" / "calendar.json"
        cal_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import json
            import urllib.request

            url = f"https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year_i}.json"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            _log_event(db, request, user, action="calendar_sync_failed", object_type="calendar", extra={"year": year_i, "error": str(e)})
            return RedirectResponse(url="/admin/holidays?message=同步失败（可能无法联网），请手动维护", status_code=302)

        holidays: set[str] = set()
        workdays: set[str] = set()

        # 兼容 holiday-cn：days 为 dict 或 list
        days = data.get("days")
        if isinstance(days, dict):
            for k, v in days.items():
                if not isinstance(k, str):
                    continue
                is_off = False
                if isinstance(v, dict):
                    is_off = bool(v.get("isOffDay"))
                elif isinstance(v, bool):
                    is_off = bool(v)
                if is_off:
                    holidays.add(k)
                else:
                    try:
                        if dt.date.fromisoformat(k).isoweekday() >= 6:
                            workdays.add(k)
                    except ValueError:
                        pass
        elif isinstance(days, list):
            for v in days:
                if not isinstance(v, dict):
                    continue
                date_s = v.get("date") or v.get("day")
                if not isinstance(date_s, str):
                    continue
                is_off = bool(v.get("isOffDay") or v.get("is_off_day") or v.get("holiday"))
                if is_off:
                    holidays.add(date_s)
                elif v.get("isWorkDay") or v.get("is_work_day"):
                    workdays.add(date_s)
                else:
                    try:
                        if dt.date.fromisoformat(date_s).isoweekday() >= 6:
                            workdays.add(date_s)
                    except ValueError:
                        pass

        payload = {
            "holidays": sorted(holidays),
            "workdays": sorted(workdays),
            "synced_year": year_i,
            "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
        }
        import json

        cal_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _log_event(db, request, user, action="calendar_sync", object_type="calendar", extra={"year": year_i, "holidays": len(payload["holidays"]), "workdays": len(payload["workdays"])})
        return RedirectResponse(url="/admin/holidays?message=同步成功（请核对补班日）", status_code=302)

    @r.post("/admin/email-config", response_class=HTMLResponse)
    def email_config_save(
        request: Request,
        host: str = Form(""),
        port: str = Form("25"),
        username: str = Form(""),
        password: str = Form(""),
        sender: str = Form(""),
        to: str = Form(""),
        schedule_enabled: str = Form("0"),
        schedule_weekday: str = Form("1"),
        schedule_time: str = Form("09:00"),
        starttls: str = Form("0"),
        ssl: str = Form("0"),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        cfg_existing = load_user_email_config(user.id)
        cfg = {
            "host": host.strip(),
            "port": port.strip(),
            "username": username.strip(),
            "password": password.strip(),
            "sender": sender.strip(),
            "to": to.strip(),
            "schedule_enabled": schedule_enabled == "1",
            "schedule_weekday": int(schedule_weekday or "1"),
            "schedule_time": schedule_time.strip() or "09:00",
            "starttls": starttls == "1",
            "ssl": ssl == "1",
        }
        for k in ("last_auto_sent_key", "last_auto_sent_at"):
            if k in cfg_existing and k not in cfg:
                cfg[k] = cfg_existing[k]
        save_user_email_config(user.id, cfg)
        _log_event(
            db,
            request,
            user,
            action="user_email_config_save",
            object_type="email_config",
            extra={"schedule_enabled": cfg["schedule_enabled"]},
        )
        return RedirectResponse(url="/email-config", status_code=302)

    @r.post("/email-config", response_class=HTMLResponse)
    def user_email_config_save(
        request: Request,
        host: str = Form(""),
        port: str = Form("25"),
        username: str = Form(""),
        password: str = Form(""),
        sender: str = Form(""),
        to: str = Form(""),
        schedule_enabled: str = Form("0"),
        schedule_weekday: str = Form("1"),
        schedule_time: str = Form("09:00"),
        starttls: str = Form("0"),
        ssl: str = Form("0"),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        cfg_existing = load_user_email_config(user.id)
        cfg = {
            "host": host.strip(),
            "port": port.strip(),
            "username": username.strip(),
            "password": password.strip(),
            "sender": sender.strip(),
            "to": to.strip(),
            "schedule_enabled": schedule_enabled == "1",
            "schedule_weekday": int(schedule_weekday or "1"),
            "schedule_time": schedule_time.strip() or "09:00",
            "starttls": starttls == "1",
            "ssl": ssl == "1",
        }
        for k in ("last_auto_sent_key", "last_auto_sent_at"):
            if k in cfg_existing and k not in cfg:
                cfg[k] = cfg_existing[k]
        save_user_email_config(user.id, cfg)
        _log_event(
            db,
            request,
            user,
            action="user_email_config_save",
            object_type="email_config",
            extra={"schedule_enabled": cfg["schedule_enabled"]},
        )
        return RedirectResponse(url="/email-config", status_code=302)

    @r.post("/admin/teams")
    def create_team(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        team = crud.create_team(db, name=name)
        _log_event(db, request, user, action="dept_create", object_type="team", object_id=team.id, extra={"name": team.name})
        return RedirectResponse(url="/admin/users", status_code=302)

    @r.post("/admin/users")
    def create_user(
        request: Request,
        username: str = Form(...),
        name: str = Form(...),
        password: str = Form(...),
        role: str = Form("user"),
        team_id: Optional[str] = Form(None),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        new_user = crud.create_user(db, username=username, name=name, password=password, role=role, dept=None, team_id=_parse_int(team_id))
        _log_event(db, request, user, action="user_create", object_type="user", object_id=new_user.id, extra={"username": new_user.username})
        return RedirectResponse(url="/admin/users", status_code=302)

    @r.post("/admin/users/{user_id}/update")
    def update_user(
        request: Request,
        user_id: int,
        name: str = Form(...),
        role: str = Form(...),
        team_id: Optional[str] = Form(None),
        new_password: Optional[str] = Form(None),
        db: Session = Depends(get_db),
    ):
        user = _require_user(request, db)
        _require_admin(user)
        crud.update_user(
            db, user_id=user_id, name=name, role=role, dept=None, team_id=_parse_int(team_id), new_password=new_password
        )
        _log_event(
            db,
            request,
            user,
            action="user_update",
            object_type="user",
            object_id=user_id,
            extra={"team_id": _parse_int(team_id), "role": role, "password_reset": bool(new_password)},
        )
        return RedirectResponse(url="/admin/users", status_code=302)

    @r.get("/admin/dicts", response_class=HTMLResponse)
    def dicts_page(request: Request, db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        categories = crud.list_categories(db)
        sub_projects = crud.list_subprojects(db)
        return templates.TemplateResponse(
            "dicts.html", {"request": request, "user": user, "categories": categories, "sub_projects": sub_projects}
        )

    @r.post("/admin/dicts/category")
    def add_category(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        name = name.strip()
        if name:
            cat = models.CategoryDict(name=name, enabled=True)
            db.add(cat)
            db.flush()
            db.commit()
            _log_event(db, request, user, action="dict_category_create", object_type="category_dict", object_id=cat.id, extra={"name": cat.name})
        return RedirectResponse(url="/admin/dicts", status_code=302)

    @r.post("/admin/dicts/subproject")
    def add_subproject(request: Request, category_id: int = Form(...), name: str = Form(...), db: Session = Depends(get_db)):
        user = _require_user(request, db)
        _require_admin(user)
        name = name.strip()
        if name:
            sub = models.SubProjectDict(category_id=category_id, name=name, enabled=True)
            db.add(sub)
            db.flush()
            db.commit()
            _log_event(db, request, user, action="dict_subproject_create", object_type="sub_project_dict", object_id=sub.id, extra={"name": sub.name, "category_id": category_id})
        return RedirectResponse(url="/admin/dicts", status_code=302)

    return r
