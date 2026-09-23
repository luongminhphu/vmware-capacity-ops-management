from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema() -> None:
    """create_all() plus tiny additive migrations for columns introduced after
    the first release (SQLAlchemy's create_all never alters existing tables).
    Safe to call on every start — each ALTER runs only when the column is
    missing, on both SQLite and PostgreSQL."""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)
    additive = {
        "vm_snapshots": [("instance_uuid", "VARCHAR(64)")],
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in additive.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table} ({name})"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
