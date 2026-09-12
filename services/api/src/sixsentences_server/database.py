"""Database engine and request-session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from sixsentences_server.config import Settings


@dataclass(frozen=True, slots=True)
class Database:
    """A configured engine and typed session factory."""

    engine: Engine
    sessions: sessionmaker[Session]

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit one unit of work or roll it back on failure."""

        session = self.sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Release pooled connections."""

        self.engine.dispose()


def create_database(settings: Settings) -> Database:
    """Create a portable SQLite/PostgreSQL database boundary."""

    sqlite = settings.database_url.startswith("sqlite:")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False} if sqlite else {},
        pool_pre_ping=not sqlite,
    )
    if sqlite:

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection: object, _record: object) -> None:
            cursor = connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return Database(engine=engine, sessions=sessionmaker(engine, expire_on_commit=False))
