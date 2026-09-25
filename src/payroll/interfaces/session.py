"""Shared async session helpers for interface adapters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
)

from payroll.infrastructure.db.session import SessionLocal
from payroll.infrastructure.db.session import engine as default_engine


@asynccontextmanager
async def open_session(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
) -> AsyncIterator[AsyncSession]:
    """Open a SQLAlchemy async session."""
    async with session_factory() as session:
        yield session


class TransactionalSessionScope:
    """A session whose internal commits never touch the real transaction.

    Several use cases in this codebase call `session.commit()` freely while
    doing their own work (see `_refresh_summary_view` /
    `_reconcile_period_net_pay` in `payroll_repository_shared.py`, and the
    per-period commits in `payroll_repository_commands.py`). That is fine for
    every existing endpoint, which always wants those commits to stick.

    POST /payroll/import/rows with mode="validate" needs the opposite: run
    the *exact same* pipeline (so contributions/tax/warnings are genuinely
    computed, not guessed), but discard every single write at the end --
    including all of those internal commits. Wrapping the whole thing in one
    `session.rollback()` would not be enough, because each internal
    `session.commit()` already made its own transaction durable.

    The fix is `join_transaction_mode="create_savepoint"`: the session is
    bound to a connection that already has a real transaction open, and every
    `session.commit()` the application code performs only releases the
    current SAVEPOINT (SQLAlchemy immediately opens a new one). The outer,
    real transaction is never touched by application code -- only by
    `resolve()`, called exactly once, after every use case has finished.
    """

    def __init__(self, session: AsyncSession, transaction: AsyncTransaction) -> None:
        """Initialize the instance."""
        self.session = session
        self._transaction = transaction

    async def resolve(self, mode: Literal["commit", "validate"]) -> None:
        """Resolve the outer transaction: make it durable, or discard it."""
        if mode == "commit":
            await self._transaction.commit()
        else:
            await self._transaction.rollback()


@asynccontextmanager
async def open_transactional_session(
    engine: AsyncEngine = default_engine,
) -> AsyncIterator[TransactionalSessionScope]:
    """Open a TransactionalSessionScope bound to a fresh connection.

    If the caller never calls `resolve()` (an early return, an unexpected
    exception outside the route's own error handling), the outer transaction
    is rolled back defensively when the connection closes -- never left to
    the driver's implicit-close behavior.
    """
    connection: AsyncConnection
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield TransactionalSessionScope(session, transaction)
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
