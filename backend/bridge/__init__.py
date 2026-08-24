"""
Bridge package — job queue and served CLI for the qtask-bridge agent.

    from bridge import router          # FastAPI router for main.py
    from bridge.jobs import ...        # internal use
    from bridge.render import ...      # internal use
    from bridge.stale import ...       # internal use
    from bridge.unblock import ...     # internal use
"""
from bridge.router import router

__all__ = ["router"]
