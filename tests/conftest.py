"""Unit tests never reach the internet.

A test that forgets to mock Slack or GitHub fails at once with a clear error
here, instead of hanging on a slow network (see #160). Local servers, such as
the fake MFS server in test_tag_lifecycle, still work.
"""
from __future__ import annotations

import ipaddress
import socket

import pytest

_real_connect = socket.socket.connect


def _is_local(address) -> bool:
    if not isinstance(address, tuple):
        return True  # Unix-domain sockets are local.
    host = address[0]
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _guarded_connect(self, address):
    if not _is_local(address):
        raise RuntimeError(f"Unit test tried to reach the network ({address}); mock the call instead.")
    return _real_connect(self, address)


@pytest.fixture(autouse=True, scope="session")
def no_network():
    socket.socket.connect = _guarded_connect
    yield
    socket.socket.connect = _real_connect
