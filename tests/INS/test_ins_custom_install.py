"""Installer Orchestration -- INS-003 and INS-004, options off their defaults.

Both change what the installer is asked for, and each verifies the change
landed plus INS-001's post-install set against the machine it produced:
INS-003 changes every option the wizard offers, INS-004 turns WSL 2 mode off
from the unattended command line.

INS-003 is wizard-only: the bundle does not forward INSTALLFOLDER, and
ActionUpdateInstallFolder overwrites it under UILevel < 5, so no silent command
line can choose an install directory.

Verifies:
- INS-003: the wizard completes with demodb, Tray auto-start and shortcuts
  unticked, a custom WSL name and a custom install directory; the post-install
  set holds with those inversions; nothing was written to the directory
  derived from the WSL name
- INS-004: the post-install set holds with the distribution at VERSION 1

DESTRUCTIVE and elevated; INS-003 also drives the real mouse and keyboard.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path

import pytest

from cubridwsl import constants
from cubridwsl.drivers import silent, wizard

pytestmark = [pytest.mark.destructive]


# =============================================================================
# INS-003: Install with every option changed via the GUI wizard
# =============================================================================
# Change every option the wizard offers, install, and verify each change landed
# on its own artifact while the rest of the installation is unaffected.

@pytest.mark.ui
def test_ins_003_install_with_every_option_changed_via_the_wizard(
        installer, settings, run_dir, note, check_post_install):
    # The name and directory are deliberately DIFFERENT: the default directory
    # is %LOCALAPPDATA%\<WslName>, so a directory that followed the custom name
    # would still be the default one and prove nothing.
    #
    # REG_TRAY_APP off while START_TRAY_APP stays on is deliberate too: the two
    # Tray settings in opposition show they act independently.
    changed = {constants.OPTION_WSL_NAME: "CUBRID-WSL",
               "REG_TRAY_APP": 0, "CREATE_SHORTCUT": 0, "CREATE_DEMODB": 0}
    options = {**constants.INSTALL_OPTIONS, **changed}
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    install_dir = ntpath.join(local_appdata, "CUBRID-INS003-Dir")
    derived_dir = ntpath.join(local_appdata,
                              changed[constants.OPTION_WSL_NAME])

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-003-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- install through the wizard with every option changed
    # ----------------------------------------------------------------- #
    result = wizard.install(installer, settings,
                            run_dir / "ins-003-install.log", options=changed,
                            install_dir=install_dir, note=note)
    for step in result.steps:
        note(f"  INS-003-step       : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  INS-003-install    : {result.describe()}")

    assert result.ok, f"the wizard did not complete: {result.error}"

    # ----------------------------------------------------------------- #
    # 3. Verification -- the post-install state, with the inversions
    # ----------------------------------------------------------------- #
    check_post_install(options, install_dir=install_dir)

    # ActionUpdateInstallFolder sets the derived path before the directory page
    # overrides it, so the payload must have gone to the chosen path ONLY.
    assert not Path(derived_dir).is_dir(), (
        f"the install went to {install_dir} as chosen, but {derived_dir} -- "
        "the directory derived from the WSL name -- was created too")

    # ----------------------------------------------------------------- #
    # 4. Clean up -- remove the installation
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-003-after.log", note=note)


# =============================================================================
# INS-004: Disable WSL2 mode explicitly
# =============================================================================
# Install unattended with IS_WSL2_MODE=0 and verify the installation runs at
# WSL 1.
#
# A WSL 1 distribution is also what a host whose default version is already 1
# produces on its own -- and INS-004 leaves it that way, since the product
# never sets it back -- so this shows a WSL 1 install works, not that the
# option was honoured.

@pytest.mark.silent
def test_ins_004_install_in_wsl1_mode_via_the_silent_cli(
        installer, settings, run_dir, note, check_post_install):
    # The ONLY override, so the machine differs from INS-002's by one setting.
    changed = {constants.OPTION_WSL2_MODE: 0}

    # ----------------------------------------------------------------- #
    # 1. Clean up -- remove anything an earlier run left behind
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-004-cleanup.log", note=note)

    # ----------------------------------------------------------------- #
    # 2. Action -- install unattended at WSL 1
    # ----------------------------------------------------------------- #
    result = silent.install_cubrid_wsl(installer, settings,
                                       run_dir / "ins-004-install.log",
                                       options=changed, note=note)
    note(f"  INS-004-install    : {result.describe()}")

    assert result.ok, (
        f"the silent install did not succeed: {result.describe()}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- the post-install state, at WSL 1
    # ----------------------------------------------------------------- #
    check_post_install({**constants.INSTALL_OPTIONS, **changed})

    # ----------------------------------------------------------------- #
    # 4. Clean up -- remove the installation
    # ----------------------------------------------------------------- #
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / "ins-004-after.log", note=note)
