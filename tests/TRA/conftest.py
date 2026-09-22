"""Fixtures used only by the CUBRID Control Tray scenarios."""
from __future__ import annotations

from typing import Callable, Iterator

import pytest

from cubridwsl import constants
from cubridwsl.provisioning import InstalledProduct, provide_installation
from cubridwsl.windows import tray
from cubridwsl.wsl import cubrid

RUNNING = constants.TRAY_TIP_RUNNING
STOPPED = constants.TRAY_TIP_STOPPED
AWAITED = constants.SERVICE_COMPONENTS_AWAITED


@pytest.fixture(scope="module")
def suite_installation(installer, settings, run_dir,
                       note) -> Iterator[InstalledProduct]:
    """One installation for every case in test_tra_control_tray.py.

        uninstall  ->  install  ->  TRA-001 .. TRA-004  ->  uninstall

    The cases share it, so each one BEGINS by putting the Tray and CUBRID into
    the state it needs -- TRA-001 ends by exiting the Tray, TRA-003 needs it
    closed -- rather than trusting the case before it to have left them there.

    The closing uninstall also stops the Tray, so nothing here has to: a Tray
    this run launched would otherwise hold the runner's console handles open
    and keep an elevated wrapper from returning.
    """
    yield from provide_installation(installer, settings, run_dir, note, "tra")


@pytest.fixture
def check_started_from_tray(suite_installation, settings) -> Callable[[str], None]:
    """CUBRID Start was chosen from the menu: the service, the icon and the
    menu all agree. `when` names the step in every failure message.

    The service is waited for in FULL -- master, broker and manager -- before
    the Tray is read. The Tray ignores a menu action while its previous one is
    still running, so the next Stop would be silently dropped otherwise.
    """
    wsl_name = suite_installation.wsl_name

    def _check(when: str) -> None:
        status = cubrid.wait_for_status(
            wsl_name, lambda s: s.all_running(AWAITED), settings)
        assert status.all_running(AWAITED), (
            f"CUBRID is not running {when}:\n{status.raw}")

        shown = tray.wait_for_icon_status(RUNNING)
        assert shown == {RUNNING}, (
            f"the Tray icon did not change to {RUNNING!r} {when}; the tray "
            f"showed {sorted(shown)}")

        assert not tray.menu_item_enabled("CUBRID Start"), (
            f"CUBRID Start is still available {when}")
        assert tray.menu_item_enabled("CUBRID Stop"), (
            f"CUBRID Stop is not available {when}")

    return _check


@pytest.fixture
def check_stopped_from_tray(suite_installation, settings) -> Callable[[str], None]:
    """CUBRID Stop was chosen from the menu: the service, the icon and the
    menu all agree. `when` names the step in every failure message."""
    wsl_name = suite_installation.wsl_name

    def _check(when: str) -> None:
        status = cubrid.wait_for_status(
            wsl_name, lambda s: not s.any_running(AWAITED), settings)
        assert not status.any_running(AWAITED), (
            f"CUBRID is still running {when}:\n{status.raw}")

        shown = tray.wait_for_icon_status(STOPPED)
        assert shown == {STOPPED}, (
            f"the Tray icon did not change to {STOPPED!r} {when}; the tray "
            f"showed {sorted(shown)}")

        assert not tray.menu_item_enabled("CUBRID Stop"), (
            f"CUBRID Stop is still available {when}")
        assert tray.menu_item_enabled("CUBRID Start"), (
            f"CUBRID Start is not available {when}")

    return _check
