"""Installer Orchestration -- INS-004, every option off its default.

    INS-004  Install with every option changed from its default, via the GUI wizard

Consolidated on 2026-09-08 from five single-option cases (install path, custom
distro name, demodb off, Tray auto-start off, shortcuts off): 3 installs became
1. That is safe because the five effects land on DISJOINT artifacts -- the
InstallDir registry value, the distro name, databases.txt, the Run key and the
desktop .lnk files -- so combining them costs no fault isolation. A failure
still names exactly one artifact.

The positive reason to combine, from the workbook: an install with every option
off its default is the configuration most likely to expose an option-INTERACTION
defect, which single-option cases structurally cannot find. This matrix already
holds one of that family -- ActionRegisterStarterApp registering
CUBRID_WSL_Starter unconditionally.

WHY THIS WAS BLOCKED: the option checkboxes are declared `Text=" "` with their
captions in separate sibling controls, so none of them exposes an accessible
name and none can be found by title. The driver pairs each box with the label on
its row instead (constants.OPTION_LABELS). That is a workaround for a product
defect -- report the missing accessible names against TOOLS-4932; with them, all
of it becomes a lookup by name.

Wizard-only, and not by preference: the bundle forwards six properties to the
MSI and INSTALLFOLDER is not among them, and ActionUpdateInstallFolder overwrites
the folder under `UILevel < 5` regardless -- so no silent command line can
express an install directory. See verify._install_dir_for.

DESTRUCTIVE, and it drives the real mouse and keyboard. Needs an elevated shell
and `pip install -e .[ui]`.
"""
from __future__ import annotations

import ntpath
import os
import pathlib

import pytest

from cubridwsl import constants, distro
from conftest import INS_004_WSL_NAME

pytestmark = [pytest.mark.ui, pytest.mark.destructive]


