"""
Assist package — AI assistant chat, spec generation, and card-context threads.

    from assist import router          # FastAPI router for main.py
    from assist.generate import ...    # internal use
    from assist.context import ...     # internal use
"""
from assist.router import router

__all__ = ["router"]
