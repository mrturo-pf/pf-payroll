"""Tests for employer/contract dependency factories."""

from unittest.mock import Mock

from payroll.application.use_cases.maintain_employers_contracts import (
    DeleteEmployers,
    DeleteEmploymentContracts,
    MaintainEmployers,
    MaintainEmploymentContracts,
)
from payroll.interfaces.api.dependencies import (
    get_transactional_delete_contracts_use_case,
    get_transactional_delete_employers_use_case,
    get_transactional_maintain_contracts_use_case,
    get_transactional_maintain_employers_use_case,
)


def test_employer_contract_dependency_factories() -> None:
    """Build all employer/contract use cases with the supplied repository."""
    repository = Mock()
    assert isinstance(
        get_transactional_delete_employers_use_case(repository), DeleteEmployers
    )
    assert isinstance(
        get_transactional_delete_contracts_use_case(repository),
        DeleteEmploymentContracts,
    )
    assert isinstance(
        get_transactional_maintain_employers_use_case(repository), MaintainEmployers
    )
    assert isinstance(
        get_transactional_maintain_contracts_use_case(repository),
        MaintainEmploymentContracts,
    )
