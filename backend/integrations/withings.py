"""
Withings implementation of HealthProvider.

Unlike calendar_ics.py, this needed real extraction, not just wrapping: routers/withings.py's
_do_sync_impl mixed API fetching, DB upserts, and habit auto-completion in one function, with
no existing seam matching HealthProvider's shape before this. get_auth_url, exchange_code, and
fetch_measurements were pulled out of routers/withings.py's auth-url endpoint, callback
endpoint, and _do_sync_impl respectively, for this purpose -- routers/withings.py's own
do_sync() now calls fetch_measurements() too, so there's one implementation of "call the
Withings API and parse a reading" instead of two, matching Part 1's "shared function, not two
implementations" rule.

Not registered anywhere / not imported from integrations/__init__.py: this module imports
routers.withings, and routers.withings imports integrations.base (for the Measurement
dataclass) -- routing this module through the package's eager re-export would make
`import routers.withings` (main.py's normal startup path) trigger `integrations/__init__.py`,
which would try to import this module, which imports routers.withings back, which is still
mid-import. Import this module directly (`from integrations.withings import WithingsProvider`)
wherever it's needed instead.
"""
from datetime import date, timedelta
from typing import Any

import routers.withings as _withings

DEFAULT_LOOKBACK_DAYS = 89


class WithingsProvider:
    """Implements HealthProvider by delegating to routers/withings.py."""

    def auth_url(self) -> str:
        return _withings.get_auth_url()

    def exchange_code(self, code: str, db: Any) -> dict:
        return _withings.exchange_code(code, db)

    def sync(self, creds: dict) -> dict:
        end = date.today()
        start = end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        readings, errors = _withings.fetch_measurements(creds, start, end)
        if errors and not readings:
            raise RuntimeError("; ".join(errors.values()))
        return readings

    def refresh(self, creds: dict, db: Any) -> dict:
        return _withings._refresh_token(creds, db)
