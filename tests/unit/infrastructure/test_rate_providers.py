"""Tests for test rate providers."""

from datetime import date
from decimal import Decimal

from pathlib import Path
from urllib.error import URLError

import pytest

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    IncomeTaxBracketWriteDTO,
)
from payroll.infrastructure.rate_providers.chained_provider import (
    ChainedEconomicIndexProvider,
    ChainedFxProvider,
)
from payroll.infrastructure.rate_providers.official_providers import (
    BcchSeriesProvider,
    MindicadorRateProvider,
    SiiIncomeTaxBracketProvider,
    SiiIndicatorsProvider,
    _build_monthly_income_tax_brackets,
    _extract_income_tax_month_rows,
    _extract_sii_rows,
    _fetch_url,
    _parse_month_heading,
    _parse_chilean_amount,
    _parse_chilean_decimal,
)


@pytest.mark.asyncio
async def test_mindicador_rate_provider_parses_year_series_and_unknown_indicator() -> (
    None
):
    """Test parsing a year series and ignoring unknown indicators."""
    provider = MindicadorRateProvider(
        fetcher=lambda url, timeout: (
            """
        {"serie":[
          {"fecha":"2026-01-31T03:00:00.000Z","valor":38000},
          {"fecha":"2026-01-30T03:00:00.000Z","valor":37900}
        ]}
        """
        ),
    )

    result = await provider.fetch_rate("UF", date(2026, 1, 31))
    missing = await provider.fetch_rate("CLP", date(2026, 1, 31))
    entry = await provider.fetch_rate_entry("UF", date(2026, 1, 31))
    missing_entry = await provider.fetch_rate_entry("CLP", date(2026, 1, 31))

    assert result == Decimal("38000")
    assert missing is None
    assert missing_entry is None
    assert entry == ExchangeRateWriteDTO(
        currency_code="UF",
        rate_date=date(2026, 1, 31),
        value_clp=Decimal("38000"),
        source="mindicador",
    )


@pytest.mark.asyncio
async def test_mindicador_rate_provider_returns_none_on_invalid_payload() -> None:
    """Test mindicador rate provider returns none on invalid payload."""
    provider = MindicadorRateProvider(fetcher=lambda url, timeout: "{")

    assert await provider.fetch_rate("USD", date(2026, 1, 31)) is None
    provider = MindicadorRateProvider(fetcher=lambda url, timeout: '{"serie":{}}')
    assert await provider.fetch_rate("USD", date(2026, 1, 31)) is None


@pytest.mark.asyncio
async def test_sii_indicators_provider_parses_utm_and_ipc_rows() -> None:
    """Test sii indicators provider parses utm and ipc rows."""
    html = """
    <table>
      <tr><th>Mes</th><th>UTM</th><th>UTA</th><th>IPC</th><th>Mensual</th>
      <th>Acumulado</th><th>12 meses</th></tr>
      <tr><td>Enero</td><td>69.751</td><td>837.012</td><td>109,71</td><td>0,4</td><td>0,4</td><td>2,8</td></tr>
      <tr><td>Febrero</td><td>69.611</td><td>835.332</td><td>109,70</td><td>0,0</td><td>0,4</td><td>2,4</td></tr>
    </table>
    """
    provider = SiiIndicatorsProvider(fetcher=lambda url, timeout: html)

    utm = await provider.fetch_rate("UTM", date(2026, 1, 15))
    utm_entry = await provider.fetch_rate_entry("UTM", date(2026, 1, 15))
    missing_entry = await provider.fetch_rate_entry("UF", date(2026, 1, 15))
    ipc = await provider.fetch_index("IPC_CL", 2026, 2)
    unsupported = await provider.fetch_rate("UF", date(2026, 1, 15))

    assert utm == Decimal("69751")
    assert missing_entry is None
    assert utm_entry == ExchangeRateWriteDTO(
        currency_code="UTM",
        rate_date=date(2026, 1, 15),
        value_clp=Decimal("69751"),
        source="sii",
    )
    assert ipc == EconomicIndexWriteDTO(
        code="IPC_CL",
        period_year=2026,
        period_month=2,
        index_value=Decimal("109.70"),
        monthly_change=Decimal("0.0"),
        yearly_change=Decimal("2.4"),
        base_period="2023=100",
        source="sii",
    )
    assert unsupported is None


