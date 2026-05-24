"""Official and public provider adapters for Chilean market data."""

from __future__ import annotations

import asyncio
import json
import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from html import unescape
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO, IncomeTaxBracketWriteDTO

_MONTHS = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}
_MONTHLY_EXEMPT_LIMIT_UTM = Decimal("13.5")
_BRACKET_UTM_QUANT = Decimal("0.0001")


def _fetch_url(url: str, timeout_seconds: int) -> str:
    """Handle fetch url."""
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def _parse_json_document(raw: str) -> dict[str, object]:
    """Handle parse json document."""
    return json.loads(raw)


def _parse_iso_date(raw: str) -> date:
    """Handle parse iso date."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _parse_chilean_decimal(raw: str) -> Decimal | None:
    """Handle parse chilean decimal."""
    cleaned = raw.replace("\xa0", "").strip()
    if not cleaned:
        return None
    normalized = cleaned.replace(".", "").replace(",", ".")
    return Decimal(normalized)


def _strip_html(raw: str) -> str:
    """Handle strip html."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _parse_chilean_amount(raw: str) -> Decimal | None:
    """Handle parse chilean amount."""
    cleaned = raw.replace("$", "").replace("-.-", "").replace("Y MÁS", "").replace("Y MAS", "").strip()
    if not cleaned:
        return None
    return _parse_chilean_decimal(cleaned)


