"""
Briefing package — daily briefing generation and delivery.

    from briefing import router                 # FastAPI router for main.py
    from briefing import generate_today_briefing  # used by telegram scheduler
    from briefing.context import event_local_date, build_today_context, ...
    from briefing.generate import _fetch_briefing_data  # internal use
"""
from briefing.router import router
from briefing.generate import generate_today_briefing

__all__ = ["router", "generate_today_briefing"]
