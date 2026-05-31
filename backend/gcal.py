"""
Calendar event fetcher using iCal (ICS) URLs.

No authentication required. Uses Google Calendar's
"Secret address in iCal format" (or any public iCal URL).
"""

from datetime import date, datetime, time as dt_time

import requests
import icalendar
import recurring_ical_events


def fetch_events(ical_url: str, start: date, end: date) -> list[dict]:
    """Fetch and expand events from an iCal URL in [start, end).

    Handles recurring events, all-day events, and timezone-aware datetimes.
    Returns datetimes as naive local time for consistency with the rest of the app.

    Each dict: id, title, description, start (datetime), end (datetime|None), all_day (bool)
    """
    response = requests.get(ical_url, timeout=15)
    response.raise_for_status()

    cal = icalendar.Calendar.from_ical(response.content)

    # Pass timezone-aware datetimes so the library doesn't default to UTC midnight,
    # which would include events that are already in the past in the user's local tz.
    local_tz = datetime.now().astimezone().tzinfo
    start_aware = datetime.combine(start, dt_time.min).replace(tzinfo=local_tz)
    end_aware = datetime.combine(end, dt_time.min).replace(tzinfo=local_tz)
    occurrences = recurring_ical_events.of(cal).between(start_aware, end_aware)

    events = []
    for ev in occurrences:
        dtstart = ev.get("DTSTART")
        if not dtstart:
            continue

        start_val = dtstart.dt
        end_obj = ev.get("DTEND")
        end_val = end_obj.dt if end_obj else None

        # All-day events use date objects; timed events use datetime objects
        all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)

        if all_day:
            start_dt = datetime(start_val.year, start_val.month, start_val.day)
            end_dt = (
                datetime(end_val.year, end_val.month, end_val.day)
                if end_val and isinstance(end_val, date)
                else None
            )
        else:
            # Convert tz-aware datetimes to naive local time
            start_dt = start_val.astimezone().replace(tzinfo=None) if start_val.tzinfo else start_val
            end_dt = end_val.astimezone().replace(tzinfo=None) if (end_val and end_val.tzinfo) else end_val

        # Skip cancelled events
        status = str(ev.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue

        uid = str(ev.get("UID", ""))
        sequence = int(ev.get("SEQUENCE", 0))
        events.append({
            "id": uid or start_dt.isoformat(),
            "uid": uid,
            "sequence": sequence,
            "title": str(ev.get("SUMMARY", "(No title)")),
            "description": str(ev.get("DESCRIPTION", "")) or None,
            "start": start_dt,
            "end": end_dt,
            "all_day": all_day,
        })

    return events
