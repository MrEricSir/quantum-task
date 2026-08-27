from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from deps import get_db
from export.registry import build_export

router = APIRouter()


@router.get("/api/export")
def export_data(db: Session = Depends(get_db)):
    """Return all of the user's own data (tasks, habits, health logs, safe
    settings, etc.) as a downloadable JSON file -- see export/registry.py
    for exactly which sections are included."""
    data = build_export(db)
    data["exported_at"] = datetime.now(timezone.utc).isoformat()
    filename = f"quantum-task-export-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
