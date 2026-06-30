from __future__ import annotations

import os
import socket
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from network.http_client import (
    _KA_CNT,
    _KA_IDLE_S,
    _KA_INTVL_S,
    _KeepAliveTransport,
    _apply_keepalive,
    make_session,
)


def _mock_socket() -> MagicMock:
    sock = MagicMock(spec=socket.socket)
    return sock


# ---------------------------------------------------------------------------
# _apply_keepalive
# ---------------------------------------------------------------------------

def test_so_keepalive_enabled():
    sock = _mock_socket()
    _apply_keepalive(sock)
    sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)


def test_tcp_keepidle_set():
    sock = _mock_socket()
    _apply_keepalive(sock)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt.assert_any_call(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, _KA_IDLE_S)


def test_tcp_keepintvl_set():
    sock = _mock_socket()
    _apply_keepalive(sock)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt.assert_any_call(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, _KA_INTVL_S)


def test_tcp_keepcnt_set():
    sock = _mock_socket()
    _apply_keepalive(sock)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt.assert_any_call(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, _KA_CNT)


def test_keepidle_within_10s():
    assert _KA_IDLE_S <= 10


# ---------------------------------------------------------------------------
# _KeepAliveTransport failure tolerance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_keepalive_transport_survives_missing_socket():
    """Transport must not raise even when the underlying socket is unreachable."""
    transport = _KeepAliveTransport()
    mock_response = MagicMock()
    mock_response.stream = MagicMock()
    del mock_response.stream._connection  # no _connection attribute

    with patch.object(transport.__class__.__bases__[0], "handle_async_request", return_value=mock_response):
        result = await transport.handle_async_request(MagicMock())
        assert result is mock_response


# ---------------------------------------------------------------------------
# make_session uses _KeepAliveTransport by default
# ---------------------------------------------------------------------------

def test_make_session_uses_keepalive_transport():
    session = make_session(http2=False)
    assert isinstance(session._transport, _KeepAliveTransport)
