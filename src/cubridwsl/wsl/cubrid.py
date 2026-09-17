"""CUBRID itself, seen from inside the distribution the installer created.

Every function takes the distribution NAME -- read from the product's own
registry key, never from settings, so it cannot be pointed at a different
distribution than the one under test -- and does ONE thing a case step names:

    cubrid.service_stop(wsl_name, settings)             an ACTION
    assert cubrid.is_service_stopped(wsl_name, settings)  a QUESTION

Actions that change what CUBRID is running WAIT until the change has landed,
the way `apps.uninstall_cubrid_wsl` does, so the question asked on the next
line is never a race. Questions answer with a plain value and never raise.
Nothing here asserts: deciding whether the answer is right is the case's job.

`cubrid service status` is parsed rather than trusted as an exit code, because
the product's own code is no help: both the Tray and the Starter decide the
whole service is up from the master line alone. An unknown component reads
None, never False -- a parser that guessed would report a component as DOWN on
the strength of this framework failing to understand the output.
"""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import constants
from . import distro

# How long a start or stop may take to show up in `cubrid service status`.
# The commands are synchronous, so this is normally one reading; past this,
# not-yet is the finding.
SERVICE_SETTLE_SECONDS = 120.0


# =========================================================================== #
# Parsing what CUBRID prints
# =========================================================================== #
def _sections(output: str) -> dict[str, list[str]]:
    """`cubrid service status` split into its per-component sections.

    Every line starting `@ cubrid ` opens a section; the lines after it are its
    body. Names match by PREFIX because the manager's header reads
    "@ cubrid manager server status".

    Components not named in constants.SERVICE_COMPONENTS are dropped. On build
    11.4-1.0.0-0003 that is `pl` and `gateway`: neither is in CUBRID's default
    `service=` line, so both being down is configuration rather than a finding.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in output.splitlines():
        line = raw.strip().lower()
        if line.startswith(constants.SERVICE_SECTION_PREFIX):
            rest = line[len(constants.SERVICE_SECTION_PREFIX):]
            current = next((c for c in constants.SERVICE_COMPONENTS
                            if rest.startswith(c)), None)
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None and line:
            sections[current].append(line)
    return sections


def parse_running_brokers(output: str) -> tuple[str, ...]:
    """The brokers listed as running, by name.

    A running broker service prints a TABLE, not a sentence, so a data row is
    identified by its SHAPE -- a name followed by a numeric PID -- rather than
    by column position. The leading `*` marks a running broker and is stripped;
    the header and the `====` rule fail the numeric-PID test.
    """
    names: list[str] = []
    for line in _sections(output).get("broker", []):
        tokens = line.lstrip("*+= ").split()
        if len(tokens) >= 2 and tokens[1].isdigit():
            names.append(tokens[0])
    return tuple(names)


def parse_started_databases(output: str) -> tuple[str, ...]:
    """The databases `cubrid service status` lists as started, by name.

    The SERVER section names one line per started database:

        @ cubrid server status
         Server demodb (rel 11.4, pid 1234)

    An EMPTY section is the normal state after a default install -- stock
    `cubrid.conf` leaves `server=` commented out, so nothing starts a database.
    Names come back LOWER-CASED, because the section splitter folds case.
    """
    names: list[str] = []
    for line in _sections(output).get("server", []):
        if not line.startswith(constants.SERVICE_SERVER_RUNNING_PREFIX):
            continue
        tokens = line.split()
        if len(tokens) >= 2:
            names.append(tokens[1])
    return tuple(names)


def parse_service_status(output: str) -> dict[str, bool | None]:
    """Split `cubrid service status` into a per-component verdict.

    Rules, in order, within each section:

    * "is not running" wins over everything;
    * "is running" means running;
    * the SERVER section prints neither, listing `Server <db> (rel ...)` per
      started database -- an EMPTY server section is False, not None;
    * the BROKER section prints neither either; it is running when it lists at
      least one broker.

    Anything else is None -- unknown, NOT false.
    """
    verdicts: dict[str, bool | None] = {}
    for component, lines in _sections(output).items():
        body = " ".join(lines)
        if constants.SERVICE_NOT_RUNNING_MARKER in body:
            verdicts[component] = False
        elif constants.SERVICE_IS_RUNNING_MARKER in body:
            verdicts[component] = True
        elif component == "server":
            verdicts[component] = any(
                line.startswith(constants.SERVICE_SERVER_RUNNING_PREFIX)
                for line in lines)
        elif component == "broker":
            verdicts[component] = bool(parse_running_brokers(output))
        else:
            verdicts[component] = None
    return verdicts


# csql prints results as a formatted table whose column header is the SELECT
# expression itself, so reading a value back out of the layout is guesswork.
# Marking it is not: the query asks for a delimited literal and the delimiters
# come back verbatim.
#
#     SELECT '<<' || TO_CHAR(COUNT(*)) || '>>' FROM code;
#     ...
#     '<<10>>'
_MARKER = re.compile(r"<<([^<>]*)>>")

# csql ECHOES the select expression as the column header, so the marker appears
# TWICE and the first occurrence is not a value:
#
#     '<<' || to_char(count(*)) || '>>'      <- the header: matches "<<...>>" too
#     '<<6>>'                                <- the value
#
# The echo always carries a quote and a pipe and no value this suite selects
# contains either, so that is what separates them.
_ECHOED_EXPRESSION = re.compile(r"['|]")

# "1 row selected." / "215 rows selected." -- csql's own count.
_ROWS_SELECTED = re.compile(r"(\d+)\s+rows?\s+selected", re.IGNORECASE)

# csql reports every failure on a line carrying this word, on stdout, and
# sometimes while still exiting 0.
_ERROR_LINE = re.compile(r"^.*\bERROR\b.*$", re.MULTILINE)


def marked(expression: str) -> str:
    """A SQL STRING expression wrapped so its value survives csql's layout."""
    return f"'<<' || {expression} || '>>'"


