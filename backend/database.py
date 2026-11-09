import os
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./todos.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# SQLite disables foreign-key enforcement per-connection unless a client turns it on --
# without this, every ForeignKey(..., ondelete="CASCADE"/"SET NULL") in models.py is pure
# documentation, not an enforced constraint. Scoped to the Engine class (not just the
# module-level `engine` above) so every SQLite connection gets it, including each test
# file's own separately-created engine -- this is deliberate: it's the only way the test
# suite actually exercises real FK enforcement. Guarded to sqlite3 connections only so it
# stays a no-op if this app ever runs against a different database.
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass
