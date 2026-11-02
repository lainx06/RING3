import subprocess

import pytest

from tests.qemu import (
    HOST_POLICY_TEST,
    HOST_POLICY_TEST_PERMISSIVE,
    KERNEL,
    KERNEL_KTEST,
    KERNEL_TEST,
    KERNEL_TEST_PERMISSIVE,
    REPO,
)


def pytest_configure(config):
    config.addinivalue_line("markers", "hw: needs the real work cell attached (opt-in: RING3_HW=1)")


@pytest.fixture(scope="session", autouse=True)
def build_all():
    """Build every image and the host policy tests once per session (tests run the current source)."""
    subprocess.run(["make", "-s"], cwd=REPO, check=True)
    subprocess.run(["make", "-s", "TEST=1"], cwd=REPO, check=True)
    subprocess.run(["make", "-s", "KTEST=1"], cwd=REPO, check=True)
    subprocess.run(["make", "-s", "TEST=1", "POLICY=permissive"], cwd=REPO, check=True)
    subprocess.run(["make", "-s", "host-policy"], cwd=REPO, check=True)
    for p in (
        KERNEL,
        KERNEL_TEST,
        KERNEL_KTEST,
        KERNEL_TEST_PERMISSIVE,
        HOST_POLICY_TEST,
        HOST_POLICY_TEST_PERMISSIVE,
    ):
        assert p.exists(), p
