"""Installer Orchestration -- INS-005, cancelling the wizard mid-install.

Driven to the last page before Ready to Install and cancelled there, so the
machine has had the options page's custom actions run against it before the
user backs out.

Verifies:
- the cancel flow completes, through CustomCancelDlg to CustomUserExit
- nothing is left behind: no registry key, distribution, Run value, Apps &
  Features entry or install directory

DESTRUCTIVE in permission, elevated, drives the real mouse and keyboard. A
passing run installs nothing.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.ui, pytest.mark.destructive]


def test_ins_005_cancel_the_wizard_before_ready_to_install(
        wizard_cancelled, note, dump_json):
    """A cancelled install must leave the machine exactly as it found it.

    The rollback assertion is `reset`'s own residue reading, not a list written
    out again here. That list is what `ensure_clean` refuses on and what every
    install fixture is measured against, so this case and the framework's idea
    of "clean" cannot drift apart -- and an artifact added there is asserted
    here the same day, with no edit to this file.
    """
    problems: list[str] = []
    run = wizard_cancelled
    result = run.result

    # ----------------------------------------------------------------- #
    # THE CANCEL ACTUALLY HAPPENED
    #
    # Asserted, and asserted FIRST, because it is what makes everything below
    # mean anything. The machine assertion passes on a machine that was never
    # touched -- so a driver that gave up on the licence page would leave a
    # clean machine and report a green case that cancelled nothing at all.
    # ----------------------------------------------------------------- #
    dump_json("wizard-cancel-result", result.as_dict())
    for step in result.steps:
        note(f"  INS-005-steps    : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  INS-005-cancel    : {result.describe()}")
    if not result.ok:
        problems.append(
            f"the cancel flow did not complete: {result.error}. The machine "
            "may well be clean, but this run did not cancel an installation, "
            "so it is not evidence that cancelling one is safe.")

    # ----------------------------------------------------------------- #
    # THE ROLLBACK
    # ----------------------------------------------------------------- #
    state = run.state
    # The five artifacts `find_residue` looks at, printed raw. On a PASS the
    # residue list below is empty, so this is the only evidence in the report
    # that the reading happened at all rather than being skipped.
    note(f"  INS-005-machine    : registry={state.registry.present} "
         f"distro={state.cubrid.distro_present} "
         f"starter={state.startup.starter_registered} "
         f"tray={state.startup.tray_app_registered} "
         f"arp={state.arp.present}")

    for item in run.residue:
        note(f"  INS-005-residue    : {item}")
    if run.residue:
        problems.append(
            f"the cancelled install left {len(run.residue)} thing(s) on the "
            "machine. CustomUserExit tells the user their system has not been "
            "modified, so anything here is a claim the product makes and does "
            "not keep:\n"
            + "\n".join(f"      {item}" for item in run.residue))
    else:
        note("  INS-005-residue    : nothing left behind")

    assert not problems, (
        f"INS-005 found {len(problems)} problem(s) with the cancelled wizard "
        "install:\n" + "\n".join(f"  - {problem}" for problem in problems))
