# Provider interfaces and implementations for external integrations (calendar, health,
# git hosting). See ARCHITECTURE_FUTURE.md ("Part 2: Pluggable External Integrations").
from integrations import base, calendar_ics

__all__ = ["base", "calendar_ics"]
