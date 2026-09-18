"""Lifecycle Management -- LCM-001 and LCM-002, the two uninstall routes.

Same steps and the same verification; only the route differs: LCM-001 goes
through Apps & Features (`apps.uninstall_cubrid_wsl`), LCM-002 re-runs the
installer bundle and drives its maintenance page (`wizard.uninstall_cubrid_wsl`).

Verifies:
- the route removes the product: Apps & Features entry, WSL distro, registry
  key, both Run values, Tray process and binary, and all files on disk
- LCM-002 additionally: re-running the bundle is DETECTED via its maintenance
  page rather than starting a fresh install

DESTRUCTIVE and elevated; LCM-002 also drives the real mouse and keyboard.
"""
from __future__ import annotations

import pytest

from cubridwsl.drivers import silent, wizard
from cubridwsl.windows import apps, registry, tray

pytestmark = [pytest.mark.destructive]


# =============================================================================
# LCM-001: Uninstall through Windows Apps & Features
# =============================================================================
# Install, remove with the exact command Windows runs from the Uninstall
# button, and confirm the product is completely gone.

@pytest.mark.silent
def test_lcm_001_uninstall_through_apps_and_features(installer, settings,
                                                     run_dir, note,
                                                     check_product_absent):

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings, run_dir / "01-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Set up -- install CUBRID for WSL, so there is something to remove
    # ----------------------------------------------------------------- #
    silent.install_cubrid_wsl(installer, settings, run_dir / "02-setup.log", note=note)

    if not registry.exists():
        pytest.fail("CUBRID for WSL is not installed after the set-up step")

    # Read now -- the uninstall removes the key that carries both.
    wsl_name = registry.wsl_name()
    tray_binary = tray.binary_path()
    note(f"  LCM-001-installed  : distro={wsl_name!r} tray={tray_binary}")
    note(f"  LCM-001-entry      : {apps.read().describe()}")

    # ----------------------------------------------------------------- #
    # 3. Action -- uninstall through Windows Apps & Features
    # ----------------------------------------------------------------- #
    # The machine's own UninstallString, with the Tray left running on purpose:
    # whether the product copes with it is part of the test.
    result = apps.uninstall_cubrid_wsl(settings, run_dir / "03-uninstall.log", note=note)
    note(f"  LCM-001-uninstall  : {result.describe()}")

    if result.timed_out:
        pytest.fail(f"the uninstall timed out after {result.duration_seconds:.0f}s, "
                    "leaving the machine mid-removal -- see 03-uninstall.log")

    # ----------------------------------------------------------------- #
    # 4. Verification
    # ----------------------------------------------------------------- #
    # The exit code is not checked: ActionUninstallWsl is Return="ignore", so
    # the bundle reports success whether or not the distribution went.
    check_product_absent(wsl_name, after="the Apps & Features uninstall",
                         tray_binary=tray_binary)

    # ----------------------------------------------------------------- #
    # 5. Clean up -- remove anything the uninstall under test did not
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings, run_dir / "04-cleanup.log", note=note)


# =============================================================================
# LCM-002: Uninstall by re-running the same installer bundle
# =============================================================================
# Install, launch the SAME bundle again, confirm it offers to remove the
# existing installation instead of installing afresh, and confirm it is gone.

@pytest.mark.ui
def test_lcm_002_uninstall_by_rerunning_the_same_bundle(installer, settings,
                                                        run_dir, note,
                                                        check_product_absent):

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings, run_dir / "01-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Set up -- install with THE BUNDLE THIS CASE RE-RUNS
    # ----------------------------------------------------------------- #
    silent.install_cubrid_wsl(installer, settings, run_dir / "02-setup.log", note=note)

    if not registry.exists():
        pytest.fail("CUBRID for WSL is not installed after the set-up step")

    # Read now -- the uninstall removes the key that carries both.
    wsl_name = registry.wsl_name()
    tray_binary = tray.binary_path()
    note(f"  LCM-002-installed  : distro={wsl_name!r} tray={tray_binary}")

    # ----------------------------------------------------------------- #
    # 3. Action -- re-run the bundle and click through Uninstall
    # ----------------------------------------------------------------- #
    # No switches: the double-click the workbook describes.
    result = wizard.uninstall_cubrid_wsl(installer, settings,
                                         run_dir / "03-uninstall.log", note=note)
    for step in result.steps:
        note(f"  LCM-002-step       : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  LCM-002-uninstall  : {result.describe()}")

    if result.timed_out:
        pytest.fail(f"the wizard walk timed out after {result.duration_seconds:.0f}s, "
                    "leaving the machine mid-removal -- see 03-uninstall.log")

    # ----------------------------------------------------------------- #
    # 4. Verification
    # ----------------------------------------------------------------- #
    # Reaching the maintenance page IS the detection: DisableModify=yes leaves
    # it one action, so a bundle that had offered a fresh install instead has no
    # Uninstall button for the walk to click.
    assert not result.error, (
        f"re-running the installed bundle did not complete an uninstall: {result.error}")

    check_product_absent(wsl_name, after="the maintenance-page uninstall",
                         tray_binary=tray_binary)

    # ----------------------------------------------------------------- #
    # 5. Clean up -- remove anything the uninstall under test did not
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings, run_dir / "04-cleanup.log", note=note)
