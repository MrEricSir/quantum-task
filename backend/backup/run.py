import os
import sqlite3
from datetime import date

from database import DATABASE_URL

_SQLITE_PREFIX = "sqlite:///"


def _db_path() -> str:
    if not DATABASE_URL.startswith(_SQLITE_PREFIX):
        raise ValueError(f"backup only supports a sqlite DATABASE_URL, got: {DATABASE_URL}")
    return DATABASE_URL[len(_SQLITE_PREFIX):]


def _backups_dir() -> str:
    """Where dated backup files are written.

    Defaults to a 'backups' subdir next to the live database, which is
    correct when the live database itself sits on the GCS-FUSE mount (local
    dev). In prod the live database is on local disk under litestream (see
    deploy-gcp.md), so BACKUP_DIR points at the still-FUSE-mounted directory
    instead -- the live db and the backups destination are on different
    volumes there.
    """
    override = os.environ.get("BACKUP_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(_db_path()) or ".", "backups")


def run_backup() -> dict:
    """Copy the live database to backups/todos_<date>.db.

    Uses SQLite's online backup API rather than a plain file copy so the
    result is a consistent snapshot even while the app is writing to the
    source database. Re-running on the same day overwrites that day's file.
    """
    db_path = _db_path()
    backups_dir = _backups_dir()
    os.makedirs(backups_dir, exist_ok=True)
    dest_path = os.path.join(backups_dir, f"todos_{date.today().isoformat()}.db")

    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    return {"ok": True, "path": dest_path, "bytes": os.path.getsize(dest_path)}