@pytest.mark.asyncio
async def test_sii_indicators_provider_returns_none_for_blank_or_missing_rows() -> None:
    """Test sii indicators provider returns none for blank or missing rows."""
    provider = SiiIndicatorsProvider(
        fetcher=lambda url, timeout: (
            "<table><tr><td>Mayo</td><td>70.588</td><td></td><td></td></tr></table>"
        )
    )
    blank_ipc_provider = SiiIndicatorsProvider(
        fetcher=lambda url, timeout: (
            "<table><tr><td>Mayo</td><td>70.588</td><td>847.056</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr></table>"
        )
    )

    assert await provider.fetch_rate("UTM", date(2026, 1, 15)) is None
    assert await provider.fetch_index("IPC_CL", 2026, 5) is None
    assert await blank_ipc_provider.fetch_index("IPC_CL", 2026, 5) is None
    assert await provider.fetch_index("UF_CL", 2026, 5) is None


@pytest.mark.asyncio
async def test_sii_indicators_provider_handles_network_failures() -> None:
    """Test sii indicators provider handles network failures."""
    provider = SiiIndicatorsProvider(
        fetcher=lambda url, timeout: (_ for _ in ()).throw(URLError("offline"))
    )

    assert await provider.fetch_rate("UTM", date(2026, 1, 15)) is None


@pytest.mark.asyncio
async def test_sii_income_tax_bracket_provider_parses_monthly_sections() -> None:
    """Test parsing SII monthly sections into UTM brackets."""
    html = """
    <div class='meses' id='mes_enero'>
      <h3>Enero 2026</h3>
      <div class='table-responsive'>
        <table><tbody>
          <tr><td><strong>MENSUAL</strong></td><td>-.-</td><td>$ 941.638,50</td>
          <td>Exento</td><td>-.-</td><td>Exento</td></tr>
          <tr><td><strong></strong></td><td>$ 941.638,51</td><td>$ 2.092.530,00</td>
          <td>0,04</td><td>$ 37.665,54</td><td>2,20%</td></tr>
          <tr><td><strong></strong></td><td>$ 2.092.530,01</td><td>Y M&Aacute;S</td>
          <td>0,4</td><td>$ 2.708.922,84</td><td>M&Aacute;S DE 27,48%</td></tr>
          <tr><td><strong>QUINCENAL</strong></td><td>-.-</td><td>$ 470.819,25</td>
          <td>Exento</td><td>-.-</td><td>Exento</td></tr>
        </tbody></table>
      </div>
    </div>
    """
    provider = SiiIncomeTaxBracketProvider(fetcher=lambda url, timeout: html)

    result = await provider.fetch_income_tax_brackets(2026)

    assert result == [
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 1, 31),
            lower_bound_utm=Decimal("0.0000"),
            upper_bound_utm=Decimal("13.5000"),
            marginal_rate=Decimal("0"),
            rebate_utm=Decimal("0.0000"),
        ),
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 1, 31),
            lower_bound_utm=Decimal("13.5000"),
            upper_bound_utm=Decimal("30.0000"),
            marginal_rate=Decimal("0.04"),
            rebate_utm=Decimal("0.5400"),
        ),
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 1, 31),
            lower_bound_utm=Decimal("30.0000"),
            upper_bound_utm=None,
            marginal_rate=Decimal("0.4"),
            rebate_utm=Decimal("38.8370"),
        ),
    ]


