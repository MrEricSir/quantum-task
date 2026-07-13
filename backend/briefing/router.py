"""
Briefing FastAPI endpoints — thin HTTP adapters only.

Business logic lives in briefing.generate and briefing.context.
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import schemas
from briefing.context import build_today_context, build_week_context
from briefing.generate import (
    _fetch_briefing_data,
    _today_hash, _week_hash,
    _cache_get, _cache_set,
    _TODAY_SYSTEM, _WEEK_SYSTEM,
)
from deps import get_db, llm_client, LLM_MODEL, local_date, utc_offset_minutes as _utc_offset
from weather import fetch_weather

router = APIRouter()


# ── Briefing endpoint ─────────────────────────────────────────────────────────

@router.post("/api/briefing/stream")
def stream_briefing(request: Request, req: schemas.BriefingRequest, db: Session = Depends(get_db)):
    today_dt  = local_date(request)
    tz_offset = _utc_offset(request)

    d         = _fetch_briefing_data(today_dt, tz_offset, req.lat, req.lon, db=db)
    local_now = d["local_now"]

    today_h = _today_hash(d["today_cards"], d["today_events"], d["habits"],
                          d["weather"] is not None, local_now, d["steps_today"])
    week_h  = _week_hash(d["week_cards"], d["week_events"])

    cached_today = cached_weather = cached_week = None
    if not req.force:
        row_t = _cache_get("today", today_h)
        if row_t:
            cached_today   = row_t.text
            cached_weather = row_t.weather_json
        if not req.today_only:
            row_w = _cache_get("week", week_h)
            if row_w:
                cached_week = row_w.text

    need_week  = not req.today_only
    all_cached = cached_today is not None and (not need_week or cached_week is not None)

    if all_cached:
        def replay():
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
            if cached_week:
                yield f"data: {json.dumps({'section': 'week', 'text': cached_week})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"X-Briefing-Cached": "true"})

    pending_habits = [h for h in d["habits"] if not h.completed_today]
    today_ctx = build_today_context(
        d["today_cards"], d["today_events"], today_dt, d["habits"],
        d["observations"], tz_offset, d["eng_prs"],
        local_now=local_now, weather=d["weather"],
        all_cal_events=d["all_cal_events"], health_context=d["health_ctx"],
    )
    week_ctx = build_week_context(
        d["week_cards"], d["week_events"], today_dt, tz_offset, d["eng_issues"]
    ) if need_week else None

    def generate():
        weather_raw: str | None = None

        if cached_today is not None:
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
        else:
            if d["weather"]:
                weather_raw = json.dumps({'type': 'weather', **d["weather"]})
                yield f"data: {weather_raw}\n\n"

            today_acc: list[str] = []
            if not (d["today_cards"] or d["today_events"] or pending_habits):
                text = 'Nothing scheduled today.'
                yield f"data: {json.dumps({'section': 'today', 'text': text})}\n\n"
                today_acc.append(text)
            else:
                try:
                    stream = llm_client().chat.completions.create(
                        model=LLM_MODEL,
                        messages=[{"role": "system", "content": _TODAY_SYSTEM},
                                  {"role": "user",   "content": today_ctx}],
                        stream=True, temperature=0.1,
                    )
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield f"data: {json.dumps({'section': 'today', 'text': delta})}\n\n"
                            today_acc.append(delta)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            _cache_set("today", today_h, ''.join(today_acc), weather_raw)

        if need_week:
            if cached_week is not None:
                yield f"data: {json.dumps({'section': 'week', 'text': cached_week})}\n\n"
            elif week_ctx:
                week_acc: list[str] = []
                try:
                    stream = llm_client().chat.completions.create(
                        model=LLM_MODEL,
                        messages=[{"role": "system", "content": _WEEK_SYSTEM},
                                  {"role": "user",   "content": week_ctx}],
                        stream=True, temperature=0.1,
                    )
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield f"data: {json.dumps({'section': 'week', 'text': delta})}\n\n"
                            week_acc.append(delta)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"

                if week_acc:
                    _cache_set("week", week_h, ''.join(week_acc))

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Standalone weather endpoint ───────────────────────────────────────────────

@router.get("/weather")
def get_weather(lat: float, lon: float):
    """Return current weather for the given coordinates."""
    result = fetch_weather(lat, lon)
    if result is None:
        return {}
    return result
