"""Installer Orchestration -- INS-002, the silent twin of INS-001.

    INS-002  Full default installation via the silent / unattended CLI

Same defaults, same assertions, different driver. The ID was REUSED on
2026-09-08: it previously meant "Select WSL2 mode explicitly", which was
superseded into INS-001's post-install block on 2026-09-07. Reports predating
2026-09-08 that cite INS-002 mean the old case.

What makes this worth a second install cycle is NOT that the assertions pass
again -- it is the DIFF. The workbook is explicit: *"Its value is the DIFF
against INS-001 -- a state comparison rather than a success check. Keep it that
way; re-listing INS-001's assertions here would recreate the duplication this
matrix was cleaned up to remove."*

So this file does three things and no more:

    1. the bundle exited acceptably, and NO UI was displayed
    2. INS-001's post-install set holds here too -- via the SAME comparison
       object, not a second copy of the assertions
    3. the resulting machine is EQUIVALENT to the one the wizard produced

DESTRUCTIVE; needs an elevated shell. ONE install cycle -- it does not build
INS-001's machine, it finds the snapshot INS-001 left. When both run in the same
session INS-001 goes first (conftest.INSTALL_FIXTURE_ORDER), so the reference is
this session's own.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants, state as state_mod
from cubridwsl.drivers import silent

pytestmark = [pytest.mark.silent, pytest.mark.destructive]


def test_ins_002_full_default_installation_via_the_silent_cli(
        silent_install, wizard_reference, check_against_bundle, note, dump_json):
    """A silent default install reaches the same machine the wizard does.

    ONE install cycle. INS-001's machine is a PRECONDITION, not work this case
    does: `wizard_reference` FINDS that snapshot -- from this session if INS-001
    already ran, otherwise from a previous run against the same bundle -- rather
    than provisioning it. Requesting the wizard fixture would give this Silent
    case a pywinauto dependency and cost ~150 seconds every run.

    SCOPE LIMIT, from the workbook: /quiet sets MSI UILevel 2 and
    ActionEnvironmentCheck never runs. On a supported host that is harmless,
    but this case proves NOTHING about prerequisite validation. Do not cite a
    green INS-002 as evidence the environment gate works -- ENV-008 and ENV-009
    own that.
    """
    problems: list[str] = []
    result = silent_install.result

    # ----------------------------------------------------------------- #
    # 1. The bundle finished, and showed nothing
    # ----------------------------------------------------------------- #
    # "Burn can return before the chained MSI completes if the process is not
    # waited on." Two things cover that: the driver uses subprocess.run, which
    # waits, and the install fixture then WAITS for the machine to settle --
    # registry key, distribution, demodb, Tray and the CUBRID components -- so
    # an exit code that arrived early cannot be mistaken for a finished install.
    note(f"  INS-002-run    : {result.describe()}")
    if result.timed_out:
        problems.append(
            f"the silent install timed out after {result.duration_seconds:.0f}s. "
            "The product's own WSL import step runs with no timeout of its own, "
            "so ours is the only backstop.")
    elif not result.ok:
        problems.append(
            f"the bundle exited {result.returncode}. INS-002 accepts 0, or "
            f"{silent.REBOOT_REQUIRED} when a reboot is pending; anything else "
            "is an install that did not happen.")

    # "Confirm no UI was displayed at any point" -- asserted from the bundle's
    # OWN log rather than from the command line we passed. The command line only
    # says what we asked for; WixBundleUILevel is Burn recording what it did.
    ui_level = silent.ui_level_from_log(result.log_path)
    note(f"  INS-002-run    : {constants.BURN_UI_LEVEL_VARIABLE} = {ui_level} "
         f"(expected {constants.BURN_UI_LEVEL_NONE} = no UI; "
         f"{constants.BURN_UI_LEVEL_PASSIVE} is /passive, "
         f"{constants.BURN_UI_LEVEL_FULL} is the wizard)")
    if ui_level != constants.BURN_UI_LEVEL_NONE:
        problems.append(
            f"the bundle recorded {constants.BURN_UI_LEVEL_VARIABLE} = "
            f"{ui_level}, not {constants.BURN_UI_LEVEL_NONE}. Under /quiet it "
            "must display nothing at all: an unattended caller has nothing to "
            "click, so any UI here is a hang waiting to happen."
            + ("" if ui_level is not None else
               " The variable is absent from the log entirely, which can also "
               "mean the bundle failed before it started."))

    # ----------------------------------------------------------------- #
    # 2. INS-001's post-install set, through the SAME comparison
    #
    # Not re-listed. `Installation.comparison` is the one verification layer
    # both drivers use, so a fact added to verify.CHECKS strengthens INS-001 and
    # INS-002 in one edit and neither track can quietly drift from the other.
    # ----------------------------------------------------------------- #
    comparison = silent_install.comparison
    dump_json("comparison-silent", comparison.as_list())
    for check in comparison.results:
        note(f"  INS-002-comparison    : {check}")
    problems.extend(comparison.problems().splitlines())

    # The two assertions the comparison structurally cannot make -- version and
    # the Add/Remove entry, both against the bundle under test. The SAME fixture
    # INS-001 uses, because the workbook requires this case to satisfy "the full
    # post-install assertion set defined in INS-001 ... version ... Add/Remove
    # entry", and a second copy of them here would be free to drift.
    problems.extend(check_against_bundle(silent_install, "INS-002"))

    cubrid = silent_install.state.cubrid
    note(f"  INS-002-service    : components -> {cubrid.service_components}")
    note(f"  INS-002-service    : brokers running -> {list(cubrid.brokers)}")

    # OPEN, from the workbook: whether the Tray auto-starts and starts CUBRID
    # under /quiet the way it does through the wizard is unconfirmed. If it does
    # not, the service and tray checks above fail HERE and pass in INS-001 --
    # and the diff below names exactly which. That is a finding to record, not
    # an assertion to loosen.
    note(f"  INS-002-tray    : mutex={silent_install.state.tray.running} "
         f"window={silent_install.state.tray.window_present} "
         "(OPEN: Tray behaviour under /quiet is unconfirmed -- see the diff)")

    # ----------------------------------------------------------------- #
    # 3. THE DIFF -- the reason this case exists
    # ----------------------------------------------------------------- #
    dump_json("diff-wizard-vs-silent", {
        "reference": wizard_reference.source if wizard_reference else None,
        "ignored": list(state_mod.DIFF_IGNORE),
        "differences": (
            state_mod.diff_reports(wizard_reference.report,
                                   silent_install.state.as_dict())
            if wizard_reference else None)})

    if wizard_reference is None:
        # The precondition is not met. Reported as a problem rather than a
        # silent pass: the diff IS this case, so a run without it has not
        # executed INS-002 -- it has executed two thirds of it.
        problems.append(
            "no INS-001 snapshot to diff against, so the DIFFERENTIAL -- the "
            "point of this case -- did not run. INS-002's preconditions "
            "require that INS-001 has been run. Either run the full suite "
            "(`run-tests.ps1 all`), or run the UI suite once against this "
            "bundle first; the snapshot is then reused from reports/.")
    else:
        differences = state_mod.diff_reports(wizard_reference.report,
                                             silent_install.state.as_dict())
        for difference in differences:
            note(f"  INS-002-diff    : {difference}")
        if differences:
            problems.append(
                "the silent install did not produce the same machine as the "
                f"wizard install ({len(differences)} field(s) differ, compared "
                f"against {wizard_reference.source}). Per INS-002 any "
                "divergence is a product defect or an undocumented default, "
                "not a test artifact -- the two paths are supposed to be the "
                "same install:\n"
                + "\n".join(f"      {d}" for d in differences))
        else:
            note("  INS-002-diff    : the two machines are identical on every "
                 f"compared field (ignoring {list(state_mod.DIFF_IGNORE)})")

    assert not problems, (
        f"INS-002 found {len(problems)} problem(s) with the silent default "
        "install:\n" + "\n".join(f"  - {problem}" for problem in problems))
