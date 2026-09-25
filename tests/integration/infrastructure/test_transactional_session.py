"""End-to-end proof that TransactionalSessionScope really discards writes.

This is the one spot in pf-payroll's test suite that talks to a real
Postgres (via testcontainers) instead of a fake session -- deliberately.
Everything else about the payroll domain is already covered by fast fakes
(see tests/unit/infrastructure/test_payroll_repository.py), but the whole
point of TransactionalSessionScope is a claim about real transaction/
SAVEPOINT semantics that no fake session could honestly verify: several
session.commit() calls happen *inside* the scope, and mode="validate" must
still make every one of them disappear. Only a real database can prove that.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from payroll.interfaces.session import open_transactional_session


@pytest.fixture(scope="module")
def postgres_container() -> AsyncIterator[PostgresContainer]:
    """Start a single throwaway Postgres container for this test module."""
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container


@pytest_asyncio.fixture
async def engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """Build a fresh engine + probe table per test.

    A fresh AsyncEngine per test avoids reusing asyncpg connections across
    the different event loops pytest-asyncio creates per test function; the
    underlying container (and its resulting wall-clock cost) is still only
    started once for the whole module.
    """
    test_engine = create_async_engine(postgres_container.get_connection_url())
    async with test_engine.begin() as connection:
        await connection.execute(text("DROP TABLE IF EXISTS probe"))
        await connection.execute(
            text("CREATE TABLE probe (id SERIAL PRIMARY KEY, value TEXT NOT NULL)")
        )
    yield test_engine
    await test_engine.dispose()


async def _probe_count(engine: AsyncEngine) -> int:
    """Count rows currently visible in the probe table."""
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT COUNT(*) FROM probe"))
        return result.scalar_one()


@pytest.mark.asyncio
async def test_commit_mode_persists_every_internal_commit(engine: AsyncEngine) -> None:
    """mode="commit" makes every session.commit() durable, as expected."""
    async with open_transactional_session(engine) as scope:
        await scope.session.execute(text("INSERT INTO probe (value) VALUES ('a')"))
        await scope.session.commit()  # mirrors import_rows()'s internal commit
        await scope.session.execute(text("INSERT INTO probe (value) VALUES ('b')"))
        await scope.session.commit()  # mirrors a post-processing step's commit
        await scope.resolve("commit")

    assert await _probe_count(engine) == 2


@pytest.mark.asyncio
async def test_validate_mode_discards_every_internal_commit(
    engine: AsyncEngine,
) -> None:
    """mode="validate" rolls back everything, despite two internal commits."""
    async with open_transactional_session(engine) as scope:
        await scope.session.execute(text("INSERT INTO probe (value) VALUES ('c')"))
        await scope.session.commit()
        await scope.session.execute(text("INSERT INTO probe (value) VALUES ('d')"))
        await scope.session.commit()
        await scope.resolve("validate")

    assert await _probe_count(engine) == 0


@pytest.mark.asyncio
async def test_never_resolved_rolls_back_defensively_on_close(
    engine: AsyncEngine,
) -> None:
    """An unresolved scope (e.g. a bug/early-return) never leaks a write."""
    async with open_transactional_session(engine) as scope:
        await scope.session.execute(text("INSERT INTO probe (value) VALUES ('e')"))
        await scope.session.commit()
        # No scope.resolve() call here -- the `finally` block in
        # open_transactional_session() must roll back on its own.

    assert await _probe_count(engine) == 0
