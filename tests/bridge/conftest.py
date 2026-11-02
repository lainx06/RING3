"""Bridge tests are host-only Python: no QEMU, no kernel build, no network, no key.

The parent ``tests/conftest.py`` has an autouse session fixture that builds every kernel image. Overriding it
here by name keeps ``uv run pytest tests/bridge`` independent of ``build/`` (two-terminal rule, D-52).
"""

import socket

import pytest


@pytest.fixture(scope="session", autouse=True)
def build_all():
    """No kernel build for host-only bridge tests."""
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Bridge tests never reach a network: any socket connect fails the test."""

    def refuse(*_a, **_k):
        raise AssertionError("network access attempted in a bridge test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
