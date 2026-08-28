"""
Tag report package.

    from reports import router          # FastAPI router for main.py
    from reports import generate_tag_report, resolve_period  # shared with telegram/bot.py
"""
from reports.generate import PERIOD_CHOICES, generate_tag_report, render_markdown, resolve_period
from reports.router import router

__all__ = ["router", "generate_tag_report", "render_markdown", "resolve_period", "PERIOD_CHOICES"]
