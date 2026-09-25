"""Shared helper for inferring a payroll row's status from its own data."""

from decimal import Decimal
from typing import Literal


def resolve_declared_status(
    declared_net_pay_clp: Decimal | None,
) -> Literal["actual", "projected"]:
    """Infer status from whether a declared net pay is already known.

    A payroll period becomes "actual" the moment its real net pay is known
    (declared_net_pay_clp is set); until then it is "projected". Mirrors the
    exact rule xlsx_importer.py already applies for CSV/XLSX imports
    (`"actual" if pd.notna(row.get("net_pay")) else "projected"`) -- kept as
    a separate shared helper rather than reused there directly, so the
    already-working CSV/XLSX import path stays untouched; this covers the
    two newer callers that also need this same inference: the PDF preview
    (infrastructure/pdf_import/extractor.py) and POST /payroll/import/rows
    (interfaces/api/routes/payroll.py), which no longer accepts `status` as
    caller input at all -- it never appeared in the source CSV either.
    """
    return "actual" if declared_net_pay_clp is not None else "projected"
