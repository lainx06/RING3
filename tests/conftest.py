import subprocess

import pytest

from tests.qemu import KERNEL, REPO


@pytest.fixture(scope="session", autouse=True)
def build_kernel():
    """Build the kernel once per session so tests always run the current source."""
    subprocess.run(["make", "-s"], cwd=REPO, check=True)
    assert KERNEL.exists()
