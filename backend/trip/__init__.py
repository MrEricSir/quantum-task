"""
Trip mode package: an open-ended travel window that pauses habit-streak accounting
(streak.py) without pausing logging, plus a Telegram "welcome back" retrospective.

    from trip import router               # FastAPI router for main.py
    from trip import generate_trip_retrospective
"""
from trip.router import router
from trip.generate import generate_trip_retrospective

__all__ = ["router", "generate_trip_retrospective"]
