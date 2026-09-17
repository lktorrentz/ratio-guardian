"""Engine SQLAlchemy + applicazione dello schema.

Convenzione del progetto (CLAUDE.md roadmap, step 2): docs/schema.sql è la
fonte di verità per la DDL. Non usiamo Base.metadata.create_all() per non
duplicare/divergere dallo schema: allo startup eseguiamo schema.sql
direttamente (le CREATE TABLE sono idempotenti, IF NOT EXISTS), poi i
modelli in app/models.py mappano quelle tabelle per l'uso ORM.
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "schema.sql"


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str) -> Engine:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def apply_schema(engine: Engine, schema_path: Path = SCHEMA_PATH) -> None:
    schema_sql = schema_path.read_text()
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(schema_sql)
        raw_conn.commit()
    finally:
        raw_conn.close()


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
