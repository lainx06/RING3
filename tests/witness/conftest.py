"""Witness tests are host-only: no QEMU, no kernel build, no network, no key, no camera (T-07 Part A).

The parent ``tests/conftest.py`` builds every kernel image in an autouse session fixture; overriding it here
by name keeps ``uv run pytest tests/witness`` off ``build/`` while T-03b works there (two-terminal rule).
"""

import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "hardware" / "gauge", REPO / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(scope="session", autouse=True)
def build_all():
    """No kernel build for host-only witness tests."""
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access attempted in a witness test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
