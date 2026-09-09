"""CUBRID Operational -- OPS-002, demodb SQL smoke test.

Owns every CSQL assertion. demodb's presence is a precondition, asserted by
INS-001.

Verifies:
- csql connects to demodb standalone (-S) and client-server (-C)
- the tables the shipped demodb sample carries are present in db_class
- `SELECT *` and `SELECT COUNT(*)` agree on the same table
- CREATE TABLE, INSERT and read-back return the value that was written
- DROP TABLE leaves no entry behind in db_class
"""
from __future__ import annotations

import pytest

from cubridwsl import constants

# `destructive` because it needs the installed machine, though it installs
# nothing itself; `action` sorts it after the cases that only observe.
pytestmark = [pytest.mark.silent, pytest.mark.destructive, pytest.mark.action]

CASE = "OPS-002"

_ROWS = "TO_CHAR(COUNT(*))"
# Written into the scratch row and read back verbatim -- a value this case
# generates, so the read-back cannot be satisfied by pre-existing data.
_SENTINEL = "ops-002-write-probe"


def test_ops_002_demodb_sql_smoke_test_connect_select_then_ddl_dml(
        cubrid, clean_scratch_table, ops_evidence, ops_result, note):
    """Connect to demodb, read the shipped dataset, then write and clean up."""
    problems: list[str] = []
    skipped: list[str] = []
    demodb = constants.DEMODB_NAME
    table = constants.DEMODB_KNOWN_TABLE
    scratch = constants.OPS_SCRATCH_TABLE

    entry = ops_evidence(CASE, "entry", cubrid.service_status())

    # ------------------------------------------------------------------ #
    # CONNECT -- standalone, then client-server
    #
    # The ORDER IS FORCED. `-S` opens the database files directly and needs no
    # server, but takes them EXCLUSIVELY; `-C` is the path a real client uses
    # and needs `cubrid server start demodb` first, because a default install
    # starts no database. Reversed, it fails on the lock rather than on
    # anything about the product.
    # ------------------------------------------------------------------ #
    # Standalone needs EXCLUSIVE access to the files, so a server still
    # holding them makes this step impossible rather than merely awkward.
    if entry.started(demodb):
        problems.append(
            f"{demodb} is still started after the precondition tried to stop "
            "it, so the standalone connection could not be attempted -- it "
            "needs exclusive access to the database files. The run notes above "
            "carry what `cubrid server stop` reported.")
        skipped.append("csql -S -- the database was still open by a server")
    else:
        standalone = ops_evidence(CASE, "connect-S",
                                  cubrid.scalar("'ok'", demodb, standalone=True))
        if not standalone.ok or standalone.value != "ok":
            problems.append(
                f"csql could not connect to {demodb} in standalone mode: "
                f"{list(standalone.errors) or standalone.returncode}. "
                "Standalone needs no running server, so this failing points at "
                "the database files themselves rather than at the service.")

    start = ops_evidence(CASE, "server-start", cubrid.server("start", demodb))
    ready = ops_evidence(CASE, "server-started",
                         cubrid.wait_for_status(lambda s: s.started(demodb)))
    serving = ready.started(demodb)
    if not serving:
        problems.append(
            f"`cubrid server start {demodb}` did not bring the database up "
            f"(rc={start.returncode}); status lists "
            f"{list(ready.started_databases)}.")

    # Everything below is a client-server connection. Attempting it against a
    # database that is not serving would turn one cause into six failures.
    if not serving:
        skipped.extend([
            "csql -C -- the database was not started",
            "the demodb catalog and dataset read",
            "the DDL/DML write sequence",
        ])
        ops_result(CASE, problems, skipped)
        return

    connected = ops_evidence(CASE, "connect-C", cubrid.scalar("'ok'", demodb))
    if not connected.ok or connected.value != "ok":
        problems.append(
            f"csql could not connect to {demodb} client-server: "
            f"{list(connected.errors) or connected.returncode}")

    # ------------------------------------------------------------------ #
    # READ
    # ------------------------------------------------------------------ #
    catalog = ops_evidence(CASE, "catalog", cubrid.csql(
        f"SELECT '<<' || class_name || '>>' FROM {constants.CATALOG_CLASS_VIEW} "
        f"WHERE {constants.CATALOG_USER_CLASS_PREDICATE}", demodb))
    observed = tuple(sorted(catalog.values))
    note(f"  {CASE}-catalog    : demodb holds {len(observed)} user table(s): "
         f"{list(observed)}")

    if not catalog.ok:
        problems.append(
            f"the demodb catalog could not be read: "
            f"{list(catalog.errors) or catalog.returncode}")
    else:
        # A SUBSET check, not equality: an added table is not a defect, a
        # missing one is. The observed list is printed above on every run, so a
        # wrong constant is one edit away from correct.
        missing = [t for t in constants.DEMODB_TABLES if t not in observed]
        if missing:
            problems.append(
                f"the shipped demodb dataset is incomplete: {missing} are not "
                f"in {constants.CATALOG_CLASS_VIEW}. demodb reported "
                f"{list(observed)}. If the product's sample schema has changed, "
                "correct constants.DEMODB_TABLES -- it is recorded from the "
                "product, not derived from it.")

    # Checked two ways against each other rather than against a hard-coded row
    # count, which would fail correct builds the first time the sample changed.
    counted = ops_evidence(CASE, "count",
                           cubrid.scalar(_ROWS, demodb, source=table))
    listed = ops_evidence(CASE, "select",
                          cubrid.csql(f"SELECT * FROM {table}", demodb))

    if not counted.ok or counted.value is None:
        problems.append(
            f"`SELECT COUNT(*) FROM {table}` failed: "
            f"{list(counted.errors) or counted.returncode}")
        skipped.append(f"the SELECT * / COUNT(*) agreement on {table}")
    elif counted.value == "0":
        problems.append(
            f"{table} is empty. The workbook expects the shipped demodb "
            "dataset to return rows; an empty table means the sample data was "
            "not loaded even though the schema was created.")
    elif not listed.ok:
        problems.append(
            f"`SELECT * FROM {table}` failed while the COUNT succeeded: "
            f"{list(listed.errors) or listed.returncode}")
    elif listed.rows_selected != int(counted.value):
        problems.append(
            f"`SELECT * FROM {table}` returned {listed.rows_selected} row(s) "
            f"but `SELECT COUNT(*)` reports {counted.value}. The two must "
            "agree; they do not, so the read path returns a different result "
            "depending on how it is asked.")
    else:
        note(f"  {CASE}-read    : {table} returned {counted.value} row(s), "
             "COUNT and SELECT * agreeing")

    # ------------------------------------------------------------------ #
    # WRITE
    #
    # One csql invocation per statement, so a failure names the statement that
    # failed rather than the whole sequence.
    # ------------------------------------------------------------------ #
    created = ops_evidence(CASE, "create", cubrid.csql(
        f"CREATE TABLE {scratch} (id INTEGER PRIMARY KEY, note VARCHAR(64))",
        demodb))
    if not created.ok:
        problems.append(
            f"CREATE TABLE {scratch} failed: "
            f"{list(created.errors) or created.returncode}")
        skipped.extend(["the INSERT and read-back",
                        "DROP TABLE and the catalog residue check"])
        ops_result(CASE, problems, skipped)
        return

    inserted = ops_evidence(CASE, "insert", cubrid.csql(
        f"INSERT INTO {scratch} VALUES (1, '{_SENTINEL}')", demodb))
    if not inserted.ok:
        problems.append(
            f"INSERT into {scratch} failed: "
            f"{list(inserted.errors) or inserted.returncode}")
        skipped.append("the read-back of the inserted row")
    else:
        read_back = ops_evidence(CASE, "select-back", cubrid.scalar(
            "note", demodb, source=f"{scratch} WHERE id = 1"))
        if not read_back.ok:
            problems.append(
                f"reading the inserted row back from {scratch} failed: "
                f"{list(read_back.errors) or read_back.returncode}")
        elif read_back.value != _SENTINEL:
            problems.append(
                f"the row inserted into {scratch} came back as "
                f"{read_back.value!r}, not {_SENTINEL!r}. The write and the "
                "read disagree about what was stored.")

    dropped = ops_evidence(CASE, "drop",
                           cubrid.csql(f"DROP TABLE {scratch}", demodb))
    if not dropped.ok:
        problems.append(
            f"DROP TABLE {scratch} failed: "
            f"{list(dropped.errors) or dropped.returncode}")

    # The catalog is re-read rather than the DROP's exit code trusted.
    residue = ops_evidence(CASE, "residue", cubrid.scalar(
        _ROWS, demodb,
        source=f"{constants.CATALOG_CLASS_VIEW} WHERE class_name = '{scratch}'"))
    if not residue.ok:
        problems.append(
            f"the catalog could not be re-read after dropping {scratch}: "
            f"{list(residue.errors) or residue.returncode}")
    elif residue.value != "0":
        problems.append(
            f"{constants.CATALOG_CLASS_VIEW} still holds {residue.value} entry "
            f"for {scratch} after DROP TABLE. The table was not removed "
            "cleanly, and the residue is left on the shared machine.")

    ops_result(CASE, problems, skipped)
