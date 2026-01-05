from __future__ import annotations

import datetime as dt
from typing import List, Optional, Set, Tuple


def iso_week_period(for_date: dt.date) -> tuple[int, int, dt.date, dt.date]:
    iso_year, iso_week, iso_weekday = for_date.isocalendar()
    monday = for_date - dt.timedelta(days=iso_weekday - 1)
    sunday = monday + dt.timedelta(days=6)
    return iso_year, iso_week, monday, sunday


def week_in_month(monday: dt.date) -> int:
    return (monday.day - 1) // 7 + 1


def week_in_month_for_period(monday: dt.date) -> int:
    anchor = monday + dt.timedelta(days=3)  # Thursday
    return week_in_month(anchor)


def workdays_in_range(
    start_date: dt.date,
    end_date: dt.date,
    *,
    holidays: Optional[Set[dt.date]] = None,
    workdays: Optional[Set[dt.date]] = None,
) -> List[dt.date]:
    holidays = holidays or set()
    workdays = workdays or set()
    days: List[dt.date] = []
    cur = start_date
    while cur <= end_date:
        if cur in workdays:
            days.append(cur)
        elif cur.isoweekday() <= 5 and cur not in holidays:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


def workday_range(
    start_date: dt.date,
    end_date: dt.date,
    *,
    holidays: Optional[Set[dt.date]] = None,
    workdays: Optional[Set[dt.date]] = None,
) -> Tuple[Optional[dt.date], Optional[dt.date], int]:
    days = workdays_in_range(start_date, end_date, holidays=holidays, workdays=workdays)
    if not days:
        return None, None, 0
    return days[0], days[-1], len(days)
