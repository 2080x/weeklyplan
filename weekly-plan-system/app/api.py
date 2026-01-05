from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import crud
from app.db import get_db
from app.schemas import DictNameIn, PeriodEnsureIn, PeriodOut, ReplaceDetailsIn, SubProjectCreateIn


def _require_user_id(request: Request, db: Session) -> int:
    user = crud.get_user_by_session_token(db, request.cookies.get("session"))
    if not user:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return user.id


router = APIRouter(prefix="/api")


@router.post("/periods/ensure", response_model=PeriodOut)
def ensure_period(payload: PeriodEnsureIn, request: Request, db: Session = Depends(get_db)):
    _require_user_id(request, db)
    period = crud.ensure_period(db, payload.date)
    return PeriodOut.model_validate(period, from_attributes=True)


@router.get("/dicts/subprojects")
def subprojects(request: Request, category_id: Optional[int] = None, db: Session = Depends(get_db)):
    _require_user_id(request, db)
    items = crud.list_subprojects(db, category_id=category_id)
    return [{"id": x.id, "category_id": x.category_id, "name": x.name} for x in items]


@router.post("/dicts/category")
def create_category(payload: DictNameIn, request: Request, db: Session = Depends(get_db)):
    _require_user_id(request, db)
    try:
        cat = crud.create_category(db, name=payload.name)
    except ValueError as e:
        msg = {"category_name_required": "所属大类名称必填"}.get(str(e), "保存失败")
        raise HTTPException(status_code=400, detail=msg)
    return {"id": cat.id, "name": cat.name}


@router.post("/dicts/subproject")
def create_subproject(payload: SubProjectCreateIn, request: Request, db: Session = Depends(get_db)):
    _require_user_id(request, db)
    try:
        sub = crud.create_subproject(db, category_id=payload.category_id, name=payload.name)
    except ValueError as e:
        msg = {
            "subproject_name_required": "子项目名称必填",
            "category_not_found": "请先选择有效的所属大类",
        }.get(str(e), "保存失败")
        raise HTTPException(status_code=400, detail=msg)
    return {"id": sub.id, "name": sub.name, "category_id": sub.category_id}


@router.post("/items/{item_id}/details/replace")
def replace_details(item_id: int, payload: ReplaceDetailsIn, request: Request, db: Session = Depends(get_db)):
    _require_user_id(request, db)
    details = [(d.content, d.hours) for d in payload.details]
    saved = crud.replace_item_details(db, item_id=item_id, details=details)
    return {"ok": True, "count": len(saved)}
