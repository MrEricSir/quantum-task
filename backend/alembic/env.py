import os
import re
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Make the backend package importable from within alembic/
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models  # noqa: F401 — registers all ORM models with Base.metadata

target_metadata = Base.metadata


def get_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///./todos.db")


def process_revision_directives(context, revision, directives):
    """Assign the next sequential integer rev ID (00001, 00002, ...).

    Scans alembic/versions/ for existing files whose names start with digits
    and increments the highest one found.  This runs during `alembic revision`
    so the generated file automatically gets the right name and rev ID.
    """
    versions_dir = os.path.join(os.path.dirname(__file__), "versions")
    max_num = 0
    if os.path.exists(versions_dir):
        for fname in os.listdir(versions_dir):
            m = re.match(r"^(\d+)_", fname)
            if m:
                max_num = max(max_num, int(m.group(1)))
    directives[0].rev_id = str(max_num + 1).zfill(5)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            process_revision_directives=process_revision_directives,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
