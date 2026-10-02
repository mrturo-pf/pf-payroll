"""Payroll spreadsheet export routes: bulk export + blank template.

The mirror-image direction of POST /payroll/import/spreadsheet
(routes/payroll.py) -- kept in its own router/module rather than appended to
that already-large file, for cohesion between import/export/query concerns
and to avoid growing a ~1400-line file further. Mounted on the same
`/payroll` prefix and included alongside payroll_router in
interfaces/api/main.py, so from the outside this is indistinguishable from
one router.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from payroll.application.dto import ExportPayrollFiltersDTO
from payroll.application.errors import PayrollError
from payroll.application.ports.repositories import PayrollRepository
from payroll.infrastructure.exporters.spreadsheet_exporter import (
    get_exporter_for_format,
)
from payroll.interfaces.api.dependencies import (
    build_export_payroll_use_case,
    get_payroll_repository,
)
from payroll.interfaces.api.errors import to_http_exception

router = APIRouter(prefix="/payroll", tags=["payroll"])

_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _content_disposition_headers(
    filename_stem: str, spreadsheet_format: str
) -> dict[str, str]:
    """Build the Content-Disposition header for a downloadable spreadsheet file."""
    return {
        "Content-Disposition": (
            f'attachment; filename="{filename_stem}.{spreadsheet_format}"'
        )
    }


@router.get("/spreadsheet/template")
async def get_payroll_spreadsheet_template(
    format: Literal["csv", "xlsx"] = Query("csv"),
) -> Response:
    """Download a blank CSV/XLSX template with the real export's exact column headers.

    Never reads a period -- a static shell derived from the same
    `wide_columns()` helper the real export uses (see
    infrastructure/exporters/spreadsheet_exporter.py), so it can never
    silently drift from that export's own column shape. Independently
    shippable of `GET /payroll/spreadsheet`: no PayrollRepository call, no
    database round-trip at all.
    """
    exporter = get_exporter_for_format(format)
    content = exporter.export_template()
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[format],
        headers=_content_disposition_headers("payroll-template", format),
    )


@router.get("/spreadsheet")
async def get_payroll_spreadsheet(
    format: Literal["csv", "xlsx"] = Query("csv"),
    employer: str | None = Query(None),
    period_year: int | None = Query(None),
    period_month: int | None = Query(None),
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> Response:
    """Export every persisted payroll period matching the given filters.

    Always bulk, per the design brief's own scope decision -- a filter that
    happens to match exactly one period, or no filter at all (returning
    every persisted period, mirroring GET /payroll's own no-filter
    behavior), is not a special case needing its own endpoint. Round-trip
    parity with POST /payroll/import/spreadsheet (export, then re-import
    unmodified, in both mode="validate" and mode="commit") is a tested
    acceptance criterion -- see
    tests/integration/api/test_payroll_export_roundtrip.py.
    """
    use_case = build_export_payroll_use_case(repository, format)
    try:
        content = await use_case.execute(
            ExportPayrollFiltersDTO(
                employer=employer,
                period_year=period_year,
                period_month=period_month,
            )
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return Response(
        content=content,
        media_type=_MEDIA_TYPES[format],
        headers=_content_disposition_headers("payroll-export", format),
    )
