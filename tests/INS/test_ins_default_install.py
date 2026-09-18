"""Installer Orchestration -- INS-001 and INS-002, default install two ways.

The same install with every option on its default; only the route differs:
INS-001 clicks through the wizard (`wizard.install`), INS-002 runs the bundle
with `/quiet` and no property overrides (`silent.install_cubrid_wsl`). Both end
with the same post-install assertion set (`check_post_install`).

Verifies:
- INS-001: the wizard advances through every page from Welcome to Finish
- INS-002: the bundle reports success (exit 0, or 3010 with a reboot pending)
- the post-install set holds: registry record, distribution at WSL 2, login
  environment, `cubrid_rel` version, master/broker/manager running, demodb,
  Tray running and registered, both shortcuts, Apps & Features entry

DESTRUCTIVE and elevated; INS-001 also drives the real mouse and keyboard.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.drivers import silent, wizard

pytestmark = [pytest.mark.destructive]


# =============================================================================
# INS-001: Full default installation via the GUI wizard
# =============================================================================
# Install through every wizard page on defaults, then verify everything the
# installer left behind -- without starting, stopping or connecting to
# anything.

@pytest.mark.ui
def test_ins_001_full_default_installation_via_the_gui_wizard(
        installer, settings, run_dir, note, check_post_install):
    # Every page the workbook's Steps column walks through, in order, plus
    # Burn's own bundle window either side of the MSI's dialogs.
    wizard_pages = ("bundle-welcome", "welcome", "license",
                    "environment-check", "install-options",
                    "install-directory", "verify-ready", "finish")

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-001-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- install through the wizard, every option on its default
    # ----------------------------------------------------------------- #
    result = wizard.install(installer, settings,
                            run_dir / "ins-001-install.log", note=note)
    for step in result.steps:
        note(f"  INS-001-step       : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  INS-001-install    : {result.describe()}")

    assert result.ok, f"the wizard did not complete: {result.error}"

    # Reaching the page AFTER the environment check is the evidence the
    # prerequisite gate passed: the driver raises on a blocking warning dialog.
    reached = [step.name for step in result.steps]
    missing = [page for page in wizard_pages if page not in reached]
    assert not missing, (
        f"the wizard never reached {missing}; pages reached: {reached}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- the post-install state
    # ----------------------------------------------------------------- #
    check_post_install(dict(constants.INSTALL_OPTIONS))

    # ----------------------------------------------------------------- #
    # 4. Clean up -- remove the installation
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-001-after.log", note=note)


# =============================================================================
# INS-002: Full default installation via the silent CLI
# =============================================================================
# Install unattended with every option on its default, then verify what
# INS-001 verifies.
#
# Scope limit: `/quiet` skips ActionEnvironmentCheck entirely, so a green run
# is no evidence the prerequisite gate works.

@pytest.mark.silent
def test_ins_002_full_default_installation_via_the_silent_cli(
        installer, settings, run_dir, note, check_post_install):

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-002-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- install unattended, with no property overrides
    # ----------------------------------------------------------------- #
    result = silent.install_cubrid_wsl(
        installer, settings, run_dir / "ins-002-install.log", note=note)
    note(f"  INS-002-install    : {result.describe()}")

    assert result.ok, (
        f"the silent install did not succeed: {result.describe()}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- the post-install state
    # ----------------------------------------------------------------- #
    check_post_install(dict(constants.INSTALL_OPTIONS))

    # ----------------------------------------------------------------- #
    # 4. Clean up -- remove the installation
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-002-after.log", note=note)
