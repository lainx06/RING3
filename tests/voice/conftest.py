"""T-19 host-only voice tests: no QEMU, no kernel build, no network, no key (the QEMU test sits in tests/)."""

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "tools")
)  # the Windows helper's protocol functions


@pytest.fixture(scope="session", autouse=True)
def build_all():
    """No kernel build for host-only tests."""
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access attempted in a voice test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
