"""Async SQLAlchemy engine and short-lived session configuration."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class Database:
    """Owned engine and session factory for one runtime process."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session without holding a transaction across caller I/O."""

        async with self.sessions() as session:
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("select 1"))

    async def close(self) -> None:
        await self.engine.dispose()


def create_database(settings: RuntimeSettings) -> Database:
    """Create the bounded application pool described by ``goal.md``.

    Psycopg's ``options`` applies the five-second web-request statement timeout
    to every pooled connection. Offline reporting uses the oracle process and a
    separate connection policy.
    """

    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={settings.database_statement_timeout_ms}"},
    )
    return Database(
        engine=engine,
        sessions=async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
    )
