"""Bridge tests are host-only Python: no QEMU, no kernel build, no network, no key.

The parent ``tests/conftest.py`` has an autouse session fixture that builds every kernel image. Overriding it
here by name keeps ``uv run pytest tests/bridge`` independent of ``build/`` (two-terminal rule, D-52).
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def build_all():
    """No kernel build for host-only bridge tests."""
    yield
