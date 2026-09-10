"""Category 03 -- CUBRID Operational Validation. Shared machine and cleanup.

Every OPS case runs against the shared Installed:default machine and performs no
install of its own. They take `silent_install` rather than `wizard_install`
because they are all Automation = Silent, which means no UI dependency: the
wizard fixture would give `run-tests.ps1 silent` a pywinauto dependency and a
run that seizes the mouse.

**Each fixture ESTABLISHES the precondition it names, then restores it.**
Cleaning on the way IN matters as much as on the way out, and is the rule the
install fixtures already follow (`_provision` calls `reset.ensure_clean` before
every install). Without it one interrupted run poisons every later run, and the
failure describes the earlier run rather than the current one:

* a `testdb` left registered makes OPS-003 fail until someone deletes it by hand
* a leftover scratch table makes OPS-002's CREATE TABLE fail on a taken name
* a demodb server left up makes OPS-002's standalone connect impossible, because
  standalone needs exclusive access to the database files

Each one **repairs, reports, and does not assert**. It reports because something
leaked that state and a silent cleanup hides it; it does not assert because the
run that leaks is caught by its own final step, and failing here as well would
report an earlier run's mess as this run's failure.
"""
from __future__ import annotations

from typing import Any

import pytest

from cubridwsl import constants, cubrid_cli, distro

# ADDING A CASE: declare its markers in the test MODULE, not here. `pytestmark`
# in a conftest is SILENTLY INERT -- pytest honours it at module and class level
# only -- so markers placed here would leave `-m silent` missing the case and
# the `action` sort doing nothing, with no error to say so.


@pytest.fixture(scope="session")
def cubrid(silent_install, settings) -> cubrid_cli.CubridCli:
    """CUBRID's command line inside the machine the silent install established.

    Session-scoped because it holds no state -- it is a binding to a
    distribution name, and building one per test would re-read nothing.
    """
    try:
        return cubrid_cli.for_installation(
            settings, silent_install.state.registry.wsl_name)
    except distro.DistroError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture
def running_service(cubrid, note):
    """CUBRID running before the case, and running after it.

    OPS-001 stops the service deliberately. If it fails before starting it
    again, every later case -- and the next run -- would be diagnosing a
    stopped service instead of its own subject.
    """
    _ensure_service_running(cubrid, note, "precondition")
    yield
    _ensure_service_running(cubrid, note, "restore")


def _ensure_service_running(cubrid, note, phase: str) -> None:
    awaited = constants.SERVICE_COMPONENTS_AWAITED
    status = cubrid.service_status()
    if status.all_running(awaited):
        return
    note(f"  {phase}    : CUBRID is {status.components}; starting the service "
         "so the case runs on the machine the workbook describes")
    cubrid.service("start")
    after = cubrid.wait_for_status(lambda s: s.all_running(awaited))
    note(f"  {phase}    : service now {after.components}")


@pytest.fixture
def demodb_server_down(cubrid, note):
    """demodb's server down before the case, and down after it.

    A default install starts NO database, so a running demodb here was left by
    something -- and it is not a state OPS-002 can work around: its first step
    opens demodb in STANDALONE mode, which needs EXCLUSIVE file access.
    """
    _stop_demodb(cubrid, note, "precondition")
    yield
    _stop_demodb(cubrid, note, "restore")


def _stop_demodb(cubrid, note, phase: str) -> None:
    name = constants.DEMODB_NAME
    if not cubrid.service_status().started(name):
        return
    cubrid.server("stop", name)
    still = cubrid.service_status().started(name)
    note(f"  {phase}    : stopped the {name} server"
         + (" -- it was up before this case ran, so a previous case or run "
            "left it started" if phase == "precondition" else "")
         + ("; it is STILL started, and OPS-002 cannot open the database "
            "standalone while it is" if still else ""))


@pytest.fixture
def clean_testdb(cubrid, note):
    """OPS-003's database: absent BEFORE the case, and absent after it.

    A leftover is removed and REPORTED, never silently: something leaked it.
    Cleaning on the way out is the safety net for the run that fails before
    OPS-003's own final delete.
    """
    _remove_testdb(cubrid, note, "precondition")
    yield
    _remove_testdb(cubrid, note, "restore")


