from datetime import date
from decimal import Decimal

from pathlib import Path
from urllib.error import URLError

import pytest

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO
from payroll.infrastructure.rate_providers.chained_provider import ChainedEconomicIndexProvider, ChainedFxProvider
from payroll.infrastructure.rate_providers.official_providers import (
    BcchSeriesProvider,
    MindicadorRateProvider,
    SiiIndicatorsProvider,
    _extract_sii_rows,
    _fetch_url,
    _parse_chilean_decimal,
)


@pytest.mark.asyncio
async def test_mindicador_rate_provider_parses_year_series_and_handles_unknown_indicator() -> None:
    provider = MindicadorRateProvider(
        fetcher=lambda url, timeout: """
        {"serie":[
          {"fecha":"2026-01-31T03:00:00.000Z","valor":38000},
          {"fecha":"2026-01-30T03:00:00.000Z","valor":37900}
        ]}
        """,
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
    provider = MindicadorRateProvider(fetcher=lambda url, timeout: "{")

    assert await provider.fetch_rate("USD", date(2026, 1, 31)) is None
    provider = MindicadorRateProvider(fetcher=lambda url, timeout: '{"serie":{}}')
    assert await provider.fetch_rate("USD", date(2026, 1, 31)) is None


@pytest.mark.asyncio
async def test_sii_indicators_provider_parses_utm_and_ipc_rows() -> None:
    html = """
    <table>
      <tr><th>Mes</th><th>UTM</th><th>UTA</th><th>IPC</th><th>Mensual</th><th>Acumulado</th><th>12 meses</th></tr>
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
    provider = SiiIndicatorsProvider(fetcher=lambda url, timeout: "<table><tr><td>Mayo</td><td>70.588</td><td></td><td></td></tr></table>")
    blank_ipc_provider = SiiIndicatorsProvider(
        fetcher=lambda url, timeout: "<table><tr><td>Mayo</td><td>70.588</td><td>847.056</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr></table>"
    )

    assert await provider.fetch_rate("UTM", date(2026, 1, 15)) is None
    assert await provider.fetch_index("IPC_CL", 2026, 5) is None
    assert await blank_ipc_provider.fetch_index("IPC_CL", 2026, 5) is None
    assert await provider.fetch_index("UF_CL", 2026, 5) is None


@pytest.mark.asyncio
async def test_sii_indicators_provider_handles_network_failures() -> None:
    provider = SiiIndicatorsProvider(fetcher=lambda url, timeout: (_ for _ in ()).throw(URLError("offline")))

    assert await provider.fetch_rate("UTM", date(2026, 1, 15)) is None


@pytest.mark.asyncio
async def test_bcch_series_provider_parses_supported_shapes_and_handles_missing_configuration() -> None:
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
    missing_provider = BcchSeriesProvider(user=None, password=None, series_codes={"UF": None})

    assert await provider.fetch_rate("UF", date(2026, 1, 31)) == Decimal("38000")
    assert await provider.fetch_rate_entry("UF", date(2026, 1, 31)) == ExchangeRateWriteDTO(
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
async def test_bcch_series_provider_handles_fetch_failures_and_empty_observations() -> None:
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
async def test_chained_rate_and_index_providers_use_first_successful_source_and_swallow_failures() -> None:
    class FailingFx:
        name = "broken"

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            raise RuntimeError("boom")

    class WorkingFx:
        name = "mindicador"

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            return Decimal("950.12")

    class FailingIndex:
        name = "broken"

        async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
            raise RuntimeError("boom")

    class WorkingIndex:
        name = "sii"

        async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
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
    assert await fx_chain.fetch_rate_entry("USD", date(2026, 1, 31)) == ExchangeRateWriteDTO(
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
async def test_chained_economic_index_provider_returns_none_when_all_providers_miss() -> None:
    class MissingIndex:
        async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
            return None

    assert await ChainedEconomicIndexProvider([MissingIndex()]).fetch_index("IPC_CL", 2026, 1) is None


def test_rate_provider_helpers_cover_local_fetch_and_edge_parsing(tmp_path: Path) -> None:
    sample = tmp_path / "sample.json"
    sample.write_text('{"ok": true}', encoding="utf-8")

    assert '"ok": true' in _fetch_url(sample.as_uri(), 5)
    assert _parse_chilean_decimal(" ") is None
    assert _extract_sii_rows("<table><tr></tr><tr><td>Enero</td><td>69.751</td></tr></table>") == {1: ["Enero", "69.751"]}
