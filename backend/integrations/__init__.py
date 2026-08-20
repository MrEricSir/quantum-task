# Provider interfaces and implementations for external integrations (calendar, health,
# git hosting). See ARCHITECTURE_FUTURE.md ("Part 2: Pluggable External Integrations").
#
# `withings` is deliberately NOT re-exported here: integrations/withings.py imports
# routers.withings, which itself imports integrations.base (for the Measurement dataclass).
# Eagerly importing withings here would make routers.withings's own top-level import of
# integrations.base circle back through this file into integrations.withings into
# routers.withings again, mid-import. Import it directly instead:
# `from integrations.withings import WithingsProvider`.
from integrations import base, calendar_ics

__all__ = ["base", "calendar_ics"]
