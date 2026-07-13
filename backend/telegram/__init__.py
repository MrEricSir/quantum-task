"""
Telegram integration package.

    from telegram import router          # FastAPI router for main.py
    from telegram.scheduler import ...   # scheduling checks
    from telegram.bot import ...         # message routing / reply functions
    from telegram.notify import ...      # raw HTTP calls to Telegram API
"""
from telegram.router import router

__all__ = ["router"]
