from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    dept: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team.id", ondelete="SET NULL"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user")  # user/admin
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    plans: Mapped[list["WeeklyPlan"]] = relationship(back_populates="owner")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")
    team: Mapped[Optional["Team"]] = relationship(back_populates="users")


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    users: Mapped[list["User"]] = relationship(back_populates="team")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    user: Mapped["User"] = relationship(back_populates="sessions")


class WeekPeriod(Base):
    __tablename__ = "week_period"
    __table_args__ = (UniqueConstraint("year", "week_no", name="uq_year_weekno"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, index=True)  # ISO year
    month: Mapped[int] = mapped_column(Integer, index=True)  # month of start_date (Monday)
    week_no: Mapped[int] = mapped_column(Integer, index=True)  # ISO week
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)

    plans: Mapped[list["WeeklyPlan"]] = relationship(back_populates="period")


class WeeklyPlan(Base):
    __tablename__ = "weekly_plan"
    __table_args__ = (UniqueConstraint("period_id", "owner_user_id", name="uq_owner_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("week_period.id", ondelete="RESTRICT"))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft/submitted/approved/rejected
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow()
    )

    period: Mapped["WeekPeriod"] = relationship(back_populates="plans")
    owner: Mapped["User"] = relationship(back_populates="plans")
    items: Mapped[list["PlanItem"]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class CategoryDict(Base):
    __tablename__ = "category_dict"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    sub_projects: Mapped[list["SubProjectDict"]] = relationship(back_populates="category")


class SubProjectDict(Base):
    __tablename__ = "sub_project_dict"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_category_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category_dict.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128), index=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped["CategoryDict"] = relationship(back_populates="sub_projects")


class PlanItem(Base):
    __tablename__ = "plan_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("weekly_plan.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("category_dict.id"), nullable=True)
    sub_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sub_project_dict.id"), nullable=True)
    weekly_goal: Mapped[str] = mapped_column(String(256), default="")
    progress_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_text: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    detail_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_hours: Mapped[Optional[float]] = mapped_column(Numeric(6, 1), nullable=True)
    actual_hours: Mapped[Optional[float]] = mapped_column(Numeric(6, 1), nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped["WeeklyPlan"] = relationship(back_populates="items")
    details: Mapped[list["PlanItemDetail"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    category: Mapped["CategoryDict"] = relationship()
    sub_project: Mapped["SubProjectDict"] = relationship()


class PlanItemDetail(Base):
    __tablename__ = "plan_item_detail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plan_item.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    hours: Mapped[Optional[float]] = mapped_column(Numeric(6, 1), nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    item: Mapped["PlanItem"] = relationship(back_populates="details")


class PlanTemplate(Base):
    __tablename__ = "plan_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow()
    )

    items: Mapped[list["PlanTemplateItem"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )
    created_by: Mapped[Optional["User"]] = relationship()


class PlanTemplateItem(Base):
    __tablename__ = "plan_template_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("plan_template.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("category_dict.id"), nullable=True)
    sub_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sub_project_dict.id"), nullable=True)
    weekly_goal: Mapped[str] = mapped_column(String(256), default="")
    progress_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_text: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    detail_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_hours: Mapped[Optional[float]] = mapped_column(Numeric(6, 1), nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped["PlanTemplate"] = relationship(back_populates="items")
    details: Mapped[list["PlanTemplateItemDetail"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class PlanTemplateItemDetail(Base):
    __tablename__ = "plan_template_item_detail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plan_template_item.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    hours: Mapped[Optional[float]] = mapped_column(Numeric(6, 1), nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    item: Mapped["PlanTemplateItem"] = relationship(back_populates="details")


class OperationLog(Base):
    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow(), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    object_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[Optional["User"]] = relationship()
