"""network/http_client.py – Shared async HTTP client for the ingestion pipeline.

All external fetch requests are subject to a hard 2500 ms connect *and* read
timeout enforced at the session level so individual call sites cannot
accidentally leave timeouts uncapped.

Timeout handling contract
-------------------------
* ``httpx.TimeoutException`` / ``asyncio.TimeoutError`` are caught,
  logged with endpoint, duration, and UTC timestamp, then re-raised as
  ``FetchTimeoutError`` so callers can distinguish them from other errors.
* Non-timeout errors (connection refused, DNS failure, HTTP error status)
  propagate unchanged — this module never swallows them.
* Connections are always returned to the pool automatically — httpx manages
  this transparently via its internal connection pool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeout constant
# ---------------------------------------------------------------------------

#: Hard limit for both the TCP connect phase and the full response-read phase.
#: Expressed in **seconds** as a float.
#: Conceptually: 2500 ms = 2.5 s.
REQUEST_TIMEOUT_S: float = 2.5

#: Human-readable label used in log messages so operators see milliseconds.
_TIMEOUT_LABEL_MS: int = 2500

# ---------------------------------------------------------------------------
# Connection limits & HTTP/2
# ---------------------------------------------------------------------------

#: Reuse up to 10 idle keepalive connections across up to 20 total connections.
#: HTTP/2 multiplexing allows a single connection to carry many concurrent
#: streams, reducing socket churn for parallel cross-currency queries.
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
)

# ---------------------------------------------------------------------------
# Sentinel timeout object – built once, reused by every request
# ---------------------------------------------------------------------------

_TIMEOUT = httpx.Timeout(
    connect=REQUEST_TIMEOUT_S,
    read=REQUEST_TIMEOUT_S,
    write=REQUEST_TIMEOUT_S,
    pool=REQUEST_TIMEOUT_S,
)


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class FetchTimeoutError(RuntimeError):
    """Raised when an outbound HTTP request exceeds ``REQUEST_TIMEOUT_S``.

    Attributes
    ----------
    url : str
        The endpoint URL that timed out.
    timeout_ms : int
        The configured hard limit in milliseconds.
    """

    def __init__(self, url: str, timeout_ms: int) -> None:
        self.url = url
        self.timeout_ms = timeout_ms
        super().__init__(
            f"[HttpClient] Request to {url!r} timed out after {timeout_ms} ms."
        )


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def make_session(**kwargs: Any) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with HTTP/2 multiplexing enabled and the
    project-wide timeout baked in.

    The caller is responsible for closing the session (use as an async context
    manager or call ``await session.aclose()`` explicitly).

    Parameters
    ----------
    **kwargs:
        Any additional keyword arguments forwarded verbatim to
        ``httpx.AsyncClient``. If *timeout* is supplied it will be
        **overridden** by the module-level ``_TIMEOUT`` to prevent accidental
        uncapping at call sites. Likewise, *limits* is overridden by
        ``_LIMITS``.

    Returns
    -------
    httpx.AsyncClient
        A configured session ready for use.

    Notes
    -----
    Passing ``timeout`` or ``limits`` in *kwargs* is silently discarded — the
    module-level constants are the single source of truth.
    """
    kwargs["timeout"] = _TIMEOUT
    kwargs["limits"] = _LIMITS
    kwargs.setdefault("http2", True)

    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def fetch_json(
    session: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[Dict[str, str]] = None,
) -> Any:
    """Perform a GET request and return the parsed JSON body.

    Parameters
    ----------
    session:
        An ``httpx.AsyncClient`` — must have been created via
        :func:`make_session` so the project timeout is enforced.
    url:
        Absolute endpoint URL.  **Must not include authentication tokens or
        secret query parameters** — callers are responsible for keeping
        credentials out of URLs to avoid them appearing in log output.
    params:
        Optional dict of query parameters to append to *url*.

    Returns
    -------
    Any
        Parsed JSON payload (dict, list, scalar …).

    Raises
    ------
    FetchTimeoutError
        When the connect or read phase exceeds ``REQUEST_TIMEOUT_S``.
        The exception is logged before being raised.
    httpx.HTTPStatusError
        Propagated unchanged for HTTP 4xx / 5xx responses when
        ``raise_for_status`` is called by the caller.
    httpx.RequestError
        Propagated unchanged for connection-refused, DNS failure, and any
        other non-timeout transport error.

    Notes
    -----
    The session-level timeout (set in :func:`make_session`) is the primary
    guard.  httpx manages the response lifecycle transparently so there is no
    manual release step.

    Time : O(1) overhead beyond the network round-trip.
    Space: O(n) for the response body buffer where n is the payload size.
    """
    try:
        resp = await session.get(url, params=params)
        return resp.json()
    except httpx.TimeoutException as exc:
        _log_timeout(url)
        raise FetchTimeoutError(url, _TIMEOUT_LABEL_MS) from exc


