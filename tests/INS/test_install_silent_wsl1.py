"""Installer Orchestration -- INS-004, the distribution imported at WSL 1.

Verifies:
- the bundle RECORDED the override, read back out of its own log
- the distribution registers at VERSION 1
- INS-001's post-install set holds unchanged on a WSL 1 machine

DESTRUCTIVE, elevated. One install cycle, and it is the last one of a run.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants, distro
from cubridwsl.drivers import silent

pytestmark = [pytest.mark.silent, pytest.mark.destructive]


def test_ins_004_install_in_wsl1_mode_via_the_silent_cli(
        silent_wsl1_install, check_against_bundle, note, dump_json):
    """WSL 1 must be shown to be the product's doing, not the host's.

    What this case adds on top is the read-back below. The EFFECT it asserts, a
    WSL 1 distribution, is also what a host whose default version is already 1
    produces on its own, so the effect alone cannot tell "the product honoured
    the option" from "the option was ignored on a machine that agreed with it".

    This is also the first case to exercise a silent property override on a
    happy path at all. Unattended installation is the core of TOOLS-4939, and
    every other case either passes no overrides or uses them to trigger a
    failure.

    If the shipping build stops offering WSL 1 at all, this case fails while
    PROVISIONING and never reaches its assertions. That is the product answering
    an open question, and the bundle log carries it -- not a fault in the
    framework to be worked around here.
    """
    problems: list[str] = []
    installation = silent_wsl1_install
    state = installation.state

    # ----------------------------------------------------------------- #
    # THE OVERRIDE REACHED THE BUNDLE
    #
    # From Burn's own log, never from the command line this run passed. The
    # command line records what was asked for; `Variable: <name> = <value>` is
    # the bundle recording what it held. A property renamed or no longer
    # forwarded fails HERE, naming itself, instead of surfacing as a distro
    # version that happens to be right for the wrong reason.
    # ----------------------------------------------------------------- #
    requested = installation.options[constants.OPTION_WSL2_MODE]
    recorded = silent.variable_from_log(installation.result.log_path,
                                        constants.OPTION_WSL2_MODE)
    note(f"  INS-004-override    : {constants.OPTION_WSL2_MODE} "
         f"requested={requested!r} recorded={recorded!r}")
    if recorded != str(requested):
        problems.append(
            f"the command line passed {constants.OPTION_WSL2_MODE}="
            f"{requested!r} but the bundle recorded {recorded!r}"
            + (" -- the variable is absent from the log entirely, which can "
               "also mean the bundle failed before it started"
               if recorded is None else "")
            + ". Every assertion below describes a machine the installer was "
              "not actually asked to build.")

    # ----------------------------------------------------------------- #
    # INS-001'S POST-INSTALL SET, THROUGH THE SAME COMPARISON
    #
    # Not re-listed here. `verify.CHECKS` is the inventory, and a copy of a list
    # is a copy that goes stale. `distro.version` carries the one inversion.
    # ----------------------------------------------------------------- #
    comparison = installation.comparison
    dump_json("comparison-silent-wsl1", comparison.as_list())
    for check in comparison.results:
        note(f"  INS-004-comparison    : {check}")
    problems.extend(comparison.problems().splitlines())

    # ----------------------------------------------------------------- #
    # EVIDENCE -- no assertions. Printed so that diagnosing a failure does not
    # mean reinstalling to see what the machine said.
    # ----------------------------------------------------------------- #
    entry = distro.find(str(state.registry.wsl_name or ""))
    note(f"  INS-004-distro    : wsl -l -v  -> "
         f"{f'{entry.name} {entry.state} VERSION={entry.version}' if entry else '<absent>'}")
    # Brokers by NAME, which no check reports: verify.CHECKS asks whether each
    # EXPECTED broker is running, so a broker running that should NOT be is
    # visible here and nowhere else. The service components and demodb are
    # deliberately not printed -- each is a named check already, and repeating
    # one leaves a reader diffing two copies of the same fact.
    note(f"  INS-004-service    : brokers running -> {list(state.cubrid.brokers)}")

    # ----------------------------------------------------------------- #
    # The assertions against the BUNDLE UNDER TEST -- version and the Add/Remove
    # entry. The workbook requires the full INS-001 set here, and these are the
    # two parts of it that compare the machine against a third thing.
    # ----------------------------------------------------------------- #
    problems.extend(check_against_bundle(installation, "INS-004"))

    assert not problems, (
        f"INS-004 found {len(problems)} problem(s) with the WSL 1 install:\n"
        + "\n".join(f"  - {problem}" for problem in problems))
