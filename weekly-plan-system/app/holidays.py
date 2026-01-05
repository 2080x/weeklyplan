from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional, Tuple


def _parse_date(value: str) -> Optional[dt.date]:
    value = value.strip()
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _load_dates_from_lines(text: str) -> set[dt.date]:
    dates: set[dt.date] = set()
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        d = _parse_date(s)
        if d:
            dates.add(d)
    return dates


def load_calendar(*, base_dir: Path) -> Tuple[set[dt.date], set[dt.date]]:
    """
    返回 (holidays, workdays)：
    - holidays：休息日（即使是工作日也会排除）
    - workdays：补班日（即使是周末也会计入）
    """
    holidays: set[dt.date] = set()
    workdays: set[dt.date] = set()

    # 新配置：data/calendar.json
    cal_path = base_dir / "data" / "calendar.json"
    try:
        if cal_path.exists():
            data = json.loads(cal_path.read_text(encoding="utf-8"))
            holidays |= {_parse_date(x) for x in (data.get("holidays") or []) if _parse_date(x)}
            workdays |= {_parse_date(x) for x in (data.get("workdays") or []) if _parse_date(x)}
    except Exception:
        pass

    env = os.getenv("HOLIDAYS", "")
    for part in env.split(","):
        d = _parse_date(part)
        if d:
            holidays.add(d)

    env_wd = os.getenv("WORKDAYS", "")
    for part in env_wd.split(","):
        d = _parse_date(part)
        if d:
            workdays.add(d)

    file_path = os.getenv("HOLIDAYS_FILE", str(base_dir / "data" / "holidays.txt"))
    try:
        p = Path(file_path)
        if p.exists():
            holidays |= _load_dates_from_lines(p.read_text(encoding="utf-8"))
    except OSError:
        pass

    wd_path = os.getenv("WORKDAYS_FILE", str(base_dir / "data" / "workdays.txt"))
    try:
        p = Path(wd_path)
        if p.exists():
            workdays |= _load_dates_from_lines(p.read_text(encoding="utf-8"))
    except OSError:
        pass

    return holidays, workdays


def load_holidays(*, base_dir: Path) -> set[dt.date]:
    holidays, _ = load_calendar(base_dir=base_dir)
    return holidays