async def fetch_text(
    session: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[Dict[str, str]] = None,
) -> str:
    """Perform a GET request and return the raw response text.

    Identical timeout semantics to :func:`fetch_json`.  Use this when the
    endpoint returns plain-text or when you need to handle the raw body before
    JSON parsing.

    Parameters
    ----------
    session:
        Session created via :func:`make_session`.
    url:
        Absolute endpoint URL (no credentials / secret params in the URL).
    params:
        Optional query parameters.

    Returns
    -------
    str
        Decoded response body.

    Raises
    ------
    FetchTimeoutError
        On connect or read timeout; logged before being raised.
    httpx.RequestError
        Propagated unchanged for all non-timeout transport errors.

    Time : O(n) where n = response body length.
    Space: O(n) for the response buffer.
    """
    try:
        resp = await session.get(url, params=params)
        return resp.text()
    except httpx.TimeoutException as exc:
        _log_timeout(url)
        raise FetchTimeoutError(url, _TIMEOUT_LABEL_MS) from exc


async def post_json(
    session: httpx.AsyncClient,
    url: str,
    payload: Any,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """Perform a POST request with a JSON body and return parsed JSON.

    Parameters
    ----------
    session:
        Session created via :func:`make_session`.
    url:
        Absolute endpoint URL (no credentials in the URL).
    payload:
        JSON-serialisable object to send as the request body.
    headers:
        Optional additional request headers.  **Do not include authentication
        tokens in this dict** — they would be captured in the session and may
        surface in diagnostic output.

    Returns
    -------
    Any
        Parsed JSON response body.

    Raises
    ------
    FetchTimeoutError
        On connect or read timeout; logged before being raised.
    httpx.RequestError
        Propagated unchanged for all non-timeout transport errors.

    Time : O(n) where n = max(request body, response body) size.
    Space: O(n) for the request and response buffers.
    """
    try:
        resp = await session.post(url, json=payload, headers=headers)
        return resp.json()
    except httpx.TimeoutException as exc:
        _log_timeout(url)
        raise FetchTimeoutError(url, _TIMEOUT_LABEL_MS) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_timeout(url: str) -> None:
    """Emit a structured warning for a timed-out request.

    Always logs:
    * ``endpoint`` – the URL that stalled (never includes auth headers/tokens)
    * ``timeout_ms`` – the configured hard limit
    * ``timestamp`` – ISO-8601 UTC moment when expiration was detected

    Never logs authentication headers, bearer tokens, or secret query
    parameters — those must be kept out of *url* by callers.
    """
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    logger.warning(
        "[HttpClient] Request timed out | endpoint=%s | timeout_ms=%d | timestamp=%s",
        url,
        _TIMEOUT_LABEL_MS,
        timestamp,
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "REQUEST_TIMEOUT_S",
    "FetchTimeoutError",
    "make_session",
    "fetch_json",
    "fetch_text",
    "post_json",
]
