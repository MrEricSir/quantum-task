"""
Calendar event fetcher using iCal (ICS) URLs.

No authentication required. Uses Google Calendar's
"Secret address in iCal format" (or any public iCal URL).
"""

import base64
import ipaddress
import re
import socket
import urllib.parse
from datetime import date, datetime, time as dt_time, timedelta, timezone

import requests
import icalendar
import recurring_ical_events

_GCAL_ICAL_URL_RE = re.compile(r'calendar\.google\.com/calendar/ical/([^/]+)/')

_ALLOWED_FETCH_SCHEMES = {"http", "https"}
_MAX_FETCH_REDIRECTS = 5

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


def _is_safe_host(hostname: str) -> bool:
    """Reject hostnames that resolve to a private, loopback, link-local, or otherwise
    non-public address -- blocks SSRF to cloud metadata endpoints (169.254.169.254) and
    internal network services via a stored feed URL that gets refetched automatically
    forever (calendar mappings, discovery feeds) with no further attacker action needed."""
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in addrinfos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _assert_safe_fetch_url(url: str) -> None:
    """Raise ValueError unless url is a plain http(s) URL resolving only to public
    addresses. Called both before the initial request and before following each
    redirect hop, so a feed can't bounce the fetch to an internal address either."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_FETCH_SCHEMES:
        raise ValueError(f"Refusing to fetch calendar feed with scheme {parsed.scheme!r} (only http/https allowed).")
    if not parsed.hostname:
        raise ValueError("Calendar feed URL has no host.")
    if not _is_safe_host(parsed.hostname):
        raise ValueError(
            f"Refusing to fetch calendar feed at host {parsed.hostname!r} -- it resolves to a "
            "private, internal, or otherwise disallowed address."
        )


# In-memory cache for iCal feed fetches.
# Keyed by (url, today_iso) so the cache naturally expires at midnight even
# without a TTL (events shift relative to today each day).  A TTL is also
# applied so a busy day doesn't hammer external servers.
_TTL_SECONDS = 15 * 60  # 15 minutes

_ical_cache: dict[tuple[str, str, int], tuple[float, list]] = {}


def _cached_fetch_events(
    ical_url: str, start: date, end: date, tz_offset_minutes: int = 0, *, force: bool = False
) -> list[dict]:
    import time
    # tz_offset_minutes is part of the key (not just url/date) so a user's offset changing
    # mid-trip is picked up immediately instead of serving a window built for their old
    # timezone until the TTL/day-key naturally expires.
    key = (ical_url, start.isoformat(), tz_offset_minutes)
    now = time.monotonic()
    if not force and key in _ical_cache:
        ts, events = _ical_cache[key]
        if now - ts < _TTL_SECONDS:
            return events
    events = fetch_events(ical_url, start, end, tz_offset_minutes)
    _ical_cache[key] = (now, events)
    return events


def get_personal_events(
    db,
    start_date: date,
    end_date: date,
    tz_offset_minutes: int = 0,
) -> list[dict]:
    """Fetch and filter personal calendar events from all configured CalendarMappings.

    - Skips OOO events
    - Skips past timed events (end or start < now_utc)
    - Adds ``local_date`` (date in user's timezone) and ``time_str`` ("3:30 PM" / "All day")
    - Skips events whose local_date falls outside [start_date, end_date)

    Returns enriched event dicts (all fields from fetch_events() plus local_date and time_str).
    ``end_date`` is exclusive.
    """
    import models

    now_utc = datetime.now(timezone.utc)
    try:
        mappings = db.query(models.CalendarMapping).all()
    except Exception as e:
        print(f"[calendar] failed to query mappings: {e}")
        return []

    results = []
    fetch_errors = []
    for m in mappings:
        try:
            for ev in _cached_fetch_events(m.ical_url, start_date, end_date, tz_offset_minutes):
                if ev.get("is_ooo"):
                    continue
                ev_start = ev["start"]
                ev_end = ev.get("end")

                if ev["all_day"]:
                    ev_date = ev_start.date() if isinstance(ev_start, datetime) else ev_start
                    time_str = "All day"
                else:
                    cutoff = ev_end if ev_end else ev_start
                    if cutoff < now_utc:
                        continue
                    local_dt = ev_start.replace(tzinfo=None) - timedelta(minutes=tz_offset_minutes)
                    ev_date = local_dt.date()
                    time_str = local_dt.strftime("%-I:%M %p").lstrip("0") or "12:00 AM"

                if ev_date < start_date or ev_date >= end_date:
                    continue

                results.append({**ev, "local_date": ev_date, "time_str": time_str})
        except Exception as e:
            print(f"[calendar] fetch error for mapping {m.id}: {e}")
            fetch_errors.append(e)

    # If every mapping failed and we have nothing to show, surface the error
    # so callers can distinguish "fetch failed" from "genuinely no events".
    if fetch_errors and not results:
        raise fetch_errors[0]

    return results


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

def fetch_events(ical_url: str, start: date, end: date, tz_offset_minutes: int = 0) -> list[dict]:
    """Fetch and expand events from an iCal URL in [start, end).

    Handles recurring events, all-day events, and timezone-aware datetimes.
    Returns timed-event datetimes as UTC-aware so callers can convert to any local timezone.

    tz_offset_minutes uses the JS Date.getTimezoneOffset() convention (UTC+10 -> -600,
    UTC-5 -> +300, see deps.py's utc_offset_minutes docstring) and is used only to place
    [start, end)'s midnight boundaries in the caller's local timezone before asking
    recurring_ical_events to expand occurrences -- using the server's own timezone here
    (always UTC on Cloud Run) instead would shift the fetch window by the caller's full
    UTC offset, silently dropping or including events for callers far from UTC.

    Each dict: id, title, description, start (datetime), end (datetime|None), all_day (bool)
    """
    ical_url = normalize_ical_url(ical_url)
    _assert_safe_fetch_url(ical_url)
    # (connect, read) -- most real iCal servers respond in a few seconds; this still gives a
    # genuinely slow feed real headroom without letting one pathological feed hold up an
    # entire request for the better part of a minute (was (10, 45) -- see
    # DISCOVERY_IMPROVEMENTS.md's "slow to load" Phase 1 for why this got tightened).
    # Redirects are followed manually (not via allow_redirects=True) so each hop's target
    # gets the same SSRF check as the original URL -- a feed can't pass validation once and
    # then redirect the actual request to an internal address.
    current_url = ical_url
    for _ in range(_MAX_FETCH_REDIRECTS):
        response = requests.get(current_url, timeout=(5, 15), headers=_FETCH_HEADERS, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                break
            current_url = urllib.parse.urljoin(current_url, location)
            _assert_safe_fetch_url(current_url)
            continue
        break
    else:
        raise ValueError(f"Too many redirects while fetching calendar feed at {ical_url!r}.")
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
    local_tz = timezone(timedelta(minutes=-tz_offset_minutes))
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
