"""Installer Orchestration -- INS-001, full default install via the wizard.

OWNS the post-install assertion set: everything observable after a successful
install without performing an action. Six other cases reference it rather than
restating it, so a check added to `verify.CHECKS` strengthens all of them.

Verifies:
- the wizard advances every page from Welcome to Finish on defaults
- the post-install set in `verify.CHECKS` holds -- registry, distro and WSL
  version, service components, brokers, demodb, login environment, startup
  entry, both shortcuts, Apps & Features
- `cubrid_rel` and the Apps & Features version match the bundle under test

DESTRUCTIVE, elevated, and it drives the real mouse and keyboard.
"""
from __future__ import annotations

import ntpath

import pytest

from cubridwsl import constants

pytestmark = [pytest.mark.ui, pytest.mark.destructive]

# Every page the workbook's Steps column walks through, in order:
#   Welcome -> License -> EnvironmentCheck -> InstallOptions -> InstallSelectDir
#   -> VerifyReady -> (install) -> Finish
# plus Burn's own bundle window either side of the MSI's dialogs.
WIZARD_PAGES = ("bundle-welcome", "welcome", "license", "environment-check",
                "install-options", "install-directory", "verify-ready",
                "finish")


def test_ins_001_full_default_installation_via_the_gui_wizard(
        wizard_install, check_against_bundle, note, dump_json):
    """The full wizard flow on defaults, and the complete post-install state.

    The workbook's Expected Result has two sections and this test follows them:

    **INSTALL COMPLETION** -- the wizard ran end to end with every default
    component present. Proceeding past the environment gate is the assertion

    **POST-INSTALL STATE -- OBSERVED, NO ACTION PERFORMED.** The boundary is
    quoted from the workbook because it decides what may ever be added here:
    *only assertions that require NO action belong here. Anything that starts,
    stops, connects, queries or creates is a Category 03 case, because it is
    exercising behaviour rather than reading installed state.* Nothing below
    issues a start, a stop, a connect or a create.
    """
    problems: list[str] = []

    # ----------------------------------------------------------------- #
    # INSTALL COMPLETION
    # ----------------------------------------------------------------- #
    result = wizard_install.result
    dump_json("wizard-install-result", result.as_dict())
    for step in result.steps:
        note(f"  INS-001-steps    : {step.name:<18} [{step.backend}] {step.action} "
             f"({step.seconds:.0f}s)")
    note(f"  INS-001-steps    : {result.describe()}")

    if result.timed_out or result.error:
        problems.append(f"the wizard did not complete: {result.error}")

    # The driver's verdict is RECORDED but never treated as evidence of a
    # correct install -- reaching Finish proves the wizard ran, not that the
    # product installed. What is asserted is the FLOW.
    #
    # The environment-check page is where ENV-001 lives: the driver cannot slip
    # past a prerequisite warning silently, because check_for_blocking_dialog
    # runs on every poll of every wait and raises on a fatal one. So reaching
    # the NEXT page is the evidence that the gate passed.
    reached = [step.name for step in result.steps]
    missing = [page for page in WIZARD_PAGES if page not in reached]
    if missing:
        problems.append(
            f"the wizard never reached these pages: {missing}. Pages reached: "
            f"{reached}. INS-001 is the only case exercising the full wizard "
            "flow, so a page dropping out of it is coverage lost with nothing "
            "else to catch it.")

    # ----------------------------------------------------------------- #
    # ALL DEFAULT COMPONENTS + most of the POST-INSTALL STATE
    #
    # One comparison covers both, because both ask the same question: does the
    # machine match the options it was installed with? Every expectation is
    # DERIVED from those options rather than hard-coded, which is what will let
    # INS-003, INS-004 and INS-005 reuse this unchanged -- INS-003 inverts
    # demodb simply by installing with CREATE_DEMODB=0.
    #
    # Two pairs are easy to confuse, and are checked separately for that reason:
    #
    #   startup.tray_app  the Tray is REGISTERED for the next logon (REG_TRAY_APP)
    #   tray.running      the Tray process is up RIGHT NOW        (START_TRAY_APP)
    #     -- two different options; INS-003 installs them in OPPOSITION, which
    #        is what proves they act independently.
    #
    #   service.broker             the broker service is up
    #   service.broker.<name>      that broker is actually running
    #     -- a broker service with an empty table serves nothing.
    #
    # verify.CHECKS is the inventory of what is asserted; it is not repeated
    # here, because a copy of a list is a copy that goes stale.
    # ----------------------------------------------------------------- #
    comparison = wizard_install.comparison
    dump_json("comparison-wizard", comparison.as_list())
    for check in comparison.results:
        note(f"  INS-001-comparison    : {check}")
    problems.extend(comparison.problems().splitlines())

    # The components are asserted by the comparison above and are NOT
    # re-asserted here. What this adds is EVIDENCE: a component verdict is a
    # reading of parsed text, so the text itself goes in -- otherwise
    # diagnosing a failure means reinstalling to see what the machine said.
    #
    # The fixture already WAITED for these, so a startup still in progress is
    # never the explanation for a False.
    cubrid = wizard_install.state.cubrid
    note(f"  INS-001-service    : components -> {cubrid.service_components}")
    note(f"  INS-001-service    : brokers running -> {list(cubrid.brokers)}")
    for line in (cubrid.service_status_raw or "<empty>").splitlines():
        note(f"  INS-001-service    :   | {line}")

    # The Tray, by two independent probes. A mutex with no window is a Tray
    # that started and failed to initialise -- which `tray.running` alone
    # cannot tell you, so both readings are printed.
    tray = wizard_install.state.tray
    note(f"  INS-001-tray    : mutex={tray.running} window={tray.window_present}")

    # ----------------------------------------------------------------- #
    # Tray auto-start registration
    #
    # The comparison above already asserts BOTH Run values, and asserts them
    # SEPARATELY -- which is the whole point of this one. CUBRID_WSL_Starter is
    # written on every install regardless of the option, because
    # ActionRegisterStarterApp carries no condition, so "no CUBRID entries under
    # Run" fails against a correct build. It is the obvious way to write this
    # assertion and it is wrong.
    #
    # What is added here is the Run value's COMMAND. A presence-only check is
    # satisfied by a stale entry from an earlier install, while auto-start at
    # the next logon launches a deleted executable.
    # ----------------------------------------------------------------- #
    startup = wizard_install.state.startup
    install_dir = wizard_install.state.registry.install_dir
    note(f"  INS-001-startup    : {constants.RUN_VALUE_TRAY}  = {startup.tray_app!r}")
    note(f"  INS-001-startup    : {constants.RUN_VALUE_STARTER} = "
         f"{startup.starter!r} (unconditional -- never assert its ABSENCE)")

    if startup.tray_app and install_dir:
        if ntpath.normcase(str(install_dir)) not in ntpath.normcase(startup.tray_app):
            problems.append(
                f"the {constants.RUN_VALUE_TRAY} Run value is "
                f"{startup.tray_app!r}, which does not point inside this "
                f"install's directory ({install_dir}). "
                "That is a stale entry from an earlier install: auto-start at "
                "logon would launch the wrong executable, or none.")

    # ----------------------------------------------------------------- #
    # Desktop shortcuts
    #
    # Only EXISTENCE is asserted, by the comparison above. Both links are still
    # fully parsed and reported here, so the evidence for the target-and-icon
    # assertions keeps arriving while they are switched off -- verify.CHECKS
    # carries the three that are commented out and why. On the day the product
    # is fixed, the run notes will already show whether re-enabling them passes.
    # ----------------------------------------------------------------- #
    shortcuts = wizard_install.state.shortcuts
    for label, shortcut in (("distro", shortcuts.distro), ("tray", shortcuts.tray)):
        note(f"  INS-001-shortcuts    : [{label}] {shortcut.describe()}")
        note(f"  INS-001-shortcuts    : [{label}] resolves -> {shortcut.resolution}")

    # ----------------------------------------------------------------- #
    # The assertions against the BUNDLE UNDER TEST
    #
    # In a fixture, not here, because INS-002 must make exactly the same ones
    # -- the workbook requires the silent install to satisfy "the full
    # post-install assertion set defined in INS-001". Two copies would drift,
    # and drift here is invisible: both keep passing while checking different
    # things.
    # ----------------------------------------------------------------- #
    problems.extend(check_against_bundle(wizard_install, "INS-001"))

    assert not problems, (
        f"INS-001 found {len(problems)} problem(s) with the default wizard "
        "install:\n" + "\n".join(f"  - {problem}" for problem in problems))
