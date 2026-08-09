import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """WAL + foreign keys so the scheduler thread and API can share the DB safely."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # A live call commits a turn every few seconds while the scheduler ticks and
    # the dashboard polls. Waiting for a busy writer is always better than
    # failing the request; 5s was short enough to surface as 500s under load.
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _column_ddl(column) -> str:
    """SQLite ADD COLUMN clause; NOT NULL columns need a literal default."""
    ddl = f'"{column.name}" {column.type.compile(engine.dialect)}'
    if column.nullable:
        return ddl
    default = getattr(column.default, "arg", None)
    if default is None or callable(default):
        python_type = getattr(column.type, "python_type", str)
        default = 0 if python_type in (int, float, bool) else ""
    literal = default if isinstance(default, (int, float)) else f"'{default}'"
    return f"{ddl} NOT NULL DEFAULT {literal}"


def ensure_schema() -> None:
    """Add tables and columns the models gained since this DB file was created.

    create_all() only creates missing tables, so a database from before a model
    change keeps booting and then fails at query time with "no such column".
    Idempotent, so it is safe on every start and on a fresh clone.
    """
    from . import models  # noqa: F401  — registers every table on Base.metadata

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {_column_ddl(column)}'))
                log.warning("schema: added %s.%s", table.name, column.name)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