@dataclass(frozen=True)
class CsqlResult:
    """One csql invocation, with the three things a case asks of it."""

    returncode: int
    stdout: str
    stderr: str
    command: str
    #: Every line csql reported as an error, in order.
    errors: tuple[str, ...] = ()
    #: Values captured from `marked()` expressions, in the order they appeared.
    values: tuple[str, ...] = ()
    #: csql's own "N rows selected" count, or None when it printed none.
    rows_selected: int | None = None

    @property
    def ok(self) -> bool:
        """csql exited cleanly AND reported no error -- neither alone is enough."""
        return self.returncode == 0 and not self.errors

    @property
    def value(self) -> str | None:
        """The single marked value, or None if the query returned none."""
        return self.values[0] if self.values else None

    def describe(self) -> str:
        if self.ok:
            return (f"ok  rows={self.rows_selected} values={list(self.values)}"
                    f"  [{self.command}]")
        return (f"FAILED rc={self.returncode} errors={list(self.errors)}"
                f"  [{self.command}]")


def parse_csql(result: distro.CommandResult) -> CsqlResult:
    """Read a csql invocation's output. Never raises."""
    combined = f"{result.stdout}\n{result.stderr}"
    rows = _ROWS_SELECTED.search(combined)
    return CsqlResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        command=result.command,
        errors=tuple(line.strip() for line in _ERROR_LINE.findall(combined)),
        values=tuple(value for value in _MARKER.findall(combined)
                     if not _ECHOED_EXPRESSION.search(value)),
        rows_selected=int(rows.group(1)) if rows else None,
    )


# =========================================================================== #
# Running a command inside the distribution
# =========================================================================== #
def run(name: str, command: str, settings: dict[str, Any], *,
        timeout: int | None = None) -> distro.CommandResult:
    """One command inside the distribution, the way the PRODUCT runs them.

    `env_setup` from settings is prepended, mirroring how the installer and the
    Tray invoke WSL.

    Every command runs from the guest HOME: `wsl.exe` inherits the CALLER's
    working directory, and csql writes `csql.err` and `csql.access` into its
    working directory -- which would otherwise be the repository.

    `timeout` defaults to the budget a READ is sized for.
    """
    return distro.run(
        name, f'cd "$HOME" 2>/dev/null || cd /; {command}',
        user=settings["distro"]["user"] or None,
        env_setup=settings["distro"]["env_setup"],
        timeout=timeout or settings["timeouts"]["wsl_command_seconds"])


