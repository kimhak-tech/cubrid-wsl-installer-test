"""CUBRID Operational -- OPS-003, creating a new database.

Mutates the shared distribution: deleting testdb is the case's own final step,
because a database that cannot be removed is a finding.

Verifies:
- `cubrid createdb` registers the database in databases.txt
- `cubrid server start` brings it up
- csql connects to it
- `cubrid deletedb` removes it and leaves databases.txt as it was
"""
from __future__ import annotations

import pytest

from cubridwsl import constants

# `destructive` because it needs the installed machine, though it installs
# nothing itself; `action` sorts it after the cases that only observe.
pytestmark = [pytest.mark.silent, pytest.mark.destructive, pytest.mark.action]

CASE = "OPS-003"


def test_ops_003_create_start_and_connect_to_a_new_database(
        cubrid, clean_testdb, ops_evidence, ops_result, note):
    """Create testdb, start it, connect to it, and remove it again."""
    problems: list[str] = []
    skipped: list[str] = []
    testdb = constants.OPS_TESTDB_NAME
    locale = constants.OPS_TESTDB_LOCALE

    # Read AFTER `clean_testdb`, so this is the baseline the case must restore
    # databases.txt to.
    before = cubrid.databases()
    ops_evidence(CASE, "entry", f"databases.txt lists {list(before)}")

    # `clean_testdb` has already removed a leftover. Reaching here with testdb
    # still registered means the delete did not work, and a database found
    # afterwards would not be one this run created.
    if testdb in before:
        problems.append(
            f"{testdb} is still registered after the precondition tried to "
            f"remove it ({list(before)}), so creating it cannot be observed. "
            "The run notes above carry the deletedb output. Until it can be "
            "deleted, this machine is not the Installed:default the OPS cases "
            "are specified against.")
        skipped.extend(["createdb", "cubrid server start", "csql", "deletedb"])
        ops_result(CASE, problems, skipped)
        return

    # ------------------------------------------------------------------ #
    # 1. createdb
    # ------------------------------------------------------------------ #
    created = ops_evidence(CASE, "createdb", cubrid.createdb(testdb, locale))
    after = cubrid.databases()
    note(f"  {CASE}-databases    : databases.txt now lists {list(after)}")

    # Creating a database must not disturb the ones already registered:
    # createdb REWRITES databases.txt, so one it drops is silently gone.
    lost = [name for name in before if name not in after]
    if lost:
        problems.append(f"creating {testdb} removed {lost} from databases.txt.")

    if testdb not in after:
        problems.append(
            f"`cubrid createdb {testdb} {locale}` did not register the database "
            f"(rc={created.returncode}); databases.txt lists {list(after)}. "
            f"Output:\n{created.stdout}\n{created.stderr}")
        skipped.extend([
            "cubrid server start -- the database was not registered",
            "csql -- the database was not registered",
            "deletedb -- there was nothing this case created"])
        ops_result(CASE, problems, skipped)
        return

    # ------------------------------------------------------------------ #
    # 2. Start it
    # ------------------------------------------------------------------ #
    started = ops_evidence(CASE, "server-start", cubrid.server("start", testdb))
    running = ops_evidence(CASE, "server-started",
                           cubrid.wait_for_status(lambda s: s.started(testdb)))
    serving = running.started(testdb)
    if not serving:
        problems.append(
            f"`cubrid server start {testdb}` did not bring the new database up "
            f"(rc={started.returncode}); `cubrid service status` lists "
            f"{list(running.started_databases)}.")

    # ------------------------------------------------------------------ #
    # 3. Connect with csql
    #
    # Client-server, because that is the mode a real client uses and step 2 has
    # just started the server it needs. A standalone read here would prove the
    # files exist without proving the database is SERVING.
    # ------------------------------------------------------------------ #
    if not serving:
        skipped.append("csql -- the database was not started")
    else:
        connected = ops_evidence(CASE, "csql", cubrid.scalar("'ok'", testdb))
        if not connected.ok or connected.value != "ok":
            problems.append(
                f"csql could not connect to the new database {testdb}: "
                f"{list(connected.errors) or connected.returncode}. A database "
                "that is registered and started but cannot be connected to is "
                "not usable.")

    # ------------------------------------------------------------------ #
    # 4. Remove it -- part of the case, not merely cleanup
    # ------------------------------------------------------------------ #
    cubrid.server("stop", testdb)
    deleted = ops_evidence(CASE, "deletedb", cubrid.deletedb(testdb))
    remaining = cubrid.databases()
    note(f"  {CASE}-databases    : databases.txt now {list(remaining)}")

    if testdb in remaining:
        problems.append(
            f"`cubrid deletedb {testdb}` left it registered in databases.txt "
            f"({list(remaining)}). The machine is shared with every other case "
            "in this suite, so a database that will not delete changes what "
            f"they see. Output:\n{deleted.stdout}\n{deleted.stderr}")
    elif sorted(remaining) != sorted(before):
        problems.append(
            f"after creating and deleting {testdb}, databases.txt reads "
            f"{list(remaining)} where it read {list(before)} before. The "
            "machine was not restored to the state the case found it in.")

    ops_result(CASE, problems, skipped)
