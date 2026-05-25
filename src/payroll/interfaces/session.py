"""Shared async session helpers for interface adapters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payroll.infrastructure.db.session import SessionLocal


@asynccontextmanager
async def open_session(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> AsyncIterator[AsyncSession]:
    """Open a SQLAlchemy async session."""
    async with session_factory() as session:
        yield session