@pytest.mark.asyncio
async def test_sii_income_tax_bracket_provider_handles_missing_rows_and_failures() -> (
    None
):
    """Test handling missing monthly rows and network failures."""
    provider = SiiIncomeTaxBracketProvider(
        fetcher=lambda url, timeout: (
            "<h3>Enero 2026</h3><div class='table-responsive'>"
            "<table><tbody></tbody></table></div>"
        )
    )
    failing = SiiIncomeTaxBracketProvider(
        fetcher=lambda url, timeout: (_ for _ in ()).throw(URLError("offline"))
    )

    assert await provider.fetch_income_tax_brackets(2026) == []
    assert await failing.fetch_income_tax_brackets(2026) == []


@pytest.mark.asyncio
async def test_bcch_series_provider_parses_supported_shapes_and_missing_config() -> (
    None
):
    """Test parsing supported BCCH responses and missing credentials."""
    provider = BcchSeriesProvider(
        user="user",
        password="pass",
        series_codes={"UF": "UF_SERIES", "IPC_CL": "IPC_SERIES"},
        fetcher=lambda url, timeout: '{"Series":{"Obs":[{"value":"38000"}]}}',
    )
    list_provider = BcchSeriesProvider(
        user="user",
        password="pass",
        series_codes={"IPC_CL": "IPC_SERIES"},
        fetcher=lambda url, timeout: '{"Series":[{"Obs":[{"Valor":"112.340000"}]}]}',
    )
    missing_provider = BcchSeriesProvider(
        user=None, password=None, series_codes={"UF": None}
    )

    assert await provider.fetch_rate("UF", date(2026, 1, 31)) == Decimal("38000")
    assert await provider.fetch_rate_entry(
        "UF", date(2026, 1, 31)
    ) == ExchangeRateWriteDTO(
        currency_code="UF",
        rate_date=date(2026, 1, 31),
        value_clp=Decimal("38000"),
        source="bcch",
    )
    assert await missing_provider.fetch_rate_entry("UF", date(2026, 1, 31)) is None
    assert await list_provider.fetch_index("IPC_CL", 2026, 1) == EconomicIndexWriteDTO(
        code="IPC_CL",
        period_year=2026,
        period_month=1,
        index_value=Decimal("112.340000"),
        source="bcch",
    )
    assert await missing_provider.fetch_rate("UF", date(2026, 1, 31)) is None


