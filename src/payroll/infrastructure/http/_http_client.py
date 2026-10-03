"""Shared async HTTP GET helper and base class for pf-rates adapters."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx
import structlog

from payroll.application.errors import PayrollDependencyError
from payroll.infrastructure.http._ttl_cache import TTLCache

_logger = structlog.get_logger()

# Network-level failures (timeouts, connection resets, DNS errors -- anything
# below the HTTP layer) get a few retries with a short backoff before this
# gives up: pf-rates' own GET /exchange-rates/value falls through a chained
# provider cascade (BCCH -> SII -> Mindicador, each allowed up to
# rate_provider_timeout_seconds=10s) whenever the requested date isn't
# already cached in its DB, and httpx's default client timeout (~5s, no
# override configured here) can easily be shorter than that whole cascade
# under concurrent load -- confirmed in practice (2026-10-02 investigation):
# bursts of `pf_rates_network_error` in this client correlated exactly with
# `mindicador_fetch_failed`/`bcch_credentials_not_configured` on pf-rates'
# side, for dates whose USD/EUR rate wasn't pre-cached. Without a retry,
# every one of those transient hiccups degrades to the exact same `null` a
# genuine "no rate published for this date" 404 produces -- indistinguishable
# to any caller. A real HTTP error status (4xx/5xx other than 404) is never
# retried here: that is pf-rates itself reporting a real, already-final
# failure, not a transient network blip.
_MAX_NETWORK_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (0.5, 1.0)  # delay before attempt 2, then before attempt 3


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
    params: dict[str, str | int | float] | None,
    headers: dict[str, str],
    *,
    label: str,
    method: str = "GET",
    json_body: dict[str, object] | None = None,
) -> httpx.Response | None:
    """Issue a GET to pf-rates and return the raw response, or None on 404.

    Shared by pf_rates_get() (single-object endpoints) and
    pf_rates_get_list() (list endpoints, e.g. GET /economic-indices) --
    both need identical error handling, they only differ in how the JSON
    body is shaped once a response actually comes back.

    A 404 is a final, already-correct answer (returned immediately, no
    retry, no log -- it is not an error). An HTTP error status is also
    final (pf-rates itself reporting a real failure) and raises on the
    first attempt. A network-level failure (`httpx.RequestError` --
    timeout, connection reset, DNS error) is retried up to
    `_MAX_NETWORK_RETRY_ATTEMPTS` times with a short backoff before raising
    -- see the module docstring comment above `_MAX_NETWORK_RETRY_ATTEMPTS`
    for why this specific failure mode is worth retrying.

    Raises:
        PayrollDependencyError: On any non-404 HTTP error, or a network
            failure that persisted across every retry attempt.
    """
    attempt = 1
    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _logger.error(
                "pf_rates_http_error",
                label=label,
                url=url,
                params=params,
                status=exc.response.status_code,
            )
            raise PayrollDependencyError(
                f"pf-rates returned HTTP {exc.response.status_code} fetching {label}."
            ) from exc
        except httpx.RequestError as exc:
            exhausted = attempt >= _MAX_NETWORK_RETRY_ATTEMPTS
            _logger.warning(
                "pf_rates_network_error",
                label=label,
                url=url,
                params=params,
                attempt=attempt,
                max_attempts=_MAX_NETWORK_RETRY_ATTEMPTS,
                will_retry=not exhausted,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if exhausted:
                raise PayrollDependencyError(
                    f"Network error fetching {label} from pf-rates after "
                    f"{_MAX_NETWORK_RETRY_ATTEMPTS} attempts: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
            attempt += 1
            continue
        else:
            if attempt > 1:
                _logger.info(
                    "pf_rates_network_retry_succeeded",
                    label=label,
                    url=url,
                    params=params,
                    attempt=attempt,
                )
            return response


async def pf_rates_get(
    url: str,
    params: dict[str, str | int | float] | None,
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


async def pf_rates_post(
    url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    *,
    label: str,
) -> dict[str, object] | None:
    """Issue a POST to pf-rates and return its parsed JSON object body."""
    response = await _pf_rates_request(
        url,
        None,
        headers,
        label=label,
        method="POST",
        json_body=payload,
    )
    return response.json() if response is not None else None


async def pf_rates_get_list(
    url: str,
    params: dict[str, str | int | float] | None,
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
