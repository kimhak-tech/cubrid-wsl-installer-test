"""CUBRID Control Tray scenarios TRA-001 through TRA-011.

These tests use the real notification-area menu. CUBRID state is verified
independently through WSL so the UI driver cannot validate its own actions.
TRA-012 is intentionally manual: rebooting ends the pytest process and requires
an external reboot/login/resume controller that this framework does not have.
"""
from __future__ import annotations

import time

import pytest

from cubridwsl import constants, distro

pytestmark = [pytest.mark.tray, pytest.mark.destructive]


def _service_status(cubrid) -> bool:
    status = cubrid.service_status()
    assert status.ok, f"cubrid service status failed: {status.error}"
    master = status.running("master")
    if master is None:
        pytest.fail(f"could not read CUBRID master status: {status.describe()}")
    return master


def _set_service(cubrid, running: bool, *, timeout: float = 60) -> None:
    # CUBRID returns exit code 1 for the harmless idempotent cases "service is running"
    # and "service is not running". Avoid issuing that command when the requested
    # state already exists.
    action = "start" if running else "stop"
    deadline = time.monotonic() + timeout
    stable_readings = 0
    last_action = 0.0
    while time.monotonic() < deadline:
        if _service_status(cubrid) is running:
            stable_readings += 1
            if stable_readings >= 3:
                return
            time.sleep(1)
            continue
        stable_readings = 0
        # A preceding Tray operation can still own its asynchronous worker for
        # a few seconds after the master status changes. Retry convergence
        # rather than treating that transient race as a scenario failure.
        now = time.monotonic()
        if now - last_action >= 3:
            cubrid.service(action)
            last_action = time.monotonic()
        time.sleep(1)
    pytest.fail(f"CUBRID service did not converge to {action!r} within {timeout}s")


