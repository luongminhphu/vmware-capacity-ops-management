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
        "vm_snapshots": [
            ("instance_uuid", "VARCHAR(64)"),
            ("guest_id", "VARCHAR(64)"), ("guest_full_name", "VARCHAR(255)"),
            ("tools_guest_full_name", "VARCHAR(255)"), ("tools_guest_family", "VARCHAR(64)"),
            ("tools_status", "VARCHAR(32)"), ("guest_hostname", "VARCHAR(255)"),
        ],
        "host_snapshots": [
            ("vendor", "VARCHAR(128)"), ("model", "VARCHAR(128)"), ("esxi_version", "VARCHAR(64)"),
            ("esxi_build", "VARCHAR(64)"), ("cpu_model", "VARCHAR(255)"), ("cpu_sockets", "INTEGER"),
            ("cpu_threads", "INTEGER"), ("cluster_ha", "BOOLEAN"), ("cluster_drs", "BOOLEAN"),
        ],
    }
    indexed = {"instance_uuid"}
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in additive.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    if name in indexed:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{name} ON {table} ({name})"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