def _extract_sii_rows(html: str) -> dict[int, list[str]]:
    """Handle extract sii rows."""
    rows_by_month: dict[int, list[str]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = [_strip_html(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)]
        if not cells:
            continue
        month = _MONTHS.get(cells[0].upper())
        if month is not None:
            rows_by_month[month] = cells
    return rows_by_month


def _parse_month_heading(raw: str) -> date | None:
    """Handle parse month heading."""
    match = re.search(r"([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{4})", raw)
    if match is None:
        return None
    month = _MONTHS.get(unescape(match.group(1)).upper())
    if month is None:
        return None
    return date(int(match.group(2)), month, 1)


def _extract_income_tax_month_rows(html: str) -> dict[date, list[list[str]]]:
    """Handle extract income tax month rows."""
    rows_by_month: dict[date, list[list[str]]] = {}
    for section in re.finditer(
        r"<h3>(.*?)</h3>\s*<div class=['\"]table-responsive['\"][^>]*>.*?<tbody>(.*?)</tbody>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        month_start = _parse_month_heading(_strip_html(section.group(1)))
        if month_start is None:
            continue
        rows_by_month[month_start] = [
            [_strip_html(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", section.group(2), flags=re.IGNORECASE | re.DOTALL)
            if re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
        ]
    return rows_by_month


def _quantize_bracket_utm(value: Decimal) -> Decimal:
    """Handle quantize bracket utm."""
    return value.quantize(_BRACKET_UTM_QUANT)


def _build_monthly_income_tax_brackets(
    valid_from: date,
    rows: list[list[str]],
) -> list[IncomeTaxBracketWriteDTO]:
    """Handle build monthly income tax brackets."""
    monthly_rows: list[list[str]] = []
    collecting = False
    for row in rows:
        period_label = row[0].upper() if row else ""
        if period_label:
            if period_label == "MENSUAL":
                collecting = True
            elif collecting:
                break
            else:
                continue
        if collecting:
            monthly_rows.append(row)

    if not monthly_rows:
        return []

    first_upper_clp = _parse_chilean_amount(monthly_rows[0][2]) if len(monthly_rows[0]) > 2 else None
    if first_upper_clp is None or first_upper_clp <= 0:
        return []

    utm_value = first_upper_clp / _MONTHLY_EXEMPT_LIMIT_UTM
    valid_to = date(valid_from.year, valid_from.month, monthrange(valid_from.year, valid_from.month)[1])
    lower_bound_utm = Decimal("0.0000")
    brackets: list[IncomeTaxBracketWriteDTO] = []

    for row in monthly_rows:
        if len(row) < 5:
            continue
        upper_clp = _parse_chilean_amount(row[2])
        factor = Decimal("0") if row[3].upper() == "EXENTO" else _parse_chilean_decimal(row[3])
        if factor is None:
            continue
        rebate_clp = _parse_chilean_amount(row[4]) or Decimal("0")
        upper_bound_utm = _quantize_bracket_utm(upper_clp / utm_value) if upper_clp is not None else None
        brackets.append(
            IncomeTaxBracketWriteDTO(
                valid_from=valid_from,
                valid_to=valid_to,
                lower_bound_utm=lower_bound_utm,
                upper_bound_utm=upper_bound_utm,
                marginal_rate=factor,
                rebate_utm=_quantize_bracket_utm(rebate_clp / utm_value),
            )
        )
        if upper_bound_utm is not None:
            lower_bound_utm = upper_bound_utm

    return brackets


class MindicadorRateProvider:
    """Provide mindicador rate provider."""

    name = "mindicador"
    _CODE_MAP = {"UF": "uf", "UTM": "utm", "USD": "dolar", "EUR": "euro"}

    def __init__(
        self,
        base_url: str = "https://mindicador.cl/api",
        timeout_seconds: int = 10,
        fetcher: Callable[[str, int], str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher or _fetch_url

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        indicator = self._CODE_MAP.get(currency_code.upper())
        if indicator is None:
            return None

        url = f"{self._base_url}/{indicator}/{on.year}"
        try:
            payload = _parse_json_document(await asyncio.to_thread(self._fetcher, url, self._timeout_seconds))
        except (HTTPError, URLError, json.JSONDecodeError):
            return None

        series = payload.get("serie")
        if not isinstance(series, list):
            return None

        matching_values = [
            Decimal(str(entry["valor"]))
            for entry in series
            if isinstance(entry, dict)
            and "fecha" in entry
            and "valor" in entry
            and _parse_iso_date(str(entry["fecha"])) <= on
            and _parse_iso_date(str(entry["fecha"])).year == on.year
        ]
        return matching_values[0] if matching_values else None

    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        value = await self.fetch_rate(currency_code, on)
        if value is None:
            return None
        return ExchangeRateWriteDTO(currency_code=currency_code.upper(), rate_date=on, value_clp=value, source=self.name)


class SiiIndicatorsProvider:
    """Provide sii indicators provider."""

    name = "sii"

    def __init__(
        self,
        base_url: str = "https://www.sii.cl",
        timeout_seconds: int = 10,
        fetcher: Callable[[str, int], str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher or _fetch_url

    async def _get_rows(self, year: int) -> dict[int, list[str]]:
        """Handle get rows."""
        url = f"{self._base_url}/valores_y_fechas/utm/utm{year}.htm"
        try:
            html = await asyncio.to_thread(self._fetcher, url, self._timeout_seconds)
        except (HTTPError, URLError):
            return {}
        return _extract_sii_rows(html)

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        if currency_code.upper() != "UTM":
            return None
        row = (await self._get_rows(on.year)).get(on.month)
        if row is None or len(row) < 2:
            return None
        return _parse_chilean_decimal(row[1])

    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        value = await self.fetch_rate(currency_code, on)
        if value is None:
            return None
        return ExchangeRateWriteDTO(currency_code=currency_code.upper(), rate_date=on, value_clp=value, source=self.name)

    async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        if code.upper() != "IPC_CL":
            return None
        row = (await self._get_rows(period_year)).get(period_month)
        if row is None or len(row) < 6:
            return None

        index_value = _parse_chilean_decimal(row[3])
        if index_value is None:
            return None

        return EconomicIndexWriteDTO(
            code="IPC_CL",
            period_year=period_year,
            period_month=period_month,
            index_value=index_value,
            monthly_change=_parse_chilean_decimal(row[4]),
            yearly_change=_parse_chilean_decimal(row[6]) if len(row) > 6 else None,
            base_period="2023=100",
            source=self.name,
        )


class SiiIncomeTaxBracketProvider:
    """Provide sii income tax bracket provider."""

    name = "sii"

    def __init__(
        self,
        base_url: str = "https://www.sii.cl",
        timeout_seconds: int = 10,
        fetcher: Callable[[str, int], str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher or _fetch_url

    async def fetch_income_tax_brackets(self, year: int) -> list[IncomeTaxBracketWriteDTO]:
        """Handle fetch income tax brackets."""
        url = f"{self._base_url}/valores_y_fechas/impuesto_2da_categoria/impuesto{year}.htm"
        try:
            html = await asyncio.to_thread(self._fetcher, url, self._timeout_seconds)
        except (HTTPError, URLError):
            return []

        brackets: list[IncomeTaxBracketWriteDTO] = []
        for valid_from, rows in sorted(_extract_income_tax_month_rows(html).items()):
            brackets.extend(_build_monthly_income_tax_brackets(valid_from, rows))
        return brackets


class BcchSeriesProvider:
    """Provide bcch series provider."""

    name = "bcch"

    def __init__(
        self,
        user: str | None,
        password: str | None,
        series_codes: dict[str, str | None],
        base_url: str = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx",
        timeout_seconds: int = 10,
        fetcher: Callable[[str, int], str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._user = user
        self._password = password
        self._series_codes = {key.upper(): value for key, value in series_codes.items()}
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher or _fetch_url

    async def _fetch_series(self, code: str, start: date, end: date) -> list[dict[str, object]]:
        """Handle fetch series."""
        series_code = self._series_codes.get(code.upper())
        if not self._user or not self._password or not series_code:
            return []

        query = urlencode(
            {
                "user": self._user,
                "pass": self._password,
                "function": "GetSeries",
                "timeseries": series_code,
                "firstdate": start.strftime("%d-%m-%Y"),
                "lastdate": end.strftime("%d-%m-%Y"),
            }
        )
        url = f"{self._base_url}?{query}"
        try:
            payload = _parse_json_document(await asyncio.to_thread(self._fetcher, url, self._timeout_seconds))
        except (HTTPError, URLError, json.JSONDecodeError):
            return []

        series = payload.get("Series")
        if isinstance(series, dict) and isinstance(series.get("Obs"), list):
            return [entry for entry in series["Obs"] if isinstance(entry, dict)]
        if isinstance(series, list) and series and isinstance(series[0], dict) and isinstance(series[0].get("Obs"), list):
            return [entry for entry in series[0]["Obs"] if isinstance(entry, dict)]
        return []

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        observations = await self._fetch_series(currency_code, on, on)
        for observation in observations:
            raw_value = observation.get("value") or observation.get("Valor") or observation.get("obs_value")
            if raw_value is None:
                continue
            return Decimal(str(raw_value).replace(",", "."))
        return None

    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        value = await self.fetch_rate(currency_code, on)
        if value is None:
            return None
        return ExchangeRateWriteDTO(currency_code=currency_code.upper(), rate_date=on, value_clp=value, source=self.name)

    async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        month_date = date(period_year, period_month, 1)
        observations = await self._fetch_series(code, month_date, month_date)
        for observation in observations:
            raw_value = observation.get("value") or observation.get("Valor") or observation.get("obs_value")
            if raw_value is None:
                continue
            return EconomicIndexWriteDTO(
                code=code.upper(),
                period_year=period_year,
                period_month=period_month,
                index_value=Decimal(str(raw_value).replace(",", ".")),
                source=self.name,
            )
        return None