def _action_budget(settings: dict[str, Any]) -> int:
    """`cubrid service start` brings up three components and `createdb` writes
    volumes; neither fits the budget a status read is sized for."""
    return int(settings["timeouts"]["cubrid_command_seconds"])


def _start_daemon(name: str, command: str,
                  settings: dict[str, Any]) -> distro.CommandResult:
    """Run a command that leaves a daemon behind, under `nohup`.

    THIS IS NOT A CONVENIENCE. A command run through `wsl.exe --exec bash -c`
    belongs to a WSL session that ends when the invocation returns, and its
    daemons are sent SIGHUP with it. `cubrid_master` does not survive that;
    `cub_broker` and the manager do. So a plain `cubrid service start` reports
    `++ cubrid master start: success` and status then reports the master NOT
    running, indefinitely -- the framework killing the master, not the product
    failing to start it. `cubrid_starter.cpp` nohups for the same reason.

    FOREGROUND, no `&`: the call still waits and still returns the exit code.
    """
    return run(name, f"nohup {command}", settings,
               timeout=_action_budget(settings))


# =========================================================================== #
# The service: master, broker, manager
# =========================================================================== #
@dataclass(frozen=True)
class ServiceStatus:
    """One reading of `cubrid service status`."""

    ok: bool
    raw: str
    components: dict[str, bool | None] = field(default_factory=dict)
    brokers: tuple[str, ...] = ()
    started_databases: tuple[str, ...] = ()
    error: str | None = None

    def running(self, component: str) -> bool | None:
        return self.components.get(component)

    def all_running(self, components: tuple[str, ...]) -> bool:
        return all(self.components.get(c) is True for c in components)

    def any_running(self, components: tuple[str, ...]) -> bool:
        return any(self.components.get(c) is True for c in components)

    def started(self, database: str) -> bool:
        return database.casefold() in self.started_databases

    def describe(self) -> str:
        return (f"components={self.components} brokers={list(self.brokers)} "
                f"started={list(self.started_databases)}")


def service_status(name: str, settings: dict[str, Any]) -> ServiceStatus:
    """Read `cubrid service status`. Never raises.

    A command that could not be run reports `ok=False` with EMPTY verdicts --
    never fabricated ones.
    """
    try:
        result = run(name, "cubrid service status", settings)
    except Exception as exc:                     # a WSL-level failure
        return ServiceStatus(ok=False, raw="",
                             error=f"{type(exc).__name__}: {exc}")
    if not result.ok and not result.stdout:
        return ServiceStatus(
            ok=False, raw=result.stdout,
            error=f"rc={result.returncode} {result.stderr[:300]}")
    return ServiceStatus(
        ok=True, raw=result.stdout,
        components=parse_service_status(result.stdout),
        brokers=parse_running_brokers(result.stdout),
        started_databases=parse_started_databases(result.stdout))


def wait_for_status(name: str, predicate: Callable[[ServiceStatus], bool],
                    settings: dict[str, Any], *,
                    timeout: float = SERVICE_SETTLE_SECONDS,
                    interval: float = 2.0) -> ServiceStatus:
    """Poll `cubrid service status` until `predicate` holds, or time runs out.

    Returns the LAST reading either way and never raises: a component that did
    not get there is the case's finding to report.
    """
    deadline = time.monotonic() + timeout
    while True:
        status = service_status(name, settings)
        if predicate(status) or time.monotonic() >= deadline:
            return status
        time.sleep(interval)


