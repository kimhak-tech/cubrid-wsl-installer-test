"""CUBRID Operational -- OPS-001, service stop/start cycle.

Asserts the TRANSITION. INS-001 owns the post-install "already running" state.

Verifies:
- `cubrid service stop` confirms master, broker and manager stopped
- `cubrid service status` then reports none of them running
- `cubrid service start` confirms all three started again
- both brokers a default install ships are running after the restart
"""
from __future__ import annotations

import pytest

from cubridwsl import constants

# `destructive` because it needs the installed machine, though it installs
# nothing itself; `action` sorts it after the cases that only observe.
pytestmark = [pytest.mark.silent, pytest.mark.destructive, pytest.mark.action]

CASE = "OPS-001"


def test_ops_001_service_stop_then_start(
        cubrid, running_service, ops_evidence, ops_result, note):
    """Stop CUBRID, observe it stopped, start it, observe it running."""
    problems: list[str] = []
    awaited = constants.SERVICE_COMPONENTS_AWAITED

    # A cycle that began from a stopped service would assert the stop against
    # something already down, so the precondition is verified rather than
    # assumed.
    entry = ops_evidence(CASE, "entry", cubrid.service_status())
    if not entry.all_running(awaited):
        problems.append(
            f"the service is not running even after the precondition tried to "
            f"start it ({entry.components}), so the stopped -> running "
            "TRANSITION this case exists to assert cannot be observed. The "
            "run notes above carry what `cubrid service start` reported.")

    # ------------------------------------------------------------------ #
    # 1. STOP
    # ------------------------------------------------------------------ #
    stop_result, stop_verdict = cubrid.service("stop")
    ops_evidence(CASE, "stop", stop_result)
    note(f"  {CASE}-stop    : per-component verdict {stop_verdict}")

    # Read from the command's own output -- the product stating what it did --
    # rather than from an exit code.
    #
    # The SERVER is excluded on purpose. A default install starts no database
    # (stock cubrid.conf leaves `server=` commented out), so there is nothing to
    # stop and that section is legitimately empty; asserting it would fail a
    # correct machine. constants.SERVICE_COMPONENTS_* carries the reasoning.
    for component in awaited:
        if stop_verdict.get(component) is not True:
            problems.append(
                f"`cubrid service stop` did not confirm the {component} stop "
                f"(reported {stop_verdict.get(component)!r}). The workbook "
                "requires the stop to be confirmed, not inferred from an exit "
                f"code. Full output:\n{stop_result.stdout}")

    stopped = ops_evidence(CASE, "stopped", cubrid.wait_for_status(
        lambda s: not s.any_running(awaited)))

    # "No longer reports running" is not "every component reports stopped": a
    # component absent from the output reads None, and a stopped master need not
    # print a section per service. Only one still claiming to RUN is a finding.
    note(f"  {CASE}-stopped    : per-component reading {stopped.components} "
         "(None = the component printed no section, which a stopped CUBRID may "
         "legitimately do)")
    running_still = [c for c in awaited if stopped.running(c) is True]
    if running_still:
        problems.append(
            f"after `cubrid service stop`, {running_still} still report "
            f"running. Full status:\n{stopped.raw}")

    # ------------------------------------------------------------------ #
    # 2. START -- the stopped -> running transition, which is this case
    # ------------------------------------------------------------------ #
    start_result, start_verdict = cubrid.service("start")
    ops_evidence(CASE, "start", start_result)
    note(f"  {CASE}-start    : per-component verdict {start_verdict}")

    for component in awaited:
        if start_verdict.get(component) is not True:
            problems.append(
                f"`cubrid service start` did not confirm the {component} start "
                f"(reported {start_verdict.get(component)!r}). Full output:\n"
                f"{start_result.stdout}")

    running = ops_evidence(CASE, "running", cubrid.wait_for_status(
        lambda s: s.all_running(awaited)))
    down = [c for c in awaited if running.running(c) is not True]
    if down:
        # Two different causes needing two different reports: the daemon died,
        # or status cannot see one that is there. Read only on failure.
        surviving = ops_evidence(CASE, "processes", cubrid.processes("cub_"))
        for component in awaited:
            verdict = running.running(component)
            if verdict is not True:
                problems.append(
                    f"after `cubrid service start`, {component} reads "
                    f"{verdict!r} rather than running. The workbook requires "
                    "the components to be asserted individually, not inferred "
                    "from a zero exit code: the product's own Tray and Starter "
                    "both decide the whole service is up from the master line "
                    f"alone.\nstatus:\n{running.raw}\nCUBRID processes in the "
                    f"distribution:\n{surviving.stdout}\n"
                    "A process PRESENT here while status disagrees is a status "
                    "or socket problem; one that is ABSENT started and died.")

    # A broker service that is up with no brokers running serves nothing, so
    # the two a default install ships are checked by name.
    for broker in constants.SERVICE_EXPECTED_BROKERS:
        if broker not in running.brokers:
            problems.append(
                f"the broker {broker!r} is not running after the restart; "
                f"`cubrid service status` lists {list(running.brokers)}.")

    ops_result(CASE, problems)
