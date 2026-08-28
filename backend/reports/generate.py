"""
Tag report generation: given a tag, a mode ("done" or "todo"), and a date
window, return the matching cards. Deterministic -- no LLM call in this file.
For a report meant to be pasted into meeting notes, a paraphrased summary
risks losing precision on what a task actually was; the strongest version of
this codebase's repeated "don't invent content" principle (briefing's opaque
labels, weekly review's "do not invent any numbers") is to not call an LLM
at all for the report body. Both the webapp (reports/router.py) and the
Telegram bot (telegram/bot.py's _reply_report) call generate_tag_report and
format its structured result in their own native format (Markdown vs
Telegram HTML) rather than sharing pre-rendered text.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

import models

PERIOD_CHOICES = [
    "today", "this_week", "last_week", "this_month", "last_month",
    "last_7_days", "last_30_days",
]


def resolve_period(period: str, today: date) -> tuple[date, date]:
    """Deterministic period -> (start, end) inclusive date range. No LLM
    date arithmetic -- mirrors model_plugins.base.resolve_dates's own stated
    rationale that weekday/date arithmetic is unreliable to ask an LLM for."""
    if period == "today":
        return today, today
    if period == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if period == "last_week":
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start - timedelta(days=1)
        return start, end
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first_of_this_month = today.replace(day=1)
        end = first_of_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return start, end
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "last_30_days":
        return today - timedelta(days=29), today
    raise ValueError(f"Unknown period: {period!r}")


def _to_local_date(dt: datetime, tz_offset: int) -> date:
    """SQLite datetimes come back naive (already UTC); tz_offset is the
    JS-convention getTimezoneOffset() value, same convention used throughout
    this codebase (see deps.utc_offset_minutes)."""
    naive_utc = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return (naive_utc - timedelta(minutes=tz_offset)).date()


def generate_tag_report(
    db: Session, tag_id: int, mode: str, start: date, end: date, tz_offset: int = 0,
) -> dict[str, Any] | None:
    """Returns None if tag_id doesn't exist. Otherwise a dict:
        {tag_name, mode, start, end, items: [{id, title, date}], count}
    `date` on each item is completed_at's local date (mode="done") or
    scheduled_at's local date, or None for an undated backlog item
    (mode="todo").
    """
    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not tag:
        return None
    if mode not in ("done", "todo"):
        raise ValueError(f"Unknown mode: {mode!r}")

    base = db.query(models.Card).filter(models.Card.tags.any(models.Tag.id == tag_id))

    items: list[dict] = []
    if mode == "done":
        candidates = base.filter(
            models.Card.completed == True,  # noqa: E712
            models.Card.completed_at.isnot(None),
        ).all()
        for c in candidates:
            local_date = _to_local_date(c.completed_at, tz_offset)
            if start <= local_date <= end:
                items.append({"id": c.id, "title": c.title, "date": local_date.isoformat()})
        items.sort(key=lambda i: i["date"])
    else:
        candidates = base.filter(
            models.Card.completed == False,  # noqa: E712
            models.Card.archived == False,  # noqa: E712
        ).all()
        for c in candidates:
            if c.scheduled_at is None:
                items.append({"id": c.id, "title": c.title, "date": None})
                continue
            local_date = _to_local_date(c.scheduled_at, tz_offset)
            if start <= local_date <= end:
                items.append({"id": c.id, "title": c.title, "date": local_date.isoformat()})
        # Dated items first (chronological), undated backlog items last.
        items.sort(key=lambda i: (i["date"] is None, i["date"] or ""))

    return {
        "tag_name": tag.name,
        "mode": mode,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "items": items,
        "count": len(items),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Webapp-side rendering of a generate_tag_report() result -- a plain
    Markdown bullet list, safe to paste directly into meeting notes. The
    Telegram bot formats the same structured `items` into HTML separately
    (telegram/bot.py's _reply_report) rather than reusing this string, since
    Telegram messages use parse_mode=HTML, not Markdown."""
    heading = "Done" if report["mode"] == "done" else "To do"
    lines = [f"### {heading}: {report['tag_name']} ({report['start']} to {report['end']})", ""]
    if not report["items"]:
        lines.append("_Nothing found for this tag and period._")
        return "\n".join(lines)
    for item in report["items"]:
        suffix = f" ({item['date']})" if item["date"] else ""
        lines.append(f"- {item['title']}{suffix}")
    return "\n".join(lines)