def _wait_service(cubrid, running: bool, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    latest = None
    stable_readings = 0
    while time.monotonic() < deadline:
        latest = _service_status(cubrid)
        if latest is running:
            stable_readings += 1
            if stable_readings >= 3:
                return
        else:
            stable_readings = 0
        time.sleep(1)
    pytest.fail(f"CUBRID service did not become {'running' if running else 'stopped'}; "
                f"last state={latest}")


def _ensure_running_from_tray(cubrid, tray_app) -> None:
    if _service_status(cubrid):
        return
    tray_app.wait_for_status(False)
    tray_app.select("CUBRID Start")
    _wait_service(cubrid, True)
    tray_app.wait_for_status(True)


def _wait_distro_stopped(name: str, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        remaining = max(1, int(deadline - time.monotonic()))
        entry = distro.find(name, timeout=min(10, remaining))
        assert entry is not None, f"`wsl -l -v` does not list {name!r}"
        latest = entry.state
        if latest.casefold() == "stopped":
            return
        time.sleep(1)
    pytest.fail(f"{name} did not become Stopped in `wsl -l -v`; last state={latest!r}")


# =============================================================================
# TRA-001: Launch Control Tray
# =============================================================================
# Confirm that the installed Tray launches and displays the menu items.

def test_tra_001_launch_control_tray(tray_app, note):
    tray_app.launch()

    # Verification:
    # The Tray remains running and its menu matches the specification.
    assert tray_app.is_running(), "cubrid_tray_app.exe did not remain running"
    menu = tray_app.menu_items()
    assert tuple(menu) == constants.TRAY_MENU_ITEMS, (
        f"Tray menu differs from the product specification: {list(menu)}")
    note(f"  TRA-001    : Tray running; menu={list(menu)}")


# =============================================================================
# TRA-002: Icon reflects the running service state
# =============================================================================
# Confirm that the Tray icon reflects the running-state tooltip.

def test_tra_002_icon_reflects_running_state(
        cubrid, tray_app, note):
    _set_service(cubrid, True)

    # Verification:
    # The icon's tooltip displays running-state.
    tooltip = tray_app.wait_for_tooltip(constants.TRAY_TIP_RUNNING)
    assert tooltip == constants.TRAY_TIP_RUNNING
    note(f"  TRA-002    : tooltip={tooltip!r}")


# =============================================================================
# TRA-003: Icon reflects the stopped service state
# =============================================================================
# Confirm that the Tray icon reflects the stopped-state tooltip.

def test_tra_003_icon_reflects_stopped_state(
        cubrid, tray_app, note):
    _set_service(cubrid, False)

    # Verification:
    # The icon's tooltip displays stopped-state.
    tooltip = tray_app.wait_for_tooltip(constants.TRAY_TIP_STOPPED)
    assert tooltip == constants.TRAY_TIP_STOPPED
    note(f"  TRA-003    : tooltip={tooltip!r}")

# =============================================================================
# TRA-004: Start CUBRID from the Tray menu
# =============================================================================
# Confirm that the Tray Start action starts CUBRID and updates the Tray state.

def test_tra_004_start_cubrid_from_tray(
        cubrid, tray_app, note):
    _set_service(cubrid, False)
    tray_app.wait_for_status(False)
    tray_app.select("CUBRID Start")

    # Verification:
    # The CLI reports CUBRID is running.
    # The Tray Start button is disabled and the Stop button is enabled.
    _wait_service(cubrid, True)
    assert not tray_app.menu_item_enabled("CUBRID Start")
    assert tray_app.menu_item_enabled("CUBRID Stop")
    evidence = tray_app.wait_for_status(True)
    note(f"  TRA-004    : Tray Start -> service running; UI={evidence}")


# =============================================================================
# TRA-005: Stop CUBRID from the Tray menu
# =============================================================================
# Confirm that the Tray Stop action stops CUBRID and updates the Tray state.

def test_tra_005_stop_cubrid_from_tray(
        cubrid, tray_app, note):
    _set_service(cubrid, True)
    tray_app.wait_for_status(True)
    tray_app.select("CUBRID Stop")

    # Verification:
    # The CLI reports CUBRID is stopped.
    # The Tray Stop button is disabled and the Start button is enabled.
    _wait_service(cubrid, False)
    assert tray_app.menu_item_enabled("CUBRID Start")
    assert not tray_app.menu_item_enabled("CUBRID Stop")
    evidence = tray_app.wait_for_status(False)
    note(f"  TRA-005    : Tray Stop -> service stopped; UI={evidence}")


# =============================================================================
# TRA-007: Control Tray reflects a service stopped through CLI
# =============================================================================
# Confirm that the Tray detects a service stop initiated through the CLI.

def test_tra_007_cli_stop_is_reflected_by_tray(
        cubrid, tray_app, note):
    _ensure_running_from_tray(cubrid, tray_app)
    tray_app.wait_for_status(True)
    _set_service(cubrid, False)

    # Verification:
    # The Tray menu changes to the stopped state after its status poll.
    evidence = tray_app.wait_for_status(False, timeout=25)
    note(f"  TRA-007    : external CLI stop reflected by {evidence}")

    # Cleanup: restore the shared service for later scenarios.
    _ensure_running_from_tray(cubrid, tray_app)


# =============================================================================
# TRA-008: Show correct product information in the About dialog
# =============================================================================
# Confirm that About reports the current service and installation details.

def test_tra_008_about_reports_current_product_state(
        silent_install, cubrid, tray_app, installer, note):
    _ensure_running_from_tray(cubrid, tray_app)
    info = tray_app.about()
    try:
        registry = silent_install.state.registry

        # Verification:
        #   About matches the running service, registry, and installer metadata.
        assert "Server Status: Running" in info.text
        assert f"WSL Name: {registry.wsl_name}" in info.text
        assert str(registry.install_dir) in info.text
        assert installer.cubrid_version in info.text
        assert f"Version: v{installer.installer_version}-{installer.build}" in info.text
        note("  TRA-008    : About version/status/name/path match installed state")
    finally:
        tray_app.close_dialog(info.window)


# =============================================================================
# TRA-009: Open the installed Guide document
# =============================================================================
# Confirm that Guide opens the installed cubrid_guide.html document.

def test_tra_009_guide_opens_installed_document(tray_app, note):
    # Verification:
    # The installed cubrid_guide.html opens in an external viewer.
    guide, viewer = tray_app.open_guide()
    try:
        assert guide.name == constants.TRAY_GUIDE_FILE
        assert viewer, "Guide did not open in an external viewer"
        note(f"  TRA-009    : opened {guide.name} in viewer hwnd={viewer}")
    finally:
        tray_app.close_viewer_tab(viewer)


# =============================================================================
# TRA-010: Exit the Tray and stop its WSL distribution
# =============================================================================
# Confirm that Exit terminates the Tray and stops its WSL distribution.

def test_tra_010_exit_tray_stops_wsl_distribution(
        silent_install, cubrid, tray_app, note):
    _ensure_running_from_tray(cubrid, tray_app)
    tray_app.launch()
    tray_app.select("Exit")

    # Verification:
    # The Tray disappears and `wsl -l -v` reports its distribution as Stopped.
    tray_app.wait_until_exited(timeout=30)
    assert not tray_app.is_running(), "Tray process/window still exists after Exit"
    name = silent_install.state.registry.wsl_name
    assert name, "the product registry did not record WslName"
    _wait_distro_stopped(name, timeout=30)
    note(f"  TRA-010    : Exit removed Tray; wsl -l -v reports {name} Stopped")


# =============================================================================
# TRA-011: Complete ten repeated Start/Stop cycles
# =============================================================================
# Confirm that ten Tray Start/Stop cycles complete without state desynchronization.

def test_tra_011_repeated_start_stop_cycles(
        cubrid, tray_app, note):
    tray_app.launch()
    _set_service(cubrid, False)

    # Verification:
    # Every CLI/Tray state agrees and the Tray remains running in all cycles.
    for cycle in range(1, 11):
        tray_app.wait_for_status(False)
        tray_app.select("CUBRID Start")
        _wait_service(cubrid, True)
        tray_app.wait_for_status(True)

        tray_app.select("CUBRID Stop")
        _wait_service(cubrid, False)
        tray_app.wait_for_status(False)
        assert tray_app.is_running(), f"Tray exited during cycle {cycle}"
        note(f"  TRA-011    : cycle {cycle}/10 complete")

    # The module fixture restores the service after reporting the scenario.
