"""
Calendar event fetcher using iCal (ICS) URLs.

No authentication required. Uses Google Calendar's
"Secret address in iCal format" (or any public iCal URL).
"""

import base64
import re
import urllib.parse
from datetime import date, datetime, time as dt_time, timezone

import requests
import icalendar
import recurring_ical_events

_GCAL_ICAL_URL_RE = re.compile(r'calendar\.google\.com/calendar/ical/([^/]+)/')

_OOO_TITLE_RE = re.compile(
    r'\b(out\s+of\s+office|OOO|PTO|vacation|annual\s+leave|day\s+off|time\s+off|on\s+leave)\b',
    re.I,
)


def _parse_google_calendar_id(ical_url: str) -> str | None:
    m = _GCAL_ICAL_URL_RE.search(ical_url)
    if not m:
        return None
    return urllib.parse.unquote(m.group(1))


def _shorten_calendar_id(calendar_id: str) -> str:
    """Google encodes personal Gmail calendars as {user}@m in eid strings."""
    if calendar_id.endswith("@gmail.com"):
        return calendar_id[: -len("@gmail.com")] + "@m"
    return calendar_id


def _google_calendar_event_url(uid: str, start_dt: datetime, ical_url: str, ev=None) -> str | None:
    if not uid.endswith("@google.com"):
        return None
    calendar_id = _parse_google_calendar_id(ical_url)
    if not calendar_id:
        return None
    calendar_id = _shorten_calendar_id(calendar_id)
    uid_base = uid.replace("@google.com", "")
    eid = base64.b64encode(f"{uid_base} {calendar_id}".encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/u/0/r/event?action=VIEW&eid={eid}"


def normalize_ical_url(url: str) -> str:
    """Normalize various calendar URL formats to a fetchable https:// iCal URL.

    Handles:
    - webcal:// / webcals:// → https://
    - Google Calendar embed viewer URLs → iCal feed URL
    """
    if url.startswith("webcal://"):
        url = "https://" + url[9:]
    elif url.startswith("webcals://"):
        url = "https://" + url[10:]

    # Convert Google Calendar embed viewer URLs to iCal feed URLs.
    # e.g. https://calendar.google.com/calendar/embed?src=CALID&ctz=...
    #   → https://calendar.google.com/calendar/ical/CALID/public/basic.ics
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname == "calendar.google.com" and parsed.path == "/calendar/embed":
        params = urllib.parse.parse_qs(parsed.query)
        src_list = params.get("src", [])
        if src_list:
            # Re-encode the calendar ID (parse_qs decodes it) for use in the path.
            calendar_id = urllib.parse.quote(src_list[0], safe="@")
            return f"https://calendar.google.com/calendar/ical/{calendar_id}/public/basic.ics"

    return url


_FETCH_HEADERS = {
    "Accept": "text/calendar, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    # A realistic UA avoids being blocked by servers that filter python-requests
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

def fetch_events(ical_url: str, start: date, end: date) -> list[dict]:
    """Fetch and expand events from an iCal URL in [start, end).

    Handles recurring events, all-day events, and timezone-aware datetimes.
    Returns timed-event datetimes as UTC-aware so callers can convert to any local timezone.

    Each dict: id, title, description, start (datetime), end (datetime|None), all_day (bool)
    """
    ical_url = normalize_ical_url(ical_url)
    response = requests.get(ical_url, timeout=(10, 45), headers=_FETCH_HEADERS)
    response.raise_for_status()

    # Detect when the server redirected to a login/auth page instead of returning iCal data.
    final_url = response.url
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        if "accounts.google.com" in final_url:
            raise ValueError(
                "Google redirected to a sign-in page. Use the 'Secret address in iCal format' "
                "from Google Calendar settings (Settings → [calendar] → Integrate calendar)."
            )
        raise ValueError(
            f"Server returned an HTML page instead of iCal data "
            f"(Content-Type: {content_type}). The feed URL may require authentication "
            f"or the link may be incorrect."
        )

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
            # Normalize to UTC so the frontend can convert to the user's local timezone.
            # Naive datetimes are assumed to already be in UTC (e.g. from floating events).
            start_dt = start_val.astimezone(timezone.utc) if start_val.tzinfo else start_val.replace(tzinfo=timezone.utc)
            end_dt = end_val.astimezone(timezone.utc) if (end_val and end_val.tzinfo) else (end_val.replace(tzinfo=timezone.utc) if end_val else None)

        # Skip cancelled events
        status = str(ev.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue

        uid = str(ev.get("UID", ""))
        sequence = int(ev.get("SEQUENCE", 0))
        description = str(ev.get("DESCRIPTION", "")) or None
        url = str(ev.get("URL", "")) or _google_calendar_event_url(uid, start_dt, ical_url, ev)
        title = str(ev.get("SUMMARY", "(No title)"))

        # Detect Out-of-Office events via Outlook busy status or title patterns
        busystatus = str(ev.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper()
        is_ooo = (
            busystatus == "OOF"
            or bool(_OOO_TITLE_RE.search(title))
            or title.strip().lower() == "busy"
        )

        events.append({
            "id": uid or start_dt.isoformat(),
            "uid": uid,
            "sequence": sequence,
            "title": title,
            "description": description,
            "location": str(ev.get("LOCATION", "")) or None,
            "url": url,
            "start": start_dt,
            "end": end_dt,
            "all_day": all_day,
            "is_ooo": is_ooo,
        })

    return events