def test_ins_004_install_with_every_option_changed_via_the_wizard(
        wizard_all_custom_install, check_against_bundle, note, dump_json):
    """Every option effect, the Tray settings in opposition, and the baseline.

    Structured like INS-001, and for the same reason: the COMPARISON does the
    asserting, because every expectation there is derived from the options this
    machine was installed with -- untick a box and the expectation moves with
    it. The three inversions the workbook calls out (demodb absent, TrayApp
    absent, shortcuts absent) therefore need no special handling at all.

    What the code below adds is EVIDENCE, not assertions: the artifact values
    themselves, so that diagnosing a failure does not mean reinstalling to see
    what the machine said. Each install cycle here costs about two and a half
    minutes.
    """
    problems: list[str] = []
    installation = wizard_all_custom_install
    state = installation.state

    # ----------------------------------------------------------------- #
    # INSTALL COMPLETION
    # ----------------------------------------------------------------- #
    result = installation.result
    dump_json("wizard-all-custom-result", result.as_dict())
    for step in result.steps:
        note(f"  INS-004-steps    : {step.name:<18} [{step.backend}] {step.action} "
             f"({step.seconds:.0f}s)")
    note(f"  INS-004-steps    : {result.describe()}")
    if result.timed_out or result.error:
        problems.append(f"the wizard did not complete: {result.error}")

    # ----------------------------------------------------------------- #
    # THE OPTION EFFECTS, THE TRAY SETTINGS IN OPPOSITION, AND THE BASELINE
    #
    # One comparison covers all of it. Every check is named and evaluated
    # separately, which is what the workbook asks for -- "verify the five option
    # effects independently ... a failure in one cannot mask another".
    #
    # The pair that is easy to confuse, and the reason this case exists:
    #
    #   startup.tray_app  REG_TRAY_APP   -- registered for the next logon: OFF
    #   tray.running      START_TRAY_APP -- the process is up right now:   ON
    #
    # Left in OPPOSITION deliberately. It is the configuration that shows the
    # two settings act independently, and neither a default install nor a fully
    # minimal one shows it.
    #
    # verify.CHECKS is the inventory of what is asserted; it is not repeated
    # here, because a copy of a list is a copy that goes stale.
    # ----------------------------------------------------------------- #
    comparison = installation.comparison
    dump_json("comparison-wizard-all-custom", comparison.as_list())
    for check in comparison.results:
        note(f"  INS-004-comparison    : {check}")
    problems.extend(comparison.problems().splitlines())

    # ----------------------------------------------------------------- #
    # THE ARTIFACTS THEMSELVES -- evidence for the checks above, no assertions.
    # Printed in the workbook's order, so a run's notes can be walked straight
    # down the row: install directory, distro name, demodb, Tray, shortcuts.
    # ----------------------------------------------------------------- #
    note(f"  INS-004-options    : InstallDir = {state.registry.install_dir}")
    note(f"  INS-004-options    : WslName    = {state.registry.wsl_name!r}")
    entry = distro.find(str(state.registry.wsl_name or ""))
    note(f"  INS-004-options    : wsl -l -v  -> "
         f"{f'{entry.name} VERSION={entry.version}' if entry else '<absent>'}")
    note(f"  INS-004-options    : databases  -> {list(state.cubrid.databases)}")
    note(f"  INS-004-options    : {constants.RUN_VALUE_TRAY}  = "
         f"{state.startup.tray_app!r}")
    note(f"  INS-004-options    : {constants.RUN_VALUE_STARTER} = "
         f"{state.startup.starter!r} (unconditional -- never assert its ABSENCE)")
    for label, shortcut in (("distro", state.shortcuts.distro),
                            ("tray", state.shortcuts.tray)):
        note(f"  INS-004-options    : shortcut[{label}] {shortcut.describe()}")
    note(f"  INS-004-tray    : running={state.tray.running} "
         f"window={state.tray.window_present} "
         f"registered={state.startup.tray_app_registered}")

    # ----------------------------------------------------------------- #
    # The one assertion the comparison structurally cannot make.
    #
    # It compares the registry against the directory that was CHOSEN. What it
    # cannot see is the directory that would have been DERIVED had the choice
    # been ignored -- [LocalAppDataFolder][CUB_DEFAULT_WSL_NAME], which
    # ActionUpdateInstallFolder sets on the Next of the options page, before
    # InstallSelectDirDlg overrides it. That path is not machine state the
    # snapshot reads, and INS-004 is the only case where it differs from the
    # chosen one -- so nothing else can check that the payload was written
    # ONCE, to the chosen path, rather than to both.
    # ----------------------------------------------------------------- #
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        derived = ntpath.join(local_appdata, INS_004_WSL_NAME)
        note(f"  INS-004-options    : derived-but-not-chosen path {derived} "
             f"exists={pathlib.Path(derived).is_dir()}")
        if pathlib.Path(derived).is_dir():
            problems.append(
                f"the install went to {state.registry.install_dir} as asked, but "
                f"{derived} was created too. That is the path derived from the "
                "WSL name, which ActionUpdateInstallFolder sets before the "
                "directory page overrides it -- so the payload was written to "
                "both, and an uninstall that removes only the registered one "
                "leaves the other behind.")

    # ----------------------------------------------------------------- #
    # The assertions against the BUNDLE UNDER TEST, in a fixture because
    # INS-001 and INS-002 make exactly the same ones. Two copies would drift,
    # and drift here is invisible: both keep passing while checking different
    # things.
    # ----------------------------------------------------------------- #
    problems.extend(check_against_bundle(installation, "INS-004"))

    # ----------------------------------------------------------------- #
    # Reported on every run, passing or failing: an assertion nobody can see
    # missing is indistinguishable from one that passed.
    # ----------------------------------------------------------------- #
    note("  INS-004-gap    : GAP -- the shortcut NAMING rule is unverifiable in "
         "this configuration. The desktop shortcut is named <WslName>.lnk, and "
         "shortcuts are OFF here, while INS-001 creates them under the DEFAULT "
         "name. Nothing covers the derivation; add a dedicated case if it "
         "matters.")
    note("  INS-004-gap    : GAP -- Windows optional features and shortcut "
         "targets are not asserted, as in INS-001.")

    assert not problems, (
        f"INS-004 found {len(problems)} problem(s) with the all-custom wizard "
        "install:\n" + "\n".join(f"  - {problem}" for problem in problems))
