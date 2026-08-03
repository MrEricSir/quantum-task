"""
Tests for the database backup module.

Covers: backup.run.run_backup (unit, against a temp sqlite file) and the
POST /api/backup/run endpoint (with run_backup patched out).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sqlite3
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backup.run as backup_run
from main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_sqlite_file(path, rows=(1,)):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()


class TestRunBackup:

    def test_creates_datestamped_copy_with_same_data(self, tmp_path, monkeypatch):
        db_path = tmp_path / "todos.db"
        _make_sqlite_file(db_path, rows=(1, 2, 3))
        monkeypatch.setattr(backup_run, "DATABASE_URL", f"sqlite:///{db_path}")

        result = backup_run.run_backup()

        assert result["ok"] is True
        expected_path = tmp_path / "backups" / f"todos_{date.today().isoformat()}.db"
        assert result["path"] == str(expected_path)
        assert os.path.exists(expected_path)

        conn = sqlite3.connect(expected_path)
        rows = conn.execute("SELECT id FROM t ORDER BY id").fetchall()
        conn.close()
        assert rows == [(1,), (2,), (3,)]

    def test_second_run_same_day_overwrites_rather_than_erroring(self, tmp_path, monkeypatch):
        db_path = tmp_path / "todos.db"
        _make_sqlite_file(db_path, rows=(1,))
        monkeypatch.setattr(backup_run, "DATABASE_URL", f"sqlite:///{db_path}")

        first = backup_run.run_backup()

        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO t VALUES (2)")
        conn.commit()
        conn.close()

        second = backup_run.run_backup()

        assert first["path"] == second["path"]
        conn = sqlite3.connect(second["path"])
        rows = conn.execute("SELECT id FROM t ORDER BY id").fetchall()
        conn.close()
        assert rows == [(1,), (2,)]

    def test_rejects_non_sqlite_database_url(self, monkeypatch):
        monkeypatch.setattr(backup_run, "DATABASE_URL", "postgresql://example/db")
        with pytest.raises(ValueError):
            backup_run.run_backup()

    def test_backup_dir_override_writes_outside_the_db_directory(self, tmp_path, monkeypatch):
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        db_path = db_dir / "todos.db"
        _make_sqlite_file(db_path, rows=(1,))
        monkeypatch.setattr(backup_run, "DATABASE_URL", f"sqlite:///{db_path}")

        backups_dir = tmp_path / "mounted" / "backups"
        monkeypatch.setenv("BACKUP_DIR", str(backups_dir))

        result = backup_run.run_backup()

        expected_path = backups_dir / f"todos_{date.today().isoformat()}.db"
        assert result["path"] == str(expected_path)
        assert os.path.exists(expected_path)
        assert not os.path.exists(db_dir / "backups")


class TestBackupEndpoint:

    def test_post_run_calls_run_backup_and_returns_its_result(self, client):
        fake_result = {"ok": True, "path": "/x/backups/todos_2026-01-01.db", "bytes": 123}
        with patch("backup.router.run_backup", return_value=fake_result):
            resp = client.post("/api/backup/run")

        assert resp.status_code == 200
        assert resp.json() == fake_result
