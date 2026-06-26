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


async def pf_rates_get(
    url: str,
    params: dict[str, str | int | float],
    headers: dict[str, str],
    *,
    label: str,
) -> dict[str, object] | None:
    """Issue a GET to pf-rates and return the parsed JSON body.

    Returns:
        Parsed JSON dict on 2xx, or None on 404.

    Raises:
        PayrollDependencyError: On any non-404 HTTP error or network failure.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
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
