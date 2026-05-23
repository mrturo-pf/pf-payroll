"""Payroll flat-file import helpers."""

from decimal import Decimal
from io import BufferedIOBase

import pandas as pd

from payroll.domain.contributions import EmploymentContractKind

CONCEPT_MAP = {
    "salary_base": ("SALARY_BASE", "income", True),
    "monthly_legal_gratuity": ("LEGAL_GRATUITY", "income", True),
    "teleworking_refund": ("TELEWORK_REFUND", "income", False),
    "pension_base": ("PENSION_BASE", "discount", False),
    "pension_additional": ("PENSION_ADDITIONAL", "discount", False),
    "health_base": ("HEALTH_BASE", "discount", False),
    "health_additional_uf": ("HEALTH_ADDITIONAL_UF", "discount", False),
}

CONTRACT_KIND_ALIASES = {
    "indefinite": EmploymentContractKind.INDEFINITE,
    "indefinido": EmploymentContractKind.INDEFINITE,
    "fixed_term": EmploymentContractKind.FIXED_TERM,
    "fixed-term": EmploymentContractKind.FIXED_TERM,
    "plazo_fijo": EmploymentContractKind.FIXED_TERM,
    "plazo fijo": EmploymentContractKind.FIXED_TERM,
}


def parse_payment_date(raw_value: object) -> pd.Timestamp | pd.NaT:
    payment_dt = pd.to_datetime(raw_value, errors="coerce")
    if pd.notna(payment_dt):
        return payment_dt
    return pd.to_datetime(raw_value, errors="coerce", dayfirst=True)


def read_payroll_dataframe(filename: str, payload: BufferedIOBase) -> pd.DataFrame:
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        return pd.read_csv(payload)
    if lowered.endswith(".xlsx"):
        return pd.read_excel(payload)
    raise ValueError("Unsupported payroll file format. Use .csv or .xlsx.")


def parse_contract_kind(raw_value: object) -> EmploymentContractKind:
    normalized = str(raw_value or "").strip().lower()
    if not normalized:
        raise ValueError("Every imported payroll row must include employment_contract_kind.")
    try:
        return CONTRACT_KIND_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(
            "Unsupported employment_contract_kind. Use one of: indefinite, fixed_term, indefinido, plazo_fijo."
        ) from exc


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
            raise ValueError("Every imported payroll row must include a valid payment_date.")

        employer = str(row.get("employer", "")).strip()
        if not employer:
            raise ValueError("Every imported payroll row must include an employer.")

        base_meta = {
            "employer": employer,
            "year": int(y_str),
            "month": pd.to_datetime(m_str, format="%b").month,
            "payment_date": payment_dt.date(),
            "status": "actual" if pd.notna(row.get("net_pay")) else "projected",
            "employment_contract_kind": parse_contract_kind(row.get("employment_contract_kind")),
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
