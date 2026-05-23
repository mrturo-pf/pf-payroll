"""WeasyPrint adapter for payroll period PDF reports."""

from decimal import Decimal
from html import escape

from payroll.application.dto import PayrollPeriodDetailDTO


def _format_clp(amount: Decimal) -> str:
    return f"${amount:,.0f}".replace(",", ".")


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(lines: list[str]) -> bytes:
    content = "\n".join(
        [
            "BT",
            "/F1 12 Tf",
            "72 790 Td",
            "14 TL",
            *(f"({_escape_pdf_text(line)}) Tj T*" for line in lines),
            "ET",
        ]
    ).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        b"5 0 obj << /Length "
        + str(len(content)).encode()
        + b" >> stream\n"
        + content
        + b"\nendstream\nendobj",
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for obj in objects:
        offsets.append(sum(len(part) for part in parts))
        parts.append(obj + b"\n")
    xref_offset = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode())
    parts.append(b"0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    parts.append(
        (
            "trailer << /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".format(
                size=len(objects) + 1,
                xref=xref_offset,
            )
        ).encode()
    )
    return b"".join(parts)


class WeasyPrintPayrollReportRenderer:
    """Renders payroll period details into a styled PDF document."""

    def render_payroll_period(self, detail: PayrollPeriodDetailDTO) -> bytes:
        summary = detail.summary
        assert summary is not None

        rows = "".join(
            (
                "<tr>"
                f"<td>{escape(item.concept_code)}</td>"
                f"<td>{escape(item.concept_name)}</td>"
                f"<td>{escape(item.kind)}</td>"
                f"<td>{'Yes' if item.is_taxable else 'No'}</td>"
                f"<td class='amount'>{_format_clp(item.amount_clp)}</td>"
                f"<td>{escape(item.notes) if item.notes else ''}</td>"
                "</tr>"
            )
            for item in detail.items
        )
        html = f"""
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <style>
      @page {{ size: A4; margin: 24px; }}
      body {{ font-family: Arial, sans-serif; color: #1f2937; font-size: 12px; }}
      h1 {{ margin: 0 0 8px; font-size: 24px; }}
      h2 {{ margin: 20px 0 8px; font-size: 16px; }}
      .meta, .summary-grid {{ width: 100%; border-collapse: collapse; }}
      .meta td {{ padding: 4px 8px 4px 0; vertical-align: top; }}
      .summary-grid td {{ border: 1px solid #d1d5db; padding: 10px; width: 25%; }}
      .label {{ color: #6b7280; font-size: 11px; text-transform: uppercase; }}
      .value {{ font-size: 14px; font-weight: bold; margin-top: 4px; }}
      table.items {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
      table.items th, table.items td {{ border: 1px solid #d1d5db; padding: 6px; text-align: left; }}
      table.items th {{ background: #f3f4f6; }}
      .amount {{ text-align: right; }}
    </style>
  </head>
  <body>
    <h1>Payroll Period Report</h1>
    <table class="meta">
      <tr>
        <td><span class="label">Employer</span><div>{escape(detail.employer_name)}</div></td>
        <td><span class="label">Period</span><div>{detail.period_month:02d}/{detail.period_year}</div></td>
        <td><span class="label">Payment date</span><div>{detail.payment_date.isoformat()}</div></td>
      </tr>
      <tr>
        <td><span class="label">Status</span><div>{escape(detail.status)}</div></td>
        <td><span class="label">Contract kind</span><div>{escape(detail.employment_contract_kind.value)}</div></td>
        <td><span class="label">Worked days</span><div>{detail.worked_days}</div></td>
      </tr>
    </table>
    <h2>Summary</h2>
    <table class="summary-grid">
      <tr>
        <td><div class="label">Taxable income</div><div class="value">{_format_clp(summary.taxable_income_clp)}</div></td>
        <td><div class="label">Gross income</div><div class="value">{_format_clp(summary.gross_income_clp)}</div></td>
        <td><div class="label">Discounts</div><div class="value">{_format_clp(summary.total_discounts_clp)}</div></td>
        <td><div class="label">Net pay</div><div class="value">{_format_clp(summary.net_pay_clp)}</div></td>
      </tr>
    </table>
    <h2>Items</h2>
    <table class="items">
      <thead>
        <tr>
          <th>Code</th>
          <th>Concept</th>
          <th>Kind</th>
          <th>Taxable</th>
          <th class="amount">Amount</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </body>
</html>
"""
        fallback_lines = [
            "Payroll Period Report",
            f"Employer: {detail.employer_name}",
            f"Period: {detail.period_month:02d}/{detail.period_year}",
            f"Payment date: {detail.payment_date.isoformat()}",
            f"Status: {detail.status}",
            f"Contract kind: {detail.employment_contract_kind.value}",
            f"Worked days: {detail.worked_days}",
            f"Taxable income: {_format_clp(summary.taxable_income_clp)}",
            f"Gross income: {_format_clp(summary.gross_income_clp)}",
            f"Discounts: {_format_clp(summary.total_discounts_clp)}",
            f"Net pay: {_format_clp(summary.net_pay_clp)}",
            "Items:",
            *[
                f"- {item.concept_code} | {item.concept_name} | {_format_clp(item.amount_clp)}"
                for item in detail.items
            ],
        ]
        try:
            from weasyprint import HTML

            return HTML(string=html).write_pdf()
        except (ImportError, OSError):
            return _build_pdf(fallback_lines)
