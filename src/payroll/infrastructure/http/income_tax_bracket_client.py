"""HTTP adapter for income tax bracket lookup via pf-rates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from payroll.domain.taxes import IncomeTaxBracket
from payroll.infrastructure.http._http_client import PfRatesClientBase, pf_rates_get


class IncomeTaxBracketClient(PfRatesClientBase):
    """HTTP adapter implementing IncomeTaxBracketPort via pf-rates REST API."""

    async def get_income_tax_bracket(
        self, payment_date: date, taxable_base_utm: Decimal
    ) -> IncomeTaxBracket | None:
        """Return the bracket from pf-rates for payment_date / taxable_base_utm.

        Returns:
            The matching IncomeTaxBracket, or None if pf-rates returns 404.

        Raises:
            PayrollDependencyError: On any non-404 HTTP error or network failure.
        """
        cache_key = ("income_tax_bracket", payment_date, str(taxable_base_utm))
        hit, cached = self._cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        data = await pf_rates_get(
            f"{self._base_url}/income-tax-brackets",
            {
                "reference_date": payment_date.isoformat(),
                "taxable_base_utm": str(taxable_base_utm),
            },
            self._headers,
            label="income tax bracket",
        )
        if data is None:
            self._cache.set(cache_key, None)
            return None

        bracket = IncomeTaxBracket(
            valid_from=date.fromisoformat(str(data["valid_from"])),
            valid_to=(
                date.fromisoformat(str(data["valid_to"]))
                if data.get("valid_to") is not None
                else None
            ),
            lower_bound_utm=Decimal(str(data["lower_bound_utm"])),
            upper_bound_utm=(
                Decimal(str(data["upper_bound_utm"]))
                if data.get("upper_bound_utm") is not None
                else None
            ),
            marginal_rate=Decimal(str(data["marginal_rate"])),
            rebate_utm=Decimal(str(data["rebate_utm"])),
        )
        self._cache.set(cache_key, bracket)
        return bracket
