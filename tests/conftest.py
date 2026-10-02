"""Unit tests never reach the internet.

A test that forgets to mock Slack or GitHub fails at once with a clear error
here, instead of hanging on a slow network (see #160). Local servers, such as
the fake MFS server in test_tag_lifecycle, still work.
"""
from __future__ import annotations

import ipaddress
import os
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


if os.name == "nt":
    # Many tests replace os.environ entirely. On macOS and Linux, Path.home()
    # then falls back to the password database; Windows has no such fallback
    # and raises. Give tests the same fallback, to the real home folder.
    import ntpath

    _real_home = ntpath.expanduser("~")
    _real_expanduser = ntpath.expanduser

    def _expanduser(path):
        text = os.fspath(path)
        if (isinstance(text, str) and text[:1] == "~" and text[1:2] in {"", "\\", "/"}
                and not any(key in os.environ for key in ("USERPROFILE", "HOME", "HOMEPATH"))):
            return _real_home + text[1:]
        return _real_expanduser(path)

    ntpath.expanduser = _expanduser
