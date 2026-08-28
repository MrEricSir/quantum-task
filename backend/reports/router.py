from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from deps import get_db, local_date, utc_offset_minutes
from reports.generate import PERIOD_CHOICES, generate_tag_report, render_markdown, resolve_period

router = APIRouter()


@router.get("/api/reports/tag")
def get_tag_report(
    request: Request,
    tag_id: int,
    mode: str,
    period: str | None = None,
    start: str | None = Query(None),
    end: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Cards for a tag over a date window -- either "done" (completed) or
    "todo" (open) items. Pass `period` (one of PERIOD_CHOICES) for a quick
    range, or explicit `start`/`end` (YYYY-MM-DD) for a custom one."""
    if mode not in ("done", "todo"):
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode!r}")

    today = local_date(request)
    if period:
        if period not in PERIOD_CHOICES:
            raise HTTPException(status_code=400, detail=f"Invalid period: {period!r}")
        start_date, end_date = resolve_period(period, today)
    elif start and end:
        from datetime import date as _date
        try:
            start_date = _date.fromisoformat(start)
            end_date = _date.fromisoformat(end)
        except ValueError:
            raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD")
    else:
        raise HTTPException(status_code=400, detail="Provide either period or start+end")

    report = generate_tag_report(db, tag_id, mode, start_date, end_date, utc_offset_minutes(request))
    if report is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    report["markdown"] = render_markdown(report)
    return report
