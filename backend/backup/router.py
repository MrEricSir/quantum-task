from fastapi import APIRouter

from backup.run import run_backup

router = APIRouter()


@router.post("/api/backup/run")
def trigger_backup():
    """Manually or scheduled-trigger a database backup."""
    return run_backup()
