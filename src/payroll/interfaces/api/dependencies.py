"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.income_tax_bracket import IncomeTaxBracketPort
from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    EmployerPaymentRuleReader,
    MarketDataRepository,
    PayrollRepository,
    ReferenceDataRepository,
)
from payroll.application.ports.template_repository import (
    TemplateReader,
    TemplateRepository,
)
from payroll.infrastructure.http.pf_rates_client import PfRatesClient
from payroll.infrastructure.http.income_tax_bracket_client import IncomeTaxBracketClient
from payroll.infrastructure.exporters.spreadsheet_exporter import (
    get_exporter_for_format,
)
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter

# Use cases
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.export_payroll import ExportPayroll
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.preview_pdf_import import PreviewPdfImport
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.interfaces.repositories import (
    SqlAlchemyComplementaryInsuranceRepository,
    SqlAlchemyPayrollRepository,
    SqlAlchemyReferenceDataRepository,
    SqlAlchemyTemplateRepository,
)
from payroll.infrastructure.pdf_import.extractor import TemplatePdfPayrollExtractor
from payroll.config import settings
from payroll.interfaces.session import (
    SessionLocal,
    TransactionalSessionScope,
    open_session,
    open_transactional_session,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Get session."""
    async with open_session(SessionLocal) as session:
        yield session


def get_reference_data_repository(
    session: AsyncSession = Depends(get_session),
) -> ReferenceDataRepository:
    """Get reference data repository."""
    return SqlAlchemyReferenceDataRepository(session)


def get_reference_data_queries(
    repository: ReferenceDataRepository = Depends(get_reference_data_repository),
) -> ReferenceDataQueries:
    """Get reference data queries."""
    return ReferenceDataQueries(repository)


def get_payroll_repository(
    session: AsyncSession = Depends(get_session),
) -> PayrollRepository:
    """Get payroll repository.

    Wires in a real MarketDataRepository (PfRatesClient) so
    list_period_ranges() can predict the first future period's net_pay_clp
    via pf-rates-backed UF values; every other PayrollRepository consumer
    simply never reads it. See docs/proposals/net-pay-prediction-
    reimplementation-design-recommendation.md.
    """
    return SqlAlchemyPayrollRepository(session, get_market_data_repository())


def get_market_data_repository() -> MarketDataRepository:
    """Get market data repository (HTTP adapter backed by pf-rates)."""
    return PfRatesClient(
        base_url=settings.pf_rates_base_url,
        api_key=settings.pf_rates_api_key,
        cache_ttl_seconds=settings.pf_rates_cache_ttl_seconds,
    )


def get_income_tax_bracket_client() -> IncomeTaxBracketPort:
    """Get income tax bracket client (HTTP adapter backed by pf-rates)."""
    return IncomeTaxBracketClient(
        base_url=settings.pf_rates_base_url,
        api_key=settings.pf_rates_api_key,
        cache_ttl_seconds=settings.pf_rates_cache_ttl_seconds,
    )


def get_complementary_insurance_repository(
    session: AsyncSession = Depends(get_session),
) -> ComplementaryInsuranceRepository:
    """Get complementary insurance repository."""
    return SqlAlchemyComplementaryInsuranceRepository(session)


def get_template_repository(
    session: AsyncSession = Depends(get_session),
) -> TemplateRepository:
    """Get the full PDF template repository (CRUD), used by /payroll/templates."""
    return SqlAlchemyTemplateRepository(session)


def get_template_reader(
    session: AsyncSession = Depends(get_session),
) -> TemplateReader:
    """Get a read-only PDF template reader, used by PreviewPdfImport.

    Same concrete class as get_template_repository() -- SqlAlchemyTemplateRepository
    satisfies both Protocols -- but resolved through its own dependency so
    PreviewPdfImport's wiring only ever declares the narrower port it needs.
    """
    return SqlAlchemyTemplateRepository(session)


def get_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ImportPayroll:
    """Get import payroll use case."""
    return ImportPayroll(repository, XlsxPayrollImporter())


async def get_transactional_session() -> AsyncIterator[TransactionalSessionScope]:
    """Get a transactional session scope for the payroll import endpoints.

    Every dependency below that depends on this one shares the *same*
    instance within a single request (FastAPI caches by dependency callable),
    so ImportPayroll and ProcessImportedPayrollPeriods run against one
    connection/transaction that the route resolves exactly once, at the very
    end -- see TransactionalSessionScope's docstring for why a plain
    session.rollback() would not be enough here. Used by both
    POST /payroll/import/spreadsheet (CSV/XLSX) and POST /payroll/import/json
    (PDF-confirm): both need the ability to run the full import +
    reconciliation pipeline and still roll everything back if the result
    turns out not fully validated.
    """
    async with open_transactional_session() as scope:
        yield scope


def get_transactional_payroll_repository(
    scope: TransactionalSessionScope = Depends(get_transactional_session),
) -> PayrollRepository:
    """Get a payroll repository bound to the transactional session scope.

    Also wired with a real MarketDataRepository, mirroring
    get_payroll_repository() above, so behavior never silently diverges
    between the two -- list_period_ranges() is read-only and never actually
    reached through this transactional path today, but there is no reason
    for the two factories to drift.
    """
    return SqlAlchemyPayrollRepository(scope.session, get_market_data_repository())


def get_transactional_complementary_insurance_repository(
    scope: TransactionalSessionScope = Depends(get_transactional_session),
) -> ComplementaryInsuranceRepository:
    """Get a complementary insurance repository bound to the same scope."""
    return SqlAlchemyComplementaryInsuranceRepository(scope.session)


def get_transactional_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_transactional_payroll_repository),
) -> ImportPayroll:
    """Get the import use case bound to the transactional session scope."""
    return ImportPayroll(repository, XlsxPayrollImporter())


def get_transactional_process_imported_payroll_periods_use_case(
    repository: PayrollRepository = Depends(get_transactional_payroll_repository),
    complementary_insurance_repository: ComplementaryInsuranceRepository = Depends(
        get_transactional_complementary_insurance_repository
    ),
) -> ProcessImportedPayrollPeriods:
    """Get the post-processing use case bound to the transactional scope."""
    return ProcessImportedPayrollPeriods(
        repository,
        get_market_data_repository(),
        complementary_insurance_repository,
        get_income_tax_bracket_client(),
    )


def get_preview_pdf_import_use_case(
    reference_data: EmployerPaymentRuleReader = Depends(get_reference_data_repository),
    template_reader: TemplateReader = Depends(get_template_reader),
) -> PreviewPdfImport:
    """Get preview pdf import use case.

    Takes a required read-only TemplateReader (templates are the entire
    point of the extractor) and an optional read-only ReferenceDataRepository
    so it can resolve the real employer payment-date rule once template
    matching reveals which employer produced the PDF -- reads only, never
    writes. PreviewPdfImport still takes no PayrollRepository at all, so it
    can never persist anything nor trigger ProcessImportedPayrollPeriods.
    """
    return PreviewPdfImport(
        TemplatePdfPayrollExtractor(), template_reader, reference_data
    )


def get_process_imported_payroll_periods_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    complementary_insurance_repository: ComplementaryInsuranceRepository = Depends(
        get_complementary_insurance_repository
    ),
) -> ProcessImportedPayrollPeriods:
    """Get imported-payroll post-processing use case."""
    return ProcessImportedPayrollPeriods(
        repository,
        get_market_data_repository(),
        complementary_insurance_repository,
        get_income_tax_bracket_client(),
    )


def get_payroll_queries(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> PayrollQueries:
    """Get payroll queries."""
    return PayrollQueries(repository)


def get_deflate_amounts_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> DeflateAmounts:
    """Get deflate amounts use case for non-HTTP/internal compatibility."""
    return DeflateAmounts(repository, get_market_data_repository())


def build_export_payroll_use_case(
    repository: PayrollRepository, spreadsheet_format: str
) -> ExportPayroll:
    """Build the export use case for a given repository/format combination.

    Deliberately a plain function, not resolved via Depends() like the
    other *_use_case factories in this module: `spreadsheet_format` is a
    plain per-request value (the export route's own `format` query
    parameter, already validated as Literal["csv", "xlsx"]), not something
    FastAPI needs to inject. Call this directly from the route body with a
    repository obtained via Depends(get_payroll_repository).
    """
    return ExportPayroll(repository, get_exporter_for_format(spreadsheet_format))
