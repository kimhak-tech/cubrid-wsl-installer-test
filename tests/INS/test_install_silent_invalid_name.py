"""Installer Orchestration -- INS-006, an unattended install the product refuses.

ActionCheckWslName and ActionInvalidWslNameError are sequenced in
InstallExecuteSequence, the only sequence a silent install runs, so the refusal
is reachable with no UI at all.

Verifies:
- the bundle does NOT report success
- nothing is left behind: no registry key, distribution, Run value, Apps &
  Features entry or install directory

DESTRUCTIVE in permission, elevated. A passing run installs nothing.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.drivers import silent

pytestmark = [pytest.mark.silent, pytest.mark.destructive]


def test_ins_006_the_installer_refuses_an_invalid_wsl_name(
        silent_invalid_name, note, dump_json):
    """A rejected name must cost nothing -- not a partial install, not residue.

    Two assertions, and they are independent on purpose. A refusal that leaves
    the machine dirty and an install that succeeds when it should not are
    different defects, and either one alone is a finding: ActionUninstallWsl is
    declared Return="ignore", so this product has form for reporting an outcome
    its machine does not match.

    No specific exit code is asserted. The product commits to failing, not to a
    number, and pinning 1603 here would make a correct build fail the day WiX
    surfaces the error differently. The code IS recorded, so a change in it is
    visible in the report without being a failure.
    """
    problems: list[str] = []
    run = silent_invalid_name
    result = run.result
    rejected_name = run.options[constants.OPTION_WSL_NAME]

    # ----------------------------------------------------------------- #
    # THE INSTALLER REFUSED
    # ----------------------------------------------------------------- #
    dump_json("silent-invalid-name-result", result.as_dict())
    note(f"  INS-006-name    : {constants.OPTION_WSL_NAME} = {rejected_name!r}")
    note(f"  INS-006-run    : {result.describe()}")
    for log in result.logs:
        note(f"  INS-006-run    : log {log}")

    if result.timed_out:
        problems.append(
            f"the installer timed out after {result.duration_seconds:.0f}s "
            "instead of refusing. A name check is immediate -- it runs after "
            "CostFinalize, before anything is written -- so this is not a slow "
            "refusal, it is a refusal that never happened.")
    elif result.ok:
        problems.append(
            f"the bundle exited {result.returncode}, which is a SUCCESS, "
            f"against a WSL name the product's own CheckWslName rejects. "
            "Either the name never reached the MSI or the error action did not "
            "fire; either way an unattended caller has no way to know its "
            "install was not what it asked for.")

    # Corroboration, not a load-bearing assertion. If the override had never
    # reached the bundle the install would have SUCCEEDED under the default
    # name, and the two assertions either side of this would both fail -- so
    # this only has to explain such a failure, not catch it. Absent is not a
    # finding: a run that fails early can exit before Burn dumps its variables.
    recorded = silent.variable_from_log(result.log_path,
                                        constants.OPTION_WSL_NAME)
    note(f"  INS-006-name    : the bundle recorded {recorded!r}")
    if recorded is not None and recorded != str(rejected_name):
        problems.append(
            f"the command line passed {rejected_name!r} but the bundle held "
            f"{recorded!r}, so whatever it refused, it was not this name.")

    # ----------------------------------------------------------------- #
    # AND LEFT NOTHING BEHIND
    #
    # `reset`'s own residue reading, not a list restated here -- the same one
    # every install fixture is cleaned against, so the two cannot drift.
    # ----------------------------------------------------------------- #
    state = run.state
    # The five artifacts `find_residue` looks at, printed raw. On a PASS the
    # residue list below is empty, so this is the only evidence in the report
    # that the reading happened at all rather than being skipped.
    note(f"  INS-006-machine    : registry={state.registry.present} "
         f"distro={state.cubrid.distro_present} "
         f"starter={state.startup.starter_registered} "
         f"tray={state.startup.tray_app_registered} "
         f"arp={state.arp.present}")

    for item in run.residue:
        note(f"  INS-006-residue    : {item}")
    if run.residue:
        problems.append(
            f"the refused install left {len(run.residue)} thing(s) on the "
            "machine. A leftover install directory is not cosmetic here: "
            "InstallWslAndCubrid aborts on an existing ext4.vhdx, so residue "
            "from a refusal blocks every later install:\n"
            + "\n".join(f"      {item}" for item in run.residue))
    else:
        note("  INS-006-residue    : nothing left behind")

    assert not problems, (
        f"INS-006 found {len(problems)} problem(s) with the rejected WSL "
        "name:\n" + "\n".join(f"  - {problem}" for problem in problems))
