"""Installer Orchestration -- the wizard track.

    INS-001  Full default installation via the GUI wizard

DESTRUCTIVE, and it drives the real mouse and keyboard: do not use the machine
while this runs. Needs an elevated shell and `pip install -e .[ui]`.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.ui, pytest.mark.destructive]


def test_ins_001_wizard_install_reaches_the_same_state_as_a_silent_install(
        wizard_install, note, dump_json):
    """A default install clicked through the wizard leaves the same machine.

    The driver's own verdict is RECORDED but never asserted: reaching the Finish
    button is not evidence that the product installed correctly. The machine is
    what gets judged, through the same verification layer the silent track uses
    -- which is what makes "the same assertions" structural rather than a
    promise. Adding a check to verify.CHECKS strengthens both tracks at once.
    """
    result = wizard_install.result
    dump_json("wizard-install-result", result.as_dict())
    for step in result.steps:
        note(f"  INS-001    : {step.name:<18} [{step.backend}] {step.action} "
             f"({step.seconds:.0f}s)")
    note(f"  INS-001    : {result.describe()}")

    comparison = wizard_install.comparison
    dump_json("comparison-wizard", comparison.as_list())

    problem = comparison.problems()          # every area, not a subset
    assert not problem, (
        "a wizard install did NOT reach the same state as a silent install. "
        "Either one of the drivers or the shared verification layer is wrong, "
        "and no further cases should be added until this is resolved:\n"
        + problem)