def service_start(name: str, settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid service start`, then wait until master, broker and manager run."""
    result = _start_daemon(name, "cubrid service start", settings)
    wait_for_status(
        name, lambda s: s.all_running(constants.SERVICE_COMPONENTS_AWAITED),
        settings)
    return result


def service_stop(name: str, settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid service stop`, then wait until none of them reports running."""
    result = run(name, "cubrid service stop", settings,
                 timeout=_action_budget(settings))
    wait_for_status(
        name, lambda s: not s.any_running(constants.SERVICE_COMPONENTS_AWAITED),
        settings)
    return result


# A component is STOPPED when it no longer reports running -- not only when it
# reports "not running": a stopped master need not print a section per
# component, and a component with no section reads None.
def is_service_started(name: str, settings: dict[str, Any]) -> bool:
    """Is the CUBRID master -- the service itself -- running?"""
    return service_status(name, settings).running("master") is True


def is_service_stopped(name: str, settings: dict[str, Any]) -> bool:
    """Has the CUBRID master stopped reporting running?"""
    return service_status(name, settings).running("master") is not True


def is_broker_started(name: str, settings: dict[str, Any]) -> bool:
    """Is the broker running, with every broker a default install ships?

    A broker service that is up with no brokers running serves nothing, so the
    brokers are part of the answer.
    """
    status = service_status(name, settings)
    return (status.running("broker") is True
            and all(broker in status.brokers
                    for broker in constants.SERVICE_EXPECTED_BROKERS))


def is_broker_stopped(name: str, settings: dict[str, Any]) -> bool:
    """Has the broker stopped reporting running?"""
    return service_status(name, settings).running("broker") is not True


def is_manager_started(name: str, settings: dict[str, Any]) -> bool:
    """Is the CUBRID manager server running?"""
    return service_status(name, settings).running("manager") is True


def is_manager_stopped(name: str, settings: dict[str, Any]) -> bool:
    """Has the CUBRID manager server stopped reporting running?"""
    return service_status(name, settings).running("manager") is not True


def processes(name: str, settings: dict[str, Any], *,
              pattern: str = "cub_") -> str:
    """`pgrep -a` inside the distribution, for a failure message.

    Separates two causes that need different reports: a process PRESENT while
    status disagrees is a status or socket problem; one ABSENT started and died.
    """
    return run(name, f"pgrep -a {shlex.quote(pattern)} || echo '<no match>'",
               settings).stdout


def is_ready(name: str, settings: dict[str, Any]) -> bool:
    """Has a fresh install finished landing inside the distribution?

    Both arrive AFTER the bundle has exited: `ActionCreateDemodb` is dispatched
    asynchronously, and `ActionStartCubridService` is Return="asyncNoWait" and
    returns as soon as the MASTER is up, without waiting for broker or manager.
    """
    if not service_status(name, settings).all_running(
            constants.SERVICE_COMPONENTS_AWAITED):
        return False
    return is_database_exists(name, constants.DEMODB_NAME, settings)


# =========================================================================== #
# Databases
# =========================================================================== #
def databases(name: str, settings: dict[str, Any]) -> tuple[str, ...]:
    """Every database registered in databases.txt. Never raises.

    `|| true` so a MISSING databases.txt reads as no databases -- the correct
    answer on a machine installed with CREATE_DEMODB=0.
    """
    try:
        result = run(
            name, 'cat "$CUBRID_DATABASES/databases.txt" 2>/dev/null '
                  '|| cat "$CUBRID/databases/databases.txt" 2>/dev/null || true',
            settings)
    except Exception:
        return ()
    return tuple(line.split()[0] for line in result.stdout.splitlines()
                 if line.strip() and not line.strip().startswith("#"))


def is_database_exists(name: str, database: str,
                       settings: dict[str, Any]) -> bool:
    """Is this database registered in databases.txt?"""
    return database in databases(name, settings)


def create_database(name: str, database: str, locale: str,
                    settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid createdb` in a directory of its own under $CUBRID_DATABASES.

    createdb writes the volumes into the CURRENT directory, so it is run from a
    directory made for it; otherwise `deletedb` leaves debris behind.
    """
    quoted = shlex.quote(database)
    return run(name,
               f'mkdir -p "$CUBRID_DATABASES"/{quoted} && '
               f'cd "$CUBRID_DATABASES"/{quoted} && '
               f"cubrid createdb {quoted} {shlex.quote(locale)}",
               settings, timeout=_action_budget(settings))


def delete_database(name: str, database: str,
                    settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid deletedb`, then remove the directory it leaves behind.

    `rmdir` rather than `rm -rf`, so a deletedb that silently left volumes
    behind stays visible instead of being tidied away.
    """
    quoted = shlex.quote(database)
    return run(name,
               f"cubrid deletedb {quoted} </dev/null; "
               f'rmdir "$CUBRID_DATABASES"/{quoted} 2>/dev/null; true',
               settings, timeout=_action_budget(settings))


def server_start(name: str, database: str,
                 settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid server start <database>`, then wait until status lists it.

    Needed because `cubrid service start` starts no database: stock
    `cubrid.conf` leaves `server=` commented out.
    """
    result = _start_daemon(name, f"cubrid server start {shlex.quote(database)}",
                           settings)
    wait_for_status(name, lambda s: s.started(database), settings)
    return result


def server_stop(name: str, database: str,
                settings: dict[str, Any]) -> distro.CommandResult:
    """`cubrid server stop <database>`, then wait until status stops listing it."""
    result = run(name, f"cubrid server stop {shlex.quote(database)}", settings,
                 timeout=_action_budget(settings))
    wait_for_status(name, lambda s: not s.started(database), settings)
    return result


def is_server_started(name: str, database: str,
                      settings: dict[str, Any]) -> bool:
    """Does `cubrid service status` list this database as started?"""
    return service_status(name, settings).started(database)


# =========================================================================== #
# SQL
# =========================================================================== #
def csql(name: str, sql: str, database: str, settings: dict[str, Any], *,
         standalone: bool = False) -> CsqlResult:
    """Run SQL through csql and read the result.

    The MODE is not cosmetic:

    * `-S` (standalone) needs no server but takes the database files
      EXCLUSIVELY, so it fails while a server has the database open;
    * `-C` (client-server, the default) is what a real client uses, and needs
      `cubrid server start <database>` first.

    The user is always passed: csql prompts for credentials it was not given,
    and a prompt in an unattended run is a hang.
    """
    mode = "-S" if standalone else "-C"
    command = (f"csql {mode} -u {shlex.quote(constants.CSQL_DBA)} "
               f"-c {shlex.quote(sql)} {shlex.quote(database)}")
    return parse_csql(run(name, command, settings,
                          timeout=_action_budget(settings)))


# =========================================================================== #
# The engine itself
# =========================================================================== #
def version(name: str, settings: dict[str, Any]) -> str | None:
    """What `cubrid_rel` reports, or None when it prints nothing."""
    return run(name, "cubrid_rel", settings).stdout.strip() or None


def download(name: str, url: str, settings: dict[str, Any], *,
             timeout: int | None = None) -> tuple[distro.CommandResult, str]:
    """Fetch one file into the guest home with `wget`. Returns result and filename.

    The network is the DISTRIBUTION's, which is the Windows host's -- a machine
    that cannot reach the host cannot run the case, which is not the product
    failing.
    """
    filename = url.rsplit("/", 1)[-1]
    return (run(name, f"wget --no-verbose -O {shlex.quote(filename)} "
                      f"{shlex.quote(url)}", settings, timeout=timeout),
            filename)


def install_engine(name: str, filename: str, settings: dict[str, Any], *,
                   args: str = "", timeout: int | None = None
                   ) -> distro.CommandResult:
    """`chmod +x` then run CUBRID's Linux installer, answering its prompts.

    **THE BRACES ARE LOAD-BEARING.** `distro.run` appends ` </dev/null` to
    every command, and on a pipeline that redirect binds to the LAST command --
    the installer -- overriding the pipe. Without the group the installer reads
    EOF and reports `License not accepted. Exiting ...`, which is
    indistinguishable from the answers being wrong.

    `args` passes installer options: CPack self-extracting archives, which this
    is, accept `--skip-license` and `--exclude-subdir`.

    It does NOT stop CUBRID first -- the engine is replaced underneath a
    RUNNING service, which is what OPS-004 exercises.
    """
    quoted = shlex.quote(filename)
    options = f" {args}" if args else ""
    return run(name,
               f"chmod +x -- {quoted} && "
               f"{{ yes {shlex.quote(constants.ENGINE_INSTALLER_ANSWER)} "
               f"| ./{quoted}{options}; }}",
               settings, timeout=timeout)


def engine_locations(name: str, settings: dict[str, Any]) -> str:
    """Every CUBRID tree in the guest home, one per line, for a failure message.

    A self-extracting installer can put its payload in a VERSIONED
    subdirectory instead of over `$CUBRID`, and then `cubrid_rel` still reports
    the OLD engine because PATH still points at it.
    """
    return run(name, 'ls -1d "$HOME"/CUBRID* 2>/dev/null || echo "<none>"',
               settings).stdout
