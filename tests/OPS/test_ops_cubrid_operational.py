"""CUBRID Operational -- OPS-001 to OPS-004, one installation for all four.

Every case acts against the same `suite_installation`, in workbook order, and
each BEGINS by putting that installation into the state it needs rather than
trusting the case before it to have cleaned up.

Verifies:
- OPS-001: after `cubrid service stop`, master, broker and manager no longer
  report running; after `cubrid service start` all three run again, with both
  brokers a default install ships
- OPS-002: csql queries demodb standalone (-S) and client-server (-C); CREATE
  TABLE, INSERT and read-back return the value written; DROP TABLE leaves no
  entry in db_class
- OPS-003: `cubrid createdb` registers a new database; `cubrid server start`
  brings it up; csql queries it; `cubrid deletedb` removes it and leaves
  databases.txt as it was
- OPS-004: an engine package downloads inside the distribution, installs over
  the default, and `cubrid_rel` reports the version its filename carries

DESTRUCTIVE and elevated.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.wsl import cubrid

# `destructive` because `suite_installation` installs and uninstalls the
# product around these cases; `action` plus the case number keeps them together
# and in workbook order. OPS-004 replaces the engine and no downgrade exists,
# which is why it runs last and why the installation is removed after it.
pytestmark = [pytest.mark.silent, pytest.mark.destructive, pytest.mark.action]


# =============================================================================
# OPS-001: Stop and start the CUBRID service
# =============================================================================
# Stop CUBRID, confirm every component stopped, start it again, and confirm
# every component is back.

def test_ops_001_service_stop_then_start(suite_installation, settings, note):
    wsl_name = suite_installation.wsl_name

    # ----------------------------------------------------------------- #
    # 1. Set up -- the service must be RUNNING, or the stop proves nothing
    # ----------------------------------------------------------------- #
    if not cubrid.is_service_started(wsl_name, settings):
        cubrid.service_start(wsl_name, settings)
    note(f"  OPS-001-before     : "
         f"{cubrid.service_status(wsl_name, settings).describe()}")

    # ----------------------------------------------------------------- #
    # 2. Action -- stop the service
    # ----------------------------------------------------------------- #
    result = cubrid.service_stop(wsl_name, settings)
    note(f"  OPS-001-stop       : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- every component has stopped
    # ----------------------------------------------------------------- #
    # Read from `cubrid service status`, never from the stop's exit code.
    assert cubrid.is_service_stopped(wsl_name, settings), (
        "the CUBRID master still reports running after "
        "`cubrid service stop`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}")

    assert cubrid.is_broker_stopped(wsl_name, settings), (
        "the broker still reports running after `cubrid service stop`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}")

    assert cubrid.is_manager_stopped(wsl_name, settings), (
        "the manager still reports running after `cubrid service stop`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}")

    # ----------------------------------------------------------------- #
    # 4. Action -- start the service
    # ----------------------------------------------------------------- #
    result = cubrid.service_start(wsl_name, settings)
    note(f"  OPS-001-start      : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 5. Verification -- every component is running again
    # ----------------------------------------------------------------- #
    # Each failure message carries the CUBRID processes as well as the status:
    # a process PRESENT while status disagrees is a status problem, one ABSENT
    # started and died.
    assert cubrid.is_service_started(wsl_name, settings), (
        "the CUBRID master is not running after `cubrid service start`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}\n"
        f"processes:\n{cubrid.processes(wsl_name, settings)}")

    assert cubrid.is_broker_started(wsl_name, settings), (
        "the broker is not running with its default brokers after "
        "`cubrid service start`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}\n"
        f"processes:\n{cubrid.processes(wsl_name, settings)}")

    assert cubrid.is_manager_started(wsl_name, settings), (
        "the manager is not running after `cubrid service start`:\n"
        f"{cubrid.service_status(wsl_name, settings).raw}\n"
        f"processes:\n{cubrid.processes(wsl_name, settings)}")


# =============================================================================
# OPS-002: Query demodb with csql
# =============================================================================
# Connect to demodb both ways, then create, fill, read back and drop a table of
# its own.

def test_ops_002_demodb_sql_smoke_test_connect_select_then_ddl_dml(
        suite_installation, settings, note):
    wsl_name = suite_installation.wsl_name
    demodb = constants.DEMODB_NAME
    test_tb = "test_tb"
    # Written and read back verbatim -- generated here, so pre-existing data
    # cannot satisfy the read-back.
    sentinel = "ops-002-write-probe"

    # ----------------------------------------------------------------- #
    # 1. Set up -- service running, demodb's server DOWN
    # ----------------------------------------------------------------- #
    # The server must be down: standalone csql needs EXCLUSIVE access to the
    # database files.
    if not cubrid.is_service_started(wsl_name, settings):
        cubrid.service_start(wsl_name, settings)
    if cubrid.is_server_started(wsl_name, demodb, settings):
        cubrid.server_stop(wsl_name, demodb, settings)

    # ----------------------------------------------------------------- #
    # 2. Connect standalone (-S)
    # ----------------------------------------------------------------- #
    # FIRST, because it cannot be done once step 3 has started the server.
    result = cubrid.csql(wsl_name, "SELECT 1", demodb, settings,
                         standalone=True)
    note(f"  OPS-002-standalone : {result.describe()}")

    assert result.ok and result.rows_selected == 1, (
        f"csql could not query {demodb} in standalone mode. Standalone needs "
        "no running server, so this points at the database files themselves "
        f"rather than at the service: {result.describe()}\n{result.stdout}")

    # ----------------------------------------------------------------- #
    # 3. Start demodb's server
    # ----------------------------------------------------------------- #
    # A default install starts no database, so client-server needs this.
    result = cubrid.server_start(wsl_name, demodb, settings)
    note(f"  OPS-002-server     : {result.describe()}")

    assert cubrid.is_server_started(wsl_name, demodb, settings), (
        f"`cubrid server start {demodb}` did not bring the database up:\n"
        f"{result.stdout}\n{result.stderr}")

    # ----------------------------------------------------------------- #
    # 4. Connect client-server (-C)
    # ----------------------------------------------------------------- #
    result = cubrid.csql(wsl_name, "SELECT 1", demodb, settings)
    note(f"  OPS-002-client     : {result.describe()}")

    assert result.ok and result.rows_selected == 1, (
        f"csql could not query {demodb} client-server while its server is "
        f"started: {result.describe()}\n{result.stdout}")

    # ----------------------------------------------------------------- #
    # 5. Write -- CREATE TABLE, INSERT, read it back
    # ----------------------------------------------------------------- #
    # Dropped first in case an earlier run failed between its CREATE and its
    # DROP.
    dropped = cubrid.csql(wsl_name, f"DROP TABLE IF EXISTS {test_tb}",
                          demodb, settings)
    assert dropped.ok, (
        f"DROP TABLE IF EXISTS {test_tb} failed: {dropped.describe()}")

    created = cubrid.csql(
        wsl_name,
        f"CREATE TABLE {test_tb} (id INTEGER PRIMARY KEY, note VARCHAR(64))",
        demodb, settings)
    assert created.ok, f"CREATE TABLE {test_tb} failed: {created.describe()}"

    inserted = cubrid.csql(
        wsl_name, f"INSERT INTO {test_tb} VALUES (1, '{sentinel}')",
        demodb, settings)
    assert inserted.ok, f"INSERT into {test_tb} failed: {inserted.describe()}"

    result = cubrid.csql(
        wsl_name,
        f"SELECT {cubrid.marked('note')} FROM {test_tb} WHERE id = 1",
        demodb, settings)
    note(f"  OPS-002-read-back  : {result.describe()}")

    assert result.ok and result.value == sentinel, (
        f"the row inserted into {test_tb} came back as {result.value!r}, "
        f"not {sentinel!r}: {result.describe()}")

    # ----------------------------------------------------------------- #
    # 6. Drop the table
    # ----------------------------------------------------------------- #
    dropped = cubrid.csql(wsl_name, f"DROP TABLE {test_tb}", demodb, settings)
    assert dropped.ok, f"DROP TABLE {test_tb} failed: {dropped.describe()}"

    # The catalog is re-read rather than the DROP's exit code trusted.
    result = cubrid.csql(
        wsl_name,
        f"SELECT {cubrid.marked('TO_CHAR(COUNT(*))')} FROM db_class "
        f"WHERE class_name = '{test_tb}'",
        demodb, settings)
    note(f"  OPS-002-catalog    : {result.describe()}")

    assert result.ok and result.value == "0", (
        f"db_class still holds {test_tb} after DROP TABLE: "
        f"{result.describe()}\n{result.stdout}")

    # ----------------------------------------------------------------- #
    # 7. Clean up -- back to the default: demodb's server down
    # ----------------------------------------------------------------- #
    cubrid.server_stop(wsl_name, demodb, settings)


# =============================================================================
# OPS-003: Create, start, connect to and delete a new database
# =============================================================================
# Create testdb, start it, connect to it, delete it, and confirm databases.txt
# is back to what it was.

def test_ops_003_create_start_and_connect_to_a_new_database(
        suite_installation, settings, note):
    wsl_name = suite_installation.wsl_name
    testdb = "testdb"

    # ----------------------------------------------------------------- #
    # 1. Set up -- service running, and no testdb left by an earlier run
    # ----------------------------------------------------------------- #
    if not cubrid.is_service_started(wsl_name, settings):
        cubrid.service_start(wsl_name, settings)
    if cubrid.is_database_exists(wsl_name, testdb, settings):
        cubrid.server_stop(wsl_name, testdb, settings)
        cubrid.delete_database(wsl_name, testdb, settings)

    assert not cubrid.is_database_exists(wsl_name, testdb, settings), (
        f"{testdb} is left over from an earlier run and could not be deleted, "
        "so creating it cannot be observed")

    # Read now -- the baseline databases.txt must return to.
    before = cubrid.databases(wsl_name, settings)
    note(f"  OPS-003-before     : databases.txt lists {list(before)}")

    # ----------------------------------------------------------------- #
    # 2. Action -- create the database
    # ----------------------------------------------------------------- #
    result = cubrid.create_database(wsl_name, testdb, "en_US", settings)
    note(f"  OPS-003-createdb   : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- it is registered
    # ----------------------------------------------------------------- #
    after = cubrid.databases(wsl_name, settings)
    assert testdb in after, (
        f"`cubrid createdb` did not register {testdb}; databases.txt lists "
        f"{list(after)}. Output:\n{result.stdout}\n{result.stderr}")

    # ----------------------------------------------------------------- #
    # 4. Action -- start the database
    # ----------------------------------------------------------------- #
    result = cubrid.server_start(wsl_name, testdb, settings)
    note(f"  OPS-003-server     : {result.describe()}")

    assert cubrid.is_server_started(wsl_name, testdb, settings), (
        f"`cubrid server start {testdb}` did not bring the new database up:\n"
        f"{result.stdout}\n{result.stderr}")

    # ----------------------------------------------------------------- #
    # 5. Verification -- csql connects to it
    # ----------------------------------------------------------------- #
    # Client-server, the mode a real client uses: a standalone read would
    # prove the files exist without proving the database is SERVING.
    result = cubrid.csql(wsl_name, "SELECT 1", testdb, settings)
    note(f"  OPS-003-csql       : {result.describe()}")

    assert result.ok and result.rows_selected == 1, (
        f"csql could not query the new database {testdb} while it is "
        f"registered and started: {result.describe()}\n{result.stdout}")

    # ----------------------------------------------------------------- #
    # 6. Action -- delete the database
    # ----------------------------------------------------------------- #
    cubrid.server_stop(wsl_name, testdb, settings)
    result = cubrid.delete_database(wsl_name, testdb, settings)
    note(f"  OPS-003-deletedb   : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 7. Verification -- gone, and databases.txt is as it was
    # ----------------------------------------------------------------- #
    remaining = cubrid.databases(wsl_name, settings)
    assert testdb not in remaining, (
        f"`cubrid deletedb {testdb}` left it registered in databases.txt "
        f"({list(remaining)}). Output:\n{result.stdout}\n{result.stderr}")

    assert sorted(remaining) == sorted(before), (
        f"databases.txt reads {list(remaining)} after creating and deleting "
        f"{testdb}, where it read {list(before)} before")


# =============================================================================
# OPS-004: Install a new CUBRID engine over the default
# =============================================================================
# Download an engine package, install it over the shipped engine, and confirm
# CUBRID reports the version that was installed.

def test_ops_004_install_a_new_cubrid_version_over_the_default(
        suite_installation, settings, note, dump_json):
    wsl_name = suite_installation.wsl_name
    url = str(settings["upgrade"]["url"]).strip()
    installer_args = str(settings["upgrade"].get("installer_args") or "")
    budget = int(settings["timeouts"]["upgrade_seconds"])

    # ----------------------------------------------------------------- #
    # 1. Set up -- download the engine package
    # ----------------------------------------------------------------- #
    result, filename = cubrid.download(wsl_name, url, settings, timeout=budget)
    note(f"  OPS-004-download   : {result.describe()}")

    # wget runs INSIDE the distribution, so this is the Windows host's network,
    # not the product failing.
    assert result.ok, (
        f"could not download the engine package from {url}: "
        f"{result.stderr[:400] or result.stdout[:400]}. Point `upgrade.url` "
        "in config/settings.local.toml at a build this machine can reach.")

    # The version to expect, read out of the package's own name -- so the URL
    # is the only thing to configure and the two can never disagree.
    match = constants.ENGINE_PACKAGE_RE.search(filename)
    expected = match.group("version") if match else None
    note(f"  OPS-004-package    : {filename} -> version {expected!r}")

    assert expected, (
        f"{filename!r} carries no version to check `cubrid_rel` against. "
        "Point `upgrade.url` at a named build rather than a `-latest-` one.")

    # ----------------------------------------------------------------- #
    # 2. Action -- install the engine over the default
    # ----------------------------------------------------------------- #
    result = cubrid.install_engine(wsl_name, filename, settings,
                                   args=installer_args, timeout=budget)
    note(f"  OPS-004-install    : {result.describe()}")
    # The whole transcript -- an installer answered blind is diagnosed from
    # what it actually asked.
    dump_json("ops_004_installer", result.as_dict())

    assert result.ok, (
        f"the engine installer failed (rc={result.returncode}), run with "
        f"`{installer_args}`. Full transcript in ops_004_installer.json; the "
        f"last lines:\n{result.stdout[-1500:]}")

    # ----------------------------------------------------------------- #
    # 3. Verification -- CUBRID reports the installed version
    # ----------------------------------------------------------------- #
    version = cubrid.version(wsl_name, settings)
    note(f"  OPS-004-version    : cubrid_rel -> {version!r}")

    # No output at all is the signature of a broken ~/.cubrid.sh: $CUBRID/bin
    # is not on PATH, while the installer still reported success.
    assert version, "`cubrid_rel` produced no output after the install"

    assert expected in version, (
        f"the package installed was {filename!r}, so `cubrid_rel` should "
        f"report {expected!r} -- it reports {version!r}. An installer that "
        "extracted into a VERSIONED subdirectory leaves PATH on the old "
        "engine, which reads exactly like this. CUBRID trees in the guest "
        "home:\n"
        f"{cubrid.engine_locations(wsl_name, settings)}")


# No clean-up here: `suite_installation` owns this machine and removes it
# after the last case in this module.
