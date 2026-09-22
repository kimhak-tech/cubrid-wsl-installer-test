"""CUBRID Control Tray -- TRA-001 to TRA-004, one installation for all four.

The Tray is driven through its real notification-area menu. CUBRID's state is
read independently, from `cubrid service status`, so the Tray never vouches for
its own actions. The icon's state is read from its tooltip, which the Tray sets
together with the icon.

Verifies:
- TRA-001: the menu offers About, CUBRID Start, CUBRID Stop, Guide and Exit;
  Start runs the service, turns the icon to Running and greys Start out; Stop
  stops it, turns the icon to Stopped and greys Stop out; About shows the
  installed version, status, WSL name and directory; Guide opens the installed
  guide; Exit ends the process and removes the icon
- TRA-002: a service stopped and started through the CLI turns the icon to
  Stopped and back to Running on the Tray's own poll, with no Tray interaction
- TRA-003: opening the Tray's desktop shortcut starts the Tray, with its icon
  in the tray and a working menu
- TRA-004: repeated Start/Stop cycles from the menu each move the service and
  the icon together, and the Tray stays up throughout

DESTRUCTIVE and elevated.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.windows import registry, shortcuts, tray
from cubridwsl.wsl import cubrid

# `action`: these cases START and STOP CUBRID through the menu. `tray` drives
# the real notification area, so the mouse and keyboard must be left alone.
pytestmark = [pytest.mark.tray, pytest.mark.destructive, pytest.mark.action]

RUNNING = constants.TRAY_TIP_RUNNING
STOPPED = constants.TRAY_TIP_STOPPED


# =============================================================================
# TRA-001: Every Tray menu option is available and does its job
# =============================================================================
# Open the menu, Start and Stop CUBRID from it, open About and Guide, then Exit.

def test_tra_001_every_menu_option_performs_its_function(
        suite_installation, settings, note, check_started_from_tray,
        check_stopped_from_tray):
    wsl_name = suite_installation.wsl_name
    installer = suite_installation.package

    # ----------------------------------------------------------------- #
    # 1. Set up -- the Tray is up and CUBRID is STOPPED, so Start is the
    #    enabled action and the icon has somewhere to change from
    # ----------------------------------------------------------------- #
    if not tray.is_running():
        tray.launch()
    if not cubrid.is_service_stopped(wsl_name, settings):
        cubrid.service_stop(wsl_name, settings)
    shown = tray.wait_for_icon_status(STOPPED)
    assert shown == {STOPPED}, (
        f"set-up: the Tray icon does not show {STOPPED!r} with CUBRID "
        f"stopped; the tray showed {sorted(shown)}")

    # ----------------------------------------------------------------- #
    # 2. Action / Verification -- MENU: the context menu opens and lists
    #    every option
    # ----------------------------------------------------------------- #
    menu = tray.menu_items()
    note(f"  TRA-001-menu       : {[(i.text, i.enabled) for i in menu.values()]}")
    assert tuple(menu) == constants.TRAY_MENU_ITEMS, (
        f"the Tray menu lists {list(menu)}, expected "
        f"{list(constants.TRAY_MENU_ITEMS)}")

    # ----------------------------------------------------------------- #
    # 3. Action / Verification -- START
    # ----------------------------------------------------------------- #
    tray.select("CUBRID Start")
    check_started_from_tray("after the Tray's CUBRID Start")

    # ----------------------------------------------------------------- #
    # 4. Action / Verification -- STOP
    # ----------------------------------------------------------------- #
    tray.select("CUBRID Stop")
    check_stopped_from_tray("after the Tray's CUBRID Stop")

    # ----------------------------------------------------------------- #
    # 5. Action / Verification -- ABOUT shows what is installed and running
    # ----------------------------------------------------------------- #
    about = tray.about()
    try:
        note(f"  TRA-001-about      : {about.text!r}")
        # CUBRID was stopped in step 4, and About reads the status live.
        assert "Server Status: Stopped" in about.text, (
            f"About does not report the stopped service:\n{about.text}")
        assert installer.cubrid_version in about.text, (
            f"About does not show CUBRID {installer.cubrid_version}:\n{about.text}")
        assert (f"Version: v{installer.installer_version}-{installer.build}"
                in about.text), (
            f"About does not show installer version "
            f"v{installer.installer_version}-{installer.build}:\n{about.text}")
        assert f"WSL Name: {wsl_name}" in about.text, (
            f"About does not show WSL name {wsl_name!r}:\n{about.text}")
        assert str(registry.install_dir()) in about.text, (
            f"About does not show the install directory "
            f"{registry.install_dir()}:\n{about.text}")
    finally:
        tray.close_dialog(about.window)

    # ----------------------------------------------------------------- #
    # 6. Action / Verification -- GUIDE opens the installed guide
    # ----------------------------------------------------------------- #
    viewer = tray.open_guide()
    note(f"  TRA-001-guide      : opened in window {viewer}")
    assert viewer, "Guide did not open the installed guide in a browser"
    note(f"  TRA-001-guide-tab  : closed={tray.close_viewer_tab(viewer)}")

    # ----------------------------------------------------------------- #
    # 7. Action / Verification -- EXIT ends the process and removes the icon
    # ----------------------------------------------------------------- #
    # The window handle is read BEFORE Exit: it is what the icon is registered
    # against, and asking about it afterwards is how a removed icon is told
    # apart from one a dead process left behind.
    hwnd = tray.window()
    tray.select("Exit")
    assert tray.wait_until_exited(), (
        "the Tray process is still running after Exit")
    assert not tray.is_icon_shown(hwnd), (
        "the Tray's icon is still in the notification area after Exit")


# =============================================================================
# TRA-002: The Tray follows service changes made outside it
# =============================================================================
# Stop and start CUBRID from the CLI and watch the icon follow on its own.

def test_tra_002_tray_follows_cli_service_changes(
        suite_installation, settings, note):
    wsl_name = suite_installation.wsl_name

    # ----------------------------------------------------------------- #
    # 1. Set up -- the Tray is up and CUBRID is RUNNING
    # ----------------------------------------------------------------- #
    if not tray.is_running():
        tray.launch()
    if not cubrid.is_service_started(wsl_name, settings):
        cubrid.service_start(wsl_name, settings)

    # NOTHING below opens the menu. Opening it makes the Tray query CUBRID on
    # the spot, and this case is about the Tray noticing on its own.

    # ----------------------------------------------------------------- #
    # 2. Verification -- the icon shows Running
    # ----------------------------------------------------------------- #
    shown = tray.wait_for_icon_status(RUNNING)
    assert shown == {RUNNING}, (
        f"the Tray icon does not show {RUNNING!r} with CUBRID running; "
        f"the tray showed {sorted(shown)}")

    # ----------------------------------------------------------------- #
    # 3. Action -- stop CUBRID through the CLI
    # ----------------------------------------------------------------- #
    result = cubrid.service_stop(wsl_name, settings)
    note(f"  TRA-002-cli-stop   : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 4. Verification -- the service stopped, and the icon followed
    # ----------------------------------------------------------------- #
    assert cubrid.is_service_stopped(wsl_name, settings), (
        "CUBRID is still running after `cubrid service stop`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}")

    shown = tray.wait_for_icon_status(STOPPED)
    assert shown == {STOPPED}, (
        f"the Tray icon did not change to {STOPPED!r} after a CLI stop; "
        f"the tray showed {sorted(shown)}")

    # ----------------------------------------------------------------- #
    # 5. Action -- start CUBRID through the CLI
    # ----------------------------------------------------------------- #
    result = cubrid.service_start(wsl_name, settings)
    note(f"  TRA-002-cli-start  : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 6. Verification -- the service runs, and the icon followed
    # ----------------------------------------------------------------- #
    assert cubrid.is_service_started(wsl_name, settings), (
        "CUBRID is not running after `cubrid service start`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}")

    shown = tray.wait_for_icon_status(RUNNING)
    assert shown == {RUNNING}, (
        f"the Tray icon did not change to {RUNNING!r} after a CLI start; "
        f"the tray showed {sorted(shown)}")


# =============================================================================
# TRA-003: The desktop shortcut launches the Tray
# =============================================================================
# With the Tray closed, open its desktop shortcut and use the menu.

def test_tra_003_desktop_shortcut_launches_the_tray(suite_installation, note):
    # ----------------------------------------------------------------- #
    # 1. Set up -- the Tray is NOT running: closed through its own Exit, as a
    #    user would, so its icon goes with it
    # ----------------------------------------------------------------- #
    if tray.window():
        tray.select("Exit")
    assert tray.wait_until_exited(), (
        "set-up: the Tray is still running after choosing Exit")

    # ----------------------------------------------------------------- #
    # 2. Action -- open the shortcut, as a double-click does
    # ----------------------------------------------------------------- #
    note(f"  TRA-003-shortcut   : {shortcuts.tray_shortcut_path()}")
    shortcuts.open_tray_shortcut()

    # ----------------------------------------------------------------- #
    # 3. Verification -- the Tray started, shows its icon, and its menu opens
    # ----------------------------------------------------------------- #
    # This is also what proves the shortcut's target: only the installed Tray
    # creates a CUBRIDTrayApp window, and a missing shortcut refuses to open.
    assert tray.wait_for_icon(), (
        "the Tray's icon did not appear after opening its shortcut "
        f"(process running: {tray.is_running()})")

    menu = tray.menu_items()
    assert tuple(menu) == constants.TRAY_MENU_ITEMS, (
        f"the Tray menu lists {list(menu)}, expected "
        f"{list(constants.TRAY_MENU_ITEMS)}")


# =============================================================================
# TRA-004: Repeated Start/Stop from the Tray
# =============================================================================
# Cycle CUBRID Start and Stop from the menu and check every step.

def test_tra_004_repeated_start_stop_from_the_tray(
        suite_installation, settings, note, check_started_from_tray,
        check_stopped_from_tray):
    wsl_name = suite_installation.wsl_name
    cycles = 5

    # ----------------------------------------------------------------- #
    # 1. Set up -- the Tray is up and CUBRID is STOPPED
    # ----------------------------------------------------------------- #
    if not tray.is_running():
        tray.launch()
    if not cubrid.is_service_stopped(wsl_name, settings):
        cubrid.service_stop(wsl_name, settings)
    shown = tray.wait_for_icon_status(STOPPED)
    assert shown == {STOPPED}, (
        f"set-up: the Tray icon does not show {STOPPED!r} with CUBRID "
        f"stopped; the tray showed {sorted(shown)}")

    # ----------------------------------------------------------------- #
    # 2. Action / Verification -- Start then Stop, `cycles` times. The last
    #    Stop's checks are the final state: stopped, and shown as stopped.
    #    Every check opens the live menu, which is what proves the Tray is
    #    still up and responsive -- a Tray that exited has no menu to open.
    # ----------------------------------------------------------------- #
    for cycle in range(1, cycles + 1):
        tray.select("CUBRID Start")
        check_started_from_tray(f"after CUBRID Start in cycle {cycle}")

        tray.select("CUBRID Stop")
        check_stopped_from_tray(f"after CUBRID Stop in cycle {cycle}")

        note(f"  TRA-004-cycle      : {cycle}/{cycles} complete")
