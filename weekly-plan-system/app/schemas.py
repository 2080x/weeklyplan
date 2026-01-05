from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PeriodEnsureIn(BaseModel):
    date: dt.date


class PeriodOut(BaseModel):
    id: int
    year: int
    month: int
    week_no: int
    start_date: dt.date
    end_date: dt.date


class DictNameIn(BaseModel):
    name: str = Field(min_length=1)


class SubProjectCreateIn(DictNameIn):
    category_id: int


class ItemDetailIn(BaseModel):
    content: str = Field(min_length=1)
    hours: Optional[float] = None


class ReplaceDetailsIn(BaseModel):
    details: list[ItemDetailIn]


class ItemOut(BaseModel):
    id: int
    category_id: Optional[int]
    sub_project_id: Optional[int]
    weekly_goal: str
    progress_percent: Optional[int]
    progress_text: Optional[str]
    detail_text: Optional[str]
    estimated_hours: Optional[float]


class PlanOut(BaseModel):
    id: int
    period_id: int
    owner_user_id: int
    status: Literal["draft", "submitted", "approved", "rejected"]
