"""Import-oriented payroll repository operations."""

from collections import defaultdict

from sqlalchemy import delete, select

from payroll.application.dto import ImportPayrollResultDTO, ImportPayrollRowDTO, ImportedPayrollPeriodDTO
from payroll.application.errors import PayrollValidationError
from payroll.infrastructure.db.models import EmployerModel, PayrollConceptModel
from payroll.infrastructure.db.models.payroll import PayrollItemModel, PayrollPeriodModel, PayrollStatus
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
    build_net_pay_warning,
)


class SqlAlchemyPayrollImportRepository(SqlAlchemyPayrollRepositoryBase):
    """Persistence operations related to payroll imports."""

    async def import_rows(self, rows: list[ImportPayrollRowDTO]) -> ImportPayrollResultDTO:
        if not rows:
            return ImportPayrollResultDTO(imported_periods=0, imported_items=0, periods=[])

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(PayrollConceptModel.code.in_({row.concept_code for row in rows}))
        )
        concepts = {concept.code: concept for concept in concept_result.scalars().all()}
        missing_codes = sorted({row.concept_code for row in rows} - set(concepts))
        if missing_codes:
            raise PayrollValidationError(f"Unknown payroll concepts in import: {', '.join(missing_codes)}")

        grouped_rows: dict[tuple[str, int, int], list[ImportPayrollRowDTO]] = defaultdict(list)
        for row in rows:
            grouped_rows[(row.employer, row.period_year, row.period_month)].append(row)

        imported_periods: list[ImportedPayrollPeriodDTO] = []
        imported_items = 0

        for (employer_name, year, month), period_rows in sorted(grouped_rows.items()):
            first_row = period_rows[0]

            employer_result = await self._session.execute(
                select(EmployerModel).where(EmployerModel.name == employer_name)
            )
            employer = employer_result.scalar_one_or_none()
            if employer is None:
                employer = EmployerModel(name=employer_name, started_at=first_row.payment_date)
                self._session.add(employer)
                await self._session.flush()

            period_result = await self._session.execute(
                select(PayrollPeriodModel).where(
                    PayrollPeriodModel.employer_id == employer.id,
                    PayrollPeriodModel.period_year == year,
                    PayrollPeriodModel.period_month == month,
                )
            )
            period = period_result.scalar_one_or_none()
            if period is None:
                period = PayrollPeriodModel(
                    employer_id=employer.id,
                    period_year=year,
                    period_month=month,
                    payment_date=first_row.payment_date,
                    status=PayrollStatus(first_row.status),
                    employment_contract_kind=first_row.employment_contract_kind,
                    declared_net_pay_clp=first_row.declared_net_pay_clp,
                    expected_net_pay_clp=first_row.expected_net_pay_clp,
                    net_pay_difference_clp=first_row.net_pay_difference_clp,
                )
                self._session.add(period)
                await self._session.flush()
            else:
                period.payment_date = first_row.payment_date
                period.status = PayrollStatus(first_row.status)
                period.employment_contract_kind = first_row.employment_contract_kind
                period.declared_net_pay_clp = first_row.declared_net_pay_clp
                period.expected_net_pay_clp = first_row.expected_net_pay_clp
                period.net_pay_difference_clp = first_row.net_pay_difference_clp
                await self._session.execute(delete(PayrollItemModel).where(PayrollItemModel.period_id == period.id))

            items = [
                PayrollItemModel(
                    period_id=period.id,
                    concept_id=concepts[row.concept_code].id,
                    amount_clp=row.amount_clp,
                )
                for row in period_rows
            ]
            self._session.add_all(items)
            imported_items += len(items)

            imported_periods.append(
                ImportedPayrollPeriodDTO(
                    id=period.id,
                    employer=employer.name,
                    period_year=year,
                    period_month=month,
                    payment_date=period.payment_date,
                    status=period.status.value,
                    employment_contract_kind=period.employment_contract_kind,
                    item_count=len(items),
                    declared_net_pay_clp=period.declared_net_pay_clp,
                    expected_net_pay_clp=period.expected_net_pay_clp,
                    net_pay_difference_clp=period.net_pay_difference_clp,
                    net_pay_warning=build_net_pay_warning(period.net_pay_difference_clp),
                )
            )

        await self._refresh_summary_view()

        return ImportPayrollResultDTO(
            imported_periods=len(imported_periods),
            imported_items=imported_items,
            periods=imported_periods,
        )
