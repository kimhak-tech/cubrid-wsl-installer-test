"""Fixtures used only by the CUBRID Control Tray scenarios."""
from __future__ import annotations

import pytest

from cubridwsl import constants, cubrid_cli, distro
from cubridwsl.drivers import tray


@pytest.fixture(scope="module")
def cubrid(silent_install, settings):
    """CUBRID CLI bound to the distribution installed for the Tray tests."""
    try:
        return cubrid_cli.for_installation(
            settings, silent_install.state.registry.wsl_name)
    except distro.DistroError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="module")
def tray_app(silent_install, cubrid):
    """Installed Tray application, restored to running service state afterward."""
    driver = tray.TrayDriver.from_registry(silent_install.state.registry)
    driver.launch()
    yield driver

    # Leave the shared installation's service usable. Do not relaunch the Tray
    # here: a GUI child can retain the runner's redirected console handles and
    # prevent an elevated wrapper from returning after pytest has finished.
    if driver.is_running():
        try:
            driver.select("Exit")
            driver.wait_until_exited(timeout=30)
        except tray.TrayError:
            pass
    awaited = constants.SERVICE_COMPONENTS_AWAITED
    if not cubrid.service_status().all_running(awaited):
        cubrid.service("start")
        cubrid.wait_for_status(lambda status: status.all_running(awaited))
