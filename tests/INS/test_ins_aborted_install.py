"""Installer Orchestration -- INS-005 and INS-006, installs that must not
happen.

Both end with the machine as clean as they found it; only what stops the
install differs: INS-005 is the user cancelling the wizard, INS-006 is the
product refusing a WSL name ActionCheckWslName rejects. That refusal is
sequenced in InstallExecuteSequence, the only sequence a silent install runs,
so it is reachable with no UI at all.

Verifies:
- INS-005: the cancel flow completes, through CustomCancelDlg to CustomUserExit
- INS-006: the bundle does NOT report success, and does not time out
- nothing is left behind by either: no registry key, distribution, Run value,
  Apps & Features entry or install directory

DESTRUCTIVE in permission, elevated; INS-005 also drives the real mouse and
keyboard. A passing run of either installs nothing.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.drivers import silent, wizard

pytestmark = [pytest.mark.destructive]


# =============================================================================
# INS-005: Cancel installation mid-wizard, before Verify Ready
# =============================================================================
# Walk the wizard partway -- to the installation-directory page, past the
# options page's custom actions -- cancel, and confirm the machine is exactly
# as clean as before.

@pytest.mark.ui
def test_ins_005_cancel_the_wizard_before_ready_to_install(
        installer, settings, run_dir, note, check_product_absent):
    # The name a wrongly completed install would have used.
    wsl_name = str(constants.INSTALL_OPTIONS[constants.OPTION_WSL_NAME])

    # ----------------------------------------------------------------- #
    # 1. Clean up -- the machine must be clean BEFORE, or "nothing left
    #    behind" afterwards could be an earlier run's residue
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-005-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- walk the wizard partway, then cancel
    # ----------------------------------------------------------------- #
    result = wizard.cancel(installer, run_dir / "ins-005-cancel.log",
                           timeout=settings["timeouts"]["install_seconds"],
                           note=note)
    for step in result.steps:
        note(f"  INS-005-step       : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  INS-005-cancel     : {result.describe()}")

    # First, because a driver that gave up early leaves a clean machine too --
    # and that run cancelled nothing.
    assert result.ok, f"the cancel flow did not complete: {result.error}"

    # ----------------------------------------------------------------- #
    # 3. Verification -- nothing left behind
    # ----------------------------------------------------------------- #
    check_product_absent(wsl_name, after="a cancel")


# =============================================================================
# INS-006: Silent install with an invalid WSL distribution name
# =============================================================================
# Install unattended with a name CheckWslName rejects, and confirm the install
# fails and leaves the machine clean.

@pytest.mark.silent
def test_ins_006_the_installer_refuses_an_invalid_wsl_name(
        installer, settings, run_dir, note, check_product_absent):
    # A colon cannot name a Windows directory, and the WSL name becomes one --
    # the class of name the check exists for.
    rejected_name = "CUBRID:FOR:WSL"
    # Where residue would land: a refused name cannot name a directory.
    default_name = str(constants.INSTALL_OPTIONS[constants.OPTION_WSL_NAME])

    # ----------------------------------------------------------------- #
    # 1. Clean up -- the machine must be clean BEFORE, or "nothing left
    #    behind" afterwards could be an earlier run's residue
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-006-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- install unattended with the invalid name
    # ----------------------------------------------------------------- #
    result = silent.install_cubrid_wsl(
        installer, settings, run_dir / "ins-006-install.log",
        options={constants.OPTION_WSL_NAME: rejected_name}, note=note)
    note(f"  INS-006-install    : {result.describe()}")
    recorded = silent.variable_from_log(result.log_path,
                                        constants.OPTION_WSL_NAME)
    note(f"  INS-006-name       : the bundle recorded {recorded!r}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- refused, and nothing left behind
    # ----------------------------------------------------------------- #
    # A name check runs before anything is written, so a timeout is not a slow
    # refusal but no refusal at all.
    assert not result.timed_out, (
        f"the install timed out after {result.duration_seconds:.0f}s instead "
        "of refusing the name")

    # No specific exit code: the product commits to failing, not to a number.
    assert not result.ok, (
        f"the bundle reported SUCCESS ({result.describe()}) for the WSL name "
        f"{rejected_name!r}, which CheckWslName rejects")

    check_product_absent(default_name, after="the refusal")
