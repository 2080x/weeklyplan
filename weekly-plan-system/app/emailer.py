from __future__ import annotations

import html
import json
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from app.models import WeeklyPlan
from app.utils import week_in_month_for_period


def _base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def user_email_cfg_dir() -> Path:
    return _base_dir() / "data" / "email_config_users"


def user_email_cfg_path(user_id: int) -> Path:
    return user_email_cfg_dir() / f"{int(user_id)}.json"


def load_user_email_config(user_id: int) -> dict:
    path = user_email_cfg_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_user_email_config(user_id: int, cfg: dict) -> None:
    path = user_email_cfg_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_user_email_configs() -> Iterable[tuple[int, dict]]:
    """
    读取所有用户的邮件配置（仅用于定时任务）。
    """
    base = user_email_cfg_dir()
    if not base.exists():
        return []
    items: list[tuple[int, dict]] = []
    for p in base.glob("*.json"):
        try:
            user_id = int(p.stem)
        except ValueError:
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        items.append((user_id, cfg))
    return items


def parse_recipients(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = []
        for chunk in value.replace("；", ";").replace("，", ",").splitlines():
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk = chunk.replace(";", ",")
            parts.extend([x.strip() for x in chunk.split(",")])
        return [x for x in parts if x]
    return [str(x).strip() for x in value if str(x).strip()]


def _progress_display(item) -> str:
    if item.progress_percent is not None:
        return f"{int(item.progress_percent)}%"
    if item.progress_text:
        return item.progress_text
    return "/"


def _details_text(item) -> str:
    if item.details:
        lines = []
        for idx, d in enumerate(sorted(item.details, key=lambda x: x.sort_no), start=1):
            lines.append(f"{idx}. {d.content}")
        return "\n".join(lines)
    if item.detail_text:
        return item.detail_text
    return ""


def _item_total_hours(item) -> float:
    if item.details:
        total = 0.0
        for d in item.details:
            if d.hours is None:
                continue
            total += float(d.hours)
        return total
    return float(item.estimated_hours or 0)


def _escape_with_breaks(value: str) -> str:
    return html.escape(value).replace("\n", "<br/>")


def send_plan_email(plan: WeeklyPlan, cfg: dict) -> None:
    host = cfg.get("host")
    port = int(cfg.get("port") or 25)
    username = cfg.get("username")
    password = cfg.get("password")
    sender = cfg.get("sender")
    to_addrs = parse_recipients(cfg.get("to"))
    use_starttls = bool(cfg.get("starttls"))
    use_ssl = bool(cfg.get("ssl"))
    if not (host and sender and to_addrs):
        raise RuntimeError("邮件配置不完整（host/sender/to 必填）")

    period = plan.period
    wim = week_in_month_for_period(period.start_date)
    title = f"周计划-[{plan.owner.name}]-[{period.year}]年[{period.month}月第{wim}周]"

    items = sorted(plan.items, key=lambda x: x.sort_no)
    total_est = round(sum(float(it.estimated_hours or 0) for it in items), 1)
    total_sum = round(sum(_item_total_hours(it) for it in items), 1)

    msg = EmailMessage()
    msg["Subject"] = f"周计划 - {plan.owner.name} - {period.year} W{period.week_no}"
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)

    msg.set_content(
        "\n".join(
            [
                title,
                f"周期: {period.start_date} ~ {period.end_date} (ISO W{period.week_no})",
                f"状态: {plan.status}",
                "（此邮件含 Excel 附件；如客户端不支持 HTML，请查看附件。）",
            ]
        )
    )

    rows_html = []
    for item in items:
        category = item.category.name if item.category else ""
        sub = item.sub_project.name if item.sub_project else ""
        goal = item.weekly_goal or ""
        progress = _progress_display(item)
        details = _details_text(item)
        est = float(item.estimated_hours) if item.estimated_hours is not None else 0.0
        item_sum = _item_total_hours(item)
        rows_html.append(
            "<tr>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;'>{_escape_with_breaks(category)}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;'>{_escape_with_breaks(sub)}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;'>{_escape_with_breaks(goal)}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;text-align:center;white-space:nowrap'>{html.escape(progress)}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;'>{_escape_with_breaks(details)}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;text-align:right;white-space:nowrap'>{est:.1f}</td>"
            f"<td style='border:1px solid #94a3b8;padding:8px;vertical-align:top;text-align:right;white-space:nowrap'>{item_sum:.1f}</td>"
            "</tr>"
        )

    html_body = f"""
<!doctype html>
<html lang="zh-CN">
  <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,PingFang SC,Microsoft YaHei,sans-serif;color:#0f172a;">
    <h2 style="margin:0 0 8px 0;">{html.escape(title)}</h2>
    <div style="color:#475569;font-size:12px;margin-bottom:12px;">
      周期：{period.start_date} ~ {period.end_date}（ISO：W{period.week_no}） | 状态：{html.escape(plan.status)}
    </div>
    <table border="0" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-spacing:0;border:1px solid #94a3b8;width:100%;max-width:1100px;mso-table-lspace:0pt;mso-table-rspace:0pt;">
      <thead>
        <tr>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:left;">所属大类</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:left;">子项目</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:left;">本周目标</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:left;">进度</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:left;">目标细节</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:right;white-space:nowrap;">预计工时</th>
          <th style="border:1px solid #94a3b8;background:#D9EAD3;padding:8px;text-align:right;white-space:nowrap;">工时合计</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
        <tr>
          <td style="border:1px solid #94a3b8;padding:8px;"></td>
          <td style="border:1px solid #94a3b8;padding:8px;"></td>
          <td style="border:1px solid #94a3b8;padding:8px;"></td>
          <td style="border:1px solid #94a3b8;padding:8px;"></td>
          <td style="border:1px solid #94a3b8;padding:8px;text-align:center;font-weight:700;">合计</td>
          <td style="border:1px solid #94a3b8;padding:8px;text-align:right;font-weight:700;white-space:nowrap;">{total_est:.1f}</td>
          <td style="border:1px solid #94a3b8;padding:8px;text-align:right;font-weight:700;white-space:nowrap;">{total_sum:.1f}</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
    """.strip()

    msg.add_alternative(html_body, subtype="html")

    from app.exporter import export_plan_xlsx

    xlsx = export_plan_xlsx(plan)
    filename = f"周计划_{plan.owner.name}_{period.year}_W{period.week_no}.xlsx"
    msg.add_attachment(
        xlsx,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=10) as smtp:
        if use_starttls and not use_ssl:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)
