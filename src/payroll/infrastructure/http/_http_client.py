"""Shared async HTTP GET helper and base class for pf-rates adapters."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import structlog

from payroll.application.errors import PayrollDependencyError
from payroll.infrastructure.http._ttl_cache import TTLCache

_logger = structlog.get_logger()


class PfRatesClientBase:
    """Shared constructor for pf-rates HTTP adapters."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        cache_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a pf-rates base URL, API key, and optional cache clock.

        Args:
            base_url: Base URL of the pf-rates service (no trailing slash).
            api_key: Value for the X-API-Key authentication header.
            cache_ttl_seconds: How long to cache responses in seconds.
            clock: Injectable monotonic clock for testing TTL expiry.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        self._cache = TTLCache(cache_ttl_seconds, clock)


async def _pf_rates_request(
    url: str,
    params: dict[str, str | int | float],
    headers: dict[str, str],
    *,
    label: str,
) -> httpx.Response | None:
    """Issue a GET to pf-rates and return the raw response, or None on 404.

    Shared by pf_rates_get() (single-object endpoints) and
    pf_rates_get_list() (list endpoints, e.g. GET /economic-indices) --
    both need identical error handling, they only differ in how the JSON
    body is shaped once a response actually comes back.

    Raises:
        PayrollDependencyError: On any non-404 HTTP error or network failure.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        _logger.error(
            "pf_rates_http_error",
            label=label,
            url=url,
            status=exc.response.status_code,
        )
        raise PayrollDependencyError(
            f"pf-rates returned HTTP {exc.response.status_code} fetching {label}."
        ) from exc
    except httpx.RequestError as exc:
        _logger.error(
            "pf_rates_network_error",
            label=label,
            url=url,
            error=str(exc),
        )
        raise PayrollDependencyError(
            f"Network error fetching {label} from pf-rates: {exc}"
        ) from exc


async def pf_rates_get(
    url: str,
    params: dict[str, str | int | float],
    headers: dict[str, str],
    *,
    label: str,
) -> dict[str, object] | None:
    """Issue a GET to pf-rates and return the parsed JSON object body.

    Returns:
        Parsed JSON dict on 2xx, or None on 404.
    """
    response = await _pf_rates_request(url, params, headers, label=label)
    return response.json() if response is not None else None


async def pf_rates_get_list(
    url: str,
    params: dict[str, str | int | float],
    headers: dict[str, str],
    *,
    label: str,
) -> list[dict[str, object]] | None:
    """Issue a GET to pf-rates and return the parsed JSON list body.

    Returns:
        Parsed JSON list on 2xx, or None on 404. Note an empty list (`[]`,
        e.g. an economic index code with no stored values) is a normal 200
        response distinct from a 404 -- callers must check for both.
    """
    response = await _pf_rates_request(url, params, headers, label=label)
    return response.json() if response is not None else None
