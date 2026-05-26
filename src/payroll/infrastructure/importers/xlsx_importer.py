"""Payroll flat-file import helpers."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BufferedIOBase, BytesIO

import pandas as pd

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import ImportPayrollRowDTO
from payroll.application.ports.importers import PayrollImporter
from payroll.domain.contributions import EmploymentContractKind

CONCEPT_MAP = {
    "salary_base": ("SALARY_BASE", "income", True),
    "monthly_legal_gratuity": ("LEGAL_GRATUITY", "income", True),
    "teleworking_refund": ("TELEWORK_REFUND", "income", False),
    "health_insurance_employer_contribution": (
        "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
        "income",
        True,
    ),
    "vacation_incentive": ("VACATION_INCENTIVE", "income", True),
    "holiday_bonus": ("HOLIDAY_BONUS", "income", True),
    "availability_bonus": ("AVAILABILITY_BONUS", "income", True),
    "legal_gratuity_adjustment": ("LEGAL_GRATUITY_ADJUSTMENT", "income", True),
    "prior_salary_difference": ("PRIOR_SALARY_DIFFERENCE", "income", True),
    "pension_base": ("PENSION_BASE", "discount", False),
    "pension_additional": ("PENSION_ADDITIONAL", "discount", False),
    "health_base": ("HEALTH_BASE", "discount", False),
    "health_plan_additional": ("HEALTH_ADDITIONAL_UF", "discount", False),
    "health_insurance": ("HEALTH_INSURANCE", "discount", False),
    "vacation_bonus_advance": ("VACATION_BONUS_ADVANCE", "discount", False),
    "holiday_bonus_advance": ("HOLIDAY_BONUS_ADVANCE", "discount", False),
    "salary_advance": ("SALARY_ADVANCE", "discount", False),
    "prior_month_leave_absence_discount": (
        "PRIOR_MONTH_LEAVE_ABSENCE_DISCOUNT",
        "discount",
        False,
    ),
}

CONTRACT_KIND_ALIASES = {
    "indefinite": EmploymentContractKind.INDEFINITE,
    "indefinido": EmploymentContractKind.INDEFINITE,
    "fixed_term": EmploymentContractKind.FIXED_TERM,
    "fixed-term": EmploymentContractKind.FIXED_TERM,
    "plazo_fijo": EmploymentContractKind.FIXED_TERM,
    "plazo fijo": EmploymentContractKind.FIXED_TERM,
}


@dataclass(frozen=True, slots=True)
class NetPayValidation:
    """Represent Net Pay Validation."""

    employer: str
    period_year: int
    period_month: int
    declared_net_pay_clp: Decimal
    expected_net_pay_clp: Decimal
    net_pay_difference_clp: Decimal
    warning: str | None


_PAYMENT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def parse_payment_date(raw_value: object) -> pd.Timestamp | pd.NaT:
    """Parse payment date."""
    if isinstance(raw_value, pd.Timestamp):
        return raw_value
    if isinstance(raw_value, datetime):
        return pd.Timestamp(raw_value)
    if isinstance(raw_value, date):
        return pd.Timestamp(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        for date_format in _PAYMENT_DATE_FORMATS:
            payment_dt = pd.to_datetime(normalized, errors="coerce", format=date_format)
            if pd.notna(payment_dt):
                return payment_dt
    return pd.to_datetime(raw_value, errors="coerce")


def parse_worked_days(raw_value: object) -> int:
    """Parse worked days, defaulting to 30 when omitted."""
    if pd.isna(raw_value):
        return 30
    normalized = str(raw_value).strip()
    if not normalized:
        return 30
    try:
        decimal_value = Decimal(normalized)
    except ArithmeticError as exc:
        raise PayrollValidationError(
            "worked_days must be a whole number between 0 and 31."
        ) from exc
    if decimal_value != decimal_value.to_integral_value():
        raise PayrollValidationError(
            "worked_days must be a whole number between 0 and 31."
        )
    worked_days = int(decimal_value)
    if worked_days < 0 or worked_days > 31:
        raise PayrollValidationError(
            "worked_days must be a whole number between 0 and 31."
        )
    return worked_days


def parse_optional_plan_id(column_name: str, raw_value: object) -> int | None:
    """Parse an optional plan id column."""
    if pd.isna(raw_value):
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    try:
        decimal_value = Decimal(normalized)
    except ArithmeticError as exc:
        raise PayrollValidationError(
            f"{column_name} must be a whole positive integer when provided."
        ) from exc
    if decimal_value != decimal_value.to_integral_value() or decimal_value <= 0:
        raise PayrollValidationError(
            f"{column_name} must be a whole positive integer when provided."
        )
    return int(decimal_value)


def parse_optional_health_plan_ids(raw_value: object) -> tuple[int, ...] | None:
    """Parse optional health-plan ids allowing comma-separated values."""
    if pd.isna(raw_value):
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None

    plan_ids: list[int] = []
    for raw_part in (
        normalized.replace("|", ",").replace(";", ",").replace("/", ",").split(",")
    ):
        plan_id = parse_optional_plan_id("health_plan_id", raw_part.strip())
        if plan_id is not None:
            plan_ids.append(plan_id)

    if not plan_ids:
        return None
    return tuple(plan_ids)


def read_payroll_dataframe(filename: str, payload: BufferedIOBase) -> pd.DataFrame:
    """Read payroll dataframe."""
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        return pd.read_csv(payload)
    if lowered.endswith(".xlsx"):
        return pd.read_excel(payload)
    raise PayrollValidationError("Unsupported payroll file format. Use .csv or .xlsx.")


def parse_contract_kind(raw_value: object) -> EmploymentContractKind:
    """Parse contract kind."""
    normalized = str(raw_value or "").strip().lower()
    if not normalized:
        raise PayrollValidationError(
            "Every imported payroll row must include employment_contract_kind."
        )
    try:
        return CONTRACT_KIND_ALIASES[normalized]
    except KeyError as exc:
        raise PayrollValidationError(
            "Unsupported employment_contract_kind. "
            "Use one of: indefinite, fixed_term, indefinido, plazo_fijo."
        ) from exc


def extract_net_pay_validations(
    wide_df: pd.DataFrame,
) -> dict[tuple[str, int, int], NetPayValidation]:
    """Extract net pay validations."""
    validations: dict[tuple[str, int, int], NetPayValidation] = {}

    for _, row in wide_df.iterrows():
        period_str = str(row.get("period", "")).strip()
        if "/" not in period_str:
            continue

        m_str, y_str = period_str.split("/")
        employer = str(row.get("employer", "")).strip()
        if not employer:
            continue

        raw_net_pay = row.get("net_pay")
        if pd.isna(raw_net_pay):
            continue

        year = int(y_str)
        month = pd.to_datetime(m_str, format="%b").month
        declared_net_pay_clp = Decimal(str(raw_net_pay))
        expected_net_pay_clp = Decimal("0")
        for col, (_, kind, _) in CONCEPT_MAP.items():
            value = row.get(col)
            if pd.isna(value):
                continue
            amount = Decimal(str(value))
            expected_net_pay_clp += amount if kind == "income" else -amount

        net_pay_difference_clp = declared_net_pay_clp - expected_net_pay_clp
        validations[(employer, year, month)] = NetPayValidation(
            employer=employer,
            period_year=year,
            period_month=month,
            declared_net_pay_clp=declared_net_pay_clp,
            expected_net_pay_clp=expected_net_pay_clp,
            net_pay_difference_clp=net_pay_difference_clp,
            warning=None
            if net_pay_difference_clp == 0
            else (
                "Declared net_pay does not match the imported concept totals. "
                f"Difference: {net_pay_difference_clp} CLP."
            ),
        )

    return validations


def to_long_format(wide_df: pd.DataFrame) -> pd.DataFrame:
    """Pivots multi-column flat export into normalized application DTO formats."""
    long_rows: list[dict[str, object]] = []

    for _, row in wide_df.iterrows():
        period_str = str(row.get("period", "")).strip()
        if "/" not in period_str:
            continue

        m_str, y_str = period_str.split("/")
        payment_dt = parse_payment_date(row.get("payment_date"))
        if pd.isna(payment_dt):
            raise PayrollValidationError(
                "Every imported payroll row must include a valid payment_date."
            )

        employer = str(row.get("employer", "")).strip()
        if not employer:
            raise PayrollValidationError(
                "Every imported payroll row must include an employer."
            )

        base_meta = {
            "employer": employer,
            "year": int(y_str),
            "month": pd.to_datetime(m_str, format="%b").month,
            "payment_date": payment_dt.date(),
            "worked_days": parse_worked_days(row.get("worked_days")),
            "pension_plan_id": parse_optional_plan_id(
                "pension_plan_id", row.get("pension_plan_id")
            ),
            "health_plan_ids": parse_optional_health_plan_ids(
                row.get("health_plan_id")
            ),
            "status": "actual" if pd.notna(row.get("net_pay")) else "projected",
            "employment_contract_kind": parse_contract_kind(
                row.get("employment_contract_kind")
            ),
        }

        for col, (code, kind, is_tax) in CONCEPT_MAP.items():
            val = row.get(col)
            if pd.notna(val):
                long_rows.append(
                    {
                        **base_meta,
                        "concept_code": code,
                        "kind": kind,
                        "is_taxable": is_tax,
                        "amount_clp": Decimal(str(val)),
                    }
                )

    return pd.DataFrame(long_rows)


@dataclass(frozen=True, slots=True)
class XlsxPayrollImporter(PayrollImporter):
    """Tabular payroll importer backed by pandas CSV/XLSX readers."""

    def read_rows(self, filename: str, content: bytes) -> list[ImportPayrollRowDTO]:
        """Read rows."""
        dataframe = read_payroll_dataframe(filename, BytesIO(content))
        normalized = to_long_format(dataframe)
        validations = extract_net_pay_validations(dataframe)

        rows: list[ImportPayrollRowDTO] = []
        for row in normalized.to_dict(orient="records"):
            validation = validations.get(
                (str(row["employer"]), int(row["year"]), int(row["month"]))
            )
            rows.append(
                ImportPayrollRowDTO(
                    employer=str(row["employer"]),
                    period_year=int(row["year"]),
                    period_month=int(row["month"]),
                    payment_date=row["payment_date"],
                    status=row["status"],
                    employment_contract_kind=row["employment_contract_kind"],
                    concept_code=str(row["concept_code"]),
                    amount_clp=row["amount_clp"],
                    worked_days=int(row["worked_days"]),
                    pension_plan_id=None
                    if row["pension_plan_id"] is None
                    else int(row["pension_plan_id"]),
                    health_plan_id=(
                        None
                        if row["health_plan_ids"] is None
                        else int(row["health_plan_ids"][0])
                    ),
                    health_plan_ids=(
                        None
                        if row["health_plan_ids"] is None
                        else tuple(int(plan_id) for plan_id in row["health_plan_ids"])
                    ),
                    declared_net_pay_clp=None
                    if validation is None
                    else validation.declared_net_pay_clp,
                    expected_net_pay_clp=None
                    if validation is None
                    else validation.expected_net_pay_clp,
                    net_pay_difference_clp=None
                    if validation is None
                    else validation.net_pay_difference_clp,
                )
            )
        return rows