def _remove_testdb(cubrid, note, phase: str) -> None:
    """Stop and delete testdb if it is registered. Reports; never asserts."""
    name = constants.OPS_TESTDB_NAME
    if name not in cubrid.databases():
        return
    note(f"  {phase}    : {name} is registered "
         + ("before the case ran, so a previous run or a manual experiment "
            "left it behind; removing it so OPS-003 starts from the state its "
            "preconditions describe" if phase == "precondition"
            else "after the case; removing it"))
    cubrid.server("stop", name)
    result = cubrid.deletedb(name)
    remaining = cubrid.databases()
    note(f"  {phase}    : deletedb rc={result.returncode}; databases now "
         f"{list(remaining)}")
    if name in remaining:
        note(f"  {phase}    : {name} SURVIVED the delete -- OPS-003 cannot "
             "observe a creation while it is there, and every later case sees "
             f"it too. Output:\n{result.stdout}\n{result.stderr}")


@pytest.fixture
def clean_scratch_table(cubrid, demodb_server_down, note):
    """OPS-002's scratch table absent before the case, and absent after it.

    OPS-002 drops it itself and asserts the catalog is clean; this covers the
    run that fails between the CREATE and the DROP, which would otherwise leave
    the name taken for the next run.

    Requests `demodb_server_down` so that fixture is set up FIRST and torn down
    LAST -- dropping a table needs the database still reachable.

    The csql MODE is read from the machine rather than fixed: on the way in
    demodb's server is down so only standalone can open it, and on the way out
    the case has started the server, where standalone would fail on the lock.
    """
    _drop_scratch_table(cubrid, note, "precondition")
    yield
    _drop_scratch_table(cubrid, note, "restore")


def _drop_scratch_table(cubrid, note, phase: str) -> None:
    scratch = constants.OPS_SCRATCH_TABLE
    # Standalone only while nothing else holds the database; see the docstring.
    standalone = not cubrid.service_status().started(constants.DEMODB_NAME)
    probe = cubrid.scalar(
        "TO_CHAR(COUNT(*))", constants.DEMODB_NAME, standalone=standalone,
        source=f"{constants.CATALOG_CLASS_VIEW} "
               f"WHERE class_name = '{scratch}'")
    if not probe.ok:
        note(f"  {phase}    : could not check for a leftover {scratch} "
             f"({list(probe.errors) or probe.returncode}); nothing dropped")
        return
    if probe.value == "0":
        return
    dropped = cubrid.csql(f"DROP TABLE {scratch}", constants.DEMODB_NAME,
                          standalone=standalone)
    note(f"  {phase}    : dropped a leftover {scratch} table "
         f"(standalone={standalone}, ok={dropped.ok})"
         + (" -- it was there before this case ran, so an earlier run failed "
            "between its CREATE and its DROP" if phase == "precondition"
            else ""))


@pytest.fixture
def ops_result(note):
    """One case, one result -- naming the steps that were not evaluated.

    A failure early in an ordered sequence makes the steps after it moot, and
    running them anyway reports one cause as several failures -- the same idea
    as `requires` / `[skip]` in verify.compare.

    Skipping is not passing: each unevaluated step is printed on every run and
    named in the assertion message.
    """
    def _report(case_id: str, problems: list[str],
                skipped: list[str] | tuple[str, ...] = ()) -> None:
        for step in skipped:
            note(f"  {case_id}-NOT EVALUATED    : {step}")
        assert not problems, (
            f"{case_id} found {len(problems)} problem(s)"
            + (f", and {len(skipped)} step(s) were not evaluated as a result"
               if skipped else "") + ":\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + ("\n  not evaluated:\n"
               + "\n".join(f"    . {step}" for step in skipped)
               if skipped else ""))
    return _report


@pytest.fixture
def ops_evidence(note, dump_json) -> Any:
    """Record what a step did, in the notes and in the run report.

    A failure at step four is diagnosed from what steps one to three returned.
    pytest hides output on a pass and the report directory outlives the run, so
    each step goes to both.
    """
    steps: list[dict[str, Any]] = []
    case: dict[str, str] = {}

    def _record(case_id: str, step: str, subject: Any) -> Any:
        case.setdefault("id", case_id)
        described = (subject.describe() if hasattr(subject, "describe")
                     else str(subject))
        note(f"  {case_id}-{step}    : {described}")
        steps.append({"step": step,
                      **(subject.as_dict() if hasattr(subject, "as_dict")
                         else {"detail": described})})
        return subject

    yield _record
    if steps:
        dump_json(case["id"].lower().replace("-", "_"), steps)