@pytest.mark.asyncio
async def test_bcch_series_provider_handles_fetch_failures_and_empty_obs() -> None:
    """Test bcch series provider handles fetch failures and empty observations."""
    failing = BcchSeriesProvider(
        user="user",
        password="pass",
        series_codes={"UF": "UF_SERIES", "IPC_CL": "IPC_SERIES"},
        fetcher=lambda url, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    malformed = BcchSeriesProvider(
        user="user",
        password="pass",
        series_codes={"UF": "UF_SERIES", "IPC_CL": "IPC_SERIES"},
        fetcher=lambda url, timeout: '{"Series":{}}',
    )
    missing_values = BcchSeriesProvider(
        user="user",
        password="pass",
        series_codes={"UF": "UF_SERIES", "IPC_CL": "IPC_SERIES"},
        fetcher=lambda url, timeout: '{"Series":{"Obs":[{"value":null}]}}',
    )

    assert await failing.fetch_rate("UF", date(2026, 1, 31)) is None
    assert await malformed.fetch_index("IPC_CL", 2026, 1) is None
    assert await missing_values.fetch_rate("UF", date(2026, 1, 31)) is None
    assert await missing_values.fetch_index("IPC_CL", 2026, 1) is None


@pytest.mark.asyncio
async def test_chained_rate_and_index_providers_use_first_success() -> None:
    """Test chained providers stop on first success and swallow failures."""

    class FailingFx:
        """Represent Failing Fx."""

        name = "broken"

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            """Handle fetch rate."""
            raise RuntimeError("boom")

    class WorkingFx:
        """Represent Working Fx."""

        name = "mindicador"

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            """Handle fetch rate."""
            return Decimal("950.12")

    class FailingIndex:
        """Represent Failing Index."""

        name = "broken"

        async def fetch_index(
            self, code: str, period_year: int, period_month: int
        ) -> EconomicIndexWriteDTO | None:
            """Handle fetch index."""
            raise RuntimeError("boom")

    class WorkingIndex:
        """Represent Working Index."""

        name = "sii"

        async def fetch_index(
            self, code: str, period_year: int, period_month: int
        ) -> EconomicIndexWriteDTO | None:
            """Handle fetch index."""
            return EconomicIndexWriteDTO(
                code=code,
                period_year=period_year,
                period_month=period_month,
                index_value=Decimal("109.71"),
                source="sii",
            )

    fx_chain = ChainedFxProvider([FailingFx(), WorkingFx()])
    index_chain = ChainedEconomicIndexProvider([FailingIndex(), WorkingIndex()])

    assert await fx_chain.fetch_rate("USD", date(2026, 1, 31)) == Decimal("950.12")
    assert await fx_chain.fetch_rate_entry(
        "USD", date(2026, 1, 31)
    ) == ExchangeRateWriteDTO(
        currency_code="USD",
        rate_date=date(2026, 1, 31),
        value_clp=Decimal("950.12"),
        source="mindicador",
    )
    assert await index_chain.fetch_index("IPC_CL", 2026, 1) == EconomicIndexWriteDTO(
        code="IPC_CL",
        period_year=2026,
        period_month=1,
        index_value=Decimal("109.71"),
        source="sii",
    )


@pytest.mark.asyncio
async def test_chained_economic_index_provider_returns_none_when_all_miss() -> None:
    """Test chained economic index provider returns none when all providers miss."""

    class MissingIndex:
        """Represent Missing Index."""

        async def fetch_index(
            self, code: str, period_year: int, period_month: int
        ) -> EconomicIndexWriteDTO | None:
            """Handle fetch index."""
            return None

    assert (
        await ChainedEconomicIndexProvider([MissingIndex()]).fetch_index(
            "IPC_CL", 2026, 1
        )
        is None
    )


def test_rate_provider_helpers_cover_local_fetch_and_edge_parsing(
    tmp_path: Path,
) -> None:
    """Test rate provider helpers cover local fetch and edge parsing."""
    sample = tmp_path / "sample.json"
    sample.write_text('{"ok": true}', encoding="utf-8")

    assert '"ok": true' in _fetch_url(sample.as_uri(), 5)
    assert _parse_chilean_decimal(" ") is None
    assert _parse_chilean_amount("$ 38.613,24") == Decimal("38613.24")
    assert _parse_chilean_amount("Y MÁS") is None
    assert _parse_month_heading("Sin mes") is None
    assert _parse_month_heading("Foo 2026") is None
    assert _extract_sii_rows(
        "<table><tr></tr><tr><td>Enero</td><td>69.751</td></tr></table>"
    ) == {1: ["Enero", "69.751"]}
    assert _extract_income_tax_month_rows(
        "<h3>Marzo 2026</h3><div class='table-responsive'>"
        "<table><tbody><tr><td>MENSUAL</td></tr></tbody></table></div>"
    ) == {date(2026, 3, 1): [["MENSUAL"]]}
    assert (
        _extract_income_tax_month_rows(
            "<h3>Foo 2026</h3><div class='table-responsive'>"
            "<table><tbody><tr><td>MENSUAL</td></tr></tbody></table></div>"
        )
        == {}
    )
    assert _build_monthly_income_tax_brackets(date(2026, 3, 1), [["QUINCENAL"]]) == []
    assert (
        _build_monthly_income_tax_brackets(date(2026, 3, 1), [["MENSUAL", "-.-"]]) == []
    )
    assert _build_monthly_income_tax_brackets(
        date(2026, 3, 1),
        [
            ["MENSUAL", "-.-", "$ 1.350,00", "Exento", "-.-"],
            ["", "$ 1.350,01"],
            ["", "$ 2.000,00", "$ 3.000,00", "", "$ 100,00"],
        ],
    ) == [
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 3, 1),
            valid_to=date(2026, 3, 31),
            lower_bound_utm=Decimal("0.0000"),
            upper_bound_utm=Decimal("13.5000"),
            marginal_rate=Decimal("0"),
            rebate_utm=Decimal("0.0000"),
        )
    ]
