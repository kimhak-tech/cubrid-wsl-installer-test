"""CUBRID's own command line, run inside the installed distribution.

Where `drivers/` exercise the INSTALLER, this exercises the INSTALLED PRODUCT:
`cubrid service`, `cubrid server`, `csql`, `createdb`/`deletedb`. It is what
Category 03 (CUBRID Operational) needs and the install cases do not, because
their rule is the opposite one -- INS-001 owns everything observable WITHOUT
performing an action, and anything that starts, stops, connects, queries or
creates is an OPS case.

It follows the same layering rule as a driver: **it acts and it reports; it
never asserts.** Deciding whether `master: False` is correct belongs to the
test, which knows whether it just issued a stop.

Two things it does not do, deliberately:

* it does not invoke `wsl.exe`. `distro.run` is the single choke point for that
  and this goes through it like everything else, so encoding, the login-shell
  distinction, timeouts and the safety rules stay solved in one place;
* it does not read Windows-side state. That is `state.py`'s job, and an OPS
  case that needs both asks for both rather than growing a second reader here.

The output parsers live in `state.py` beside the ones they are variations of --
`parse_service_status`, `parse_started_databases`, `parse_service_command` --
because a recorded product text format is machine state, not a driver detail.
"""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import constants, distro, state as state_mod

# csql prints results as a formatted table whose column header is the SELECT
# expression itself and whose values are padded to the column width, so reading
# a value back out of the layout is guesswork that changes with the expression.
# Marking it does not: the query asks for a delimited literal and the delimiters
# come back verbatim, whatever the table looks like around them.
#
#     SELECT '<<' || TO_CHAR(COUNT(*)) || '>>' FROM code;
#     ...
#     '<<10>>'
#
# This is the same reasoning as pinning `parse_service_status` against recorded
# text -- a product output contract -- except that here the contract is one we
# state in the query rather than one we have to reverse-engineer.
_MARKER = re.compile(r"<<([^<>]*)>>")

# ...with one catch, found by the self-check rather than by a destructive run:
# csql ECHOES the select expression as the column header, so the marker appears
# TWICE and the first occurrence is not a value:
#
#     '<<' || to_char(count(*)) || '>>'      <- the header: matches "<<...>>" too
#     ======================================
#     '<<6>>'                                <- the value
#
# The echoed header always carries the concatenation the query was written with
# -- a quote and a pipe -- and no value this suite selects contains either, so
# that is what separates them. Queries added here must keep that true: mark a
# value that can contain a quote or a `|` and it will be discarded as an echo.
_ECHOED_EXPRESSION = re.compile(r"['|]")

# "1 row selected." / "215 rows selected." -- csql's own count, which is what
# lets a `SELECT *` be compared against a `SELECT COUNT(*)` without reading
# either result set row by row.
_ROWS_SELECTED = re.compile(r"(\d+)\s+rows?\s+selected", re.IGNORECASE)

# csql reports every failure -- syntax, connection, permission -- on a line
# carrying this word, and it does so on stdout rather than stderr. A row VALUE
# containing "ERROR" would be a false positive; no query this suite issues can
# return one, and the alternative (trusting the exit code alone) misses errors
# csql reports while still exiting 0.
_ERROR_LINE = re.compile(r"^.*\bERROR\b.*$", re.MULTILINE)


def marked(expression: str) -> str:
    """A SQL string expression wrapped so its value survives csql's layout.

    Pass a STRING expression: `marked("TO_CHAR(COUNT(*))")`, not
    `marked("COUNT(*)")`. CUBRID's `||` concatenates strings, and an implicit
    numeric conversion is not something to rely on for a value an assertion
    reads.

    The marked value must not itself contain a quote or a `|`, or `parse_csql`
    will discard it as csql's echo of this expression -- see
    `_ECHOED_EXPRESSION`.
    """
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
        """csql exited cleanly AND reported no error.

        Both, because neither alone is sufficient: csql reports some failures
        on stdout while still exiting 0, and a non-zero exit with no error line
        is a failure whose reason we would otherwise not have.
        """
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

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "ok": self.ok}


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


@dataclass(frozen=True)
class ServiceStatus:
    """One reading of `cubrid service status`.

    `components` and `brokers` are the SAME parsers INS-001 asserts through, so
    an OPS case and the post-install check can never disagree about what the
    product said.
    """

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
        """Whether ANY of them reports running.

        The negation of this -- not the conjunction of `is False` -- is what
        the workbook's "no longer reports running" asks for. A component that
        does not appear in the output at all reads None, and `cubrid service
        status` against a stopped master need not print a section per service.
        Demanding False from every component would report that layout as four
        failures.
        """
        return any(self.components.get(c) is True for c in components)

    def started(self, database: str) -> bool:
        return database.casefold() in self.started_databases

    def describe(self) -> str:
        return (f"components={self.components} brokers={list(self.brokers)} "
                f"started={list(self.started_databases)}")

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "components": self.components,
                "brokers": list(self.brokers),
                "started_databases": list(self.started_databases),
                "error": self.error, "raw": self.raw}


# `cubrid <service|server> <action>` verbs that leave a daemon running. Those
# invocations are detached from the WSL session that issued them; see
# `CubridCli._detach` for what happens when they are not.
DAEMON_ACTIONS = frozenset({"start", "restart"})


@dataclass(frozen=True)
class CubridCli:
    """CUBRID's command line inside one distribution.

    Bound to a distribution NAME read from the product's own registry key --
    what was actually installed -- for the same reason `state.read_cubrid` is:
    a name taken from settings could point this at a different distribution
    than the one under test.
    """

    name: str
    user: str | None = None
    env_setup: str = ""
    timeout: int = 300

    # ----------------------------------------------------------------- #
    # The one way anything gets run
    # ----------------------------------------------------------------- #
    def run(self, command: str, *,
            timeout: int | None = None) -> distro.CommandResult:
        """One command inside the distribution, the way the product runs them.

        `env_setup` is prepended, mirroring how the installer and the Tray
        invoke WSL -- OPS cases are about whether CUBRID WORKS, not about
        whether a login shell exports it. That second question is INS-001's
        `environment.*` checks, which deliberately do the opposite.

        Every command is run from the guest HOME, and that is not tidiness.
        `wsl.exe` inherits the CALLER's working directory and translates it, so
        a command issued from the repository runs in `/mnt/<drive>/...` over
        drvfs -- and CUBRID tools write their logs into the working directory.
        Two consequences: csql writes `csql.err` and `csql.access` into the
        repository, and every file operation goes through the Windows
        filesystem bridge.

        Scoped to this class rather than to `distro.run`, deliberately. The
        install cases read through `distro.run` too, they pass today, and none
        of their commands writes a file -- so changing the shared choke point
        would put three green cases at risk to fix a problem only this layer
        has.
        """
        return distro.run(self.name, f'cd "$HOME" 2>/dev/null || cd /; {command}',
                          user=self.user, env_setup=self.env_setup,
                          timeout=timeout or self.timeout)

    # ----------------------------------------------------------------- #
    # Service and server control
    # ----------------------------------------------------------------- #
    def _detach(self, command: str, action: str,
                detached: bool | None) -> str:
        """Prefix `nohup` when the command leaves a daemon behind.

        THIS IS NOT A CONVENIENCE, and removing it breaks OPS-001. A command run
        through `wsl.exe --exec bash -c ...` belongs to a WSL session that ends
        when the invocation returns, and the daemons started inside it are sent
        SIGHUP with it. `cubrid_master` does not survive that; `cub_broker` and
        the manager do. So a plain `cubrid service start` reports

            ++ cubrid master start: success

        and then `cubrid service status` reports the master NOT running, for as
        long as you care to poll -- while the broker and manager sit there with
        fresh PIDs. That is the framework killing the master, not the product
        failing to start it, and asserting on it fabricates a defect.

        `nohup` is the fix because `nohup` is what the PRODUCT does:
        `cubrid_starter.cpp` nohups `cubrid service start`, which is why the
        machine the installer leaves has a master that stays up. Mirroring the
        product is the rule everywhere else in this framework
        (`distro.run`'s `env_setup`, the silent driver's command line), and this
        is the same rule.

        It runs in the FOREGROUND -- no `&` -- so the call still waits, still
        returns the exit code, and still yields the per-component output the
        assertions read. `nohup` only redirects stdout when stdout is a
        terminal; here it is a pipe, so the output passes through untouched.
        """
        if detached is None:
            detached = action in DAEMON_ACTIONS
        return f"nohup {command}" if detached else command

    def service(self, action: str, *, detached: bool | None = None
                ) -> tuple[distro.CommandResult, dict[str, bool | None]]:
        """`cubrid service <action>`, with its per-component verdict.

        Returns the raw result too: the verdict says what the product claimed,
        and a case that wants to report WHY needs the text it claimed it in.

        `detached` defaults to True for the actions that LEAVE A DAEMON BEHIND
        -- see `_detach`, which explains why that is not optional.
        """
        result = self.run(self._detach(f"cubrid service {action}",
                                       action, detached))
        return result, state_mod.parse_service_command(result.stdout)

    def service_status(self) -> ServiceStatus:
        """Read `cubrid service status`. Never raises.

        A command that could not be run reports `ok=False` with its error and
        EMPTY verdicts -- never fabricated ones. An unknown component reads
        None, so "the framework could not tell" can never be mistaken for "the
        product is down".
        """
        try:
            result = self.run("cubrid service status")
        except Exception as exc:                     # a WSL-level failure
            return ServiceStatus(ok=False, raw="",
                                 error=f"{type(exc).__name__}: {exc}")
        if not result.ok and not result.stdout:
            return ServiceStatus(
                ok=False, raw=result.stdout,
                error=f"rc={result.returncode} {result.stderr[:300]}")
        return ServiceStatus(
            ok=True, raw=result.stdout,
            components=state_mod.parse_service_status(result.stdout),
            brokers=state_mod.parse_running_brokers(result.stdout),
            started_databases=state_mod.parse_started_databases(result.stdout))

    def server(self, action: str, database: str, *,
               detached: bool | None = None) -> distro.CommandResult:
        """`cubrid server <action> <database>` -- start or stop ONE database.

        Needed because `cubrid service start` starts none: stock `cubrid.conf`
        leaves `server=` commented out, so the master, broker and manager come
        up and no database does. Whether the image should set `server=demodb`
        is open with development; until it is settled, a case that connects has
        to start the database itself.

        `detached` follows the same rule as `service()`: a start leaves a daemon
        behind, and a daemon started from a `wsl.exe` invocation does not
        survive it unless it is detached. See `_detach`.
        """
        return self.run(self._detach(
            f"cubrid server {action} {shlex.quote(database)}",
            action, detached))

    def processes(self, pattern: str) -> distro.CommandResult:
        """`pgrep -a <pattern>` inside the distribution.

        Used only when a component did not reach the state it was asked for,
        and it is not a restatement of that failure: it separates the two
        causes, which need different reports. A process that EXISTS while
        `cubrid <component> status` says otherwise is a status or socket
        problem; a process that is ABSENT started and died. Guessing between
        them is how a framework bug gets filed against the product.
        """
        return self.run(f"pgrep -a {shlex.quote(pattern)} || echo '<no match>'")

    def wait_for_status(self, predicate: Callable[[ServiceStatus], bool], *,
                        timeout: float = 120, interval: float = 2.0
                        ) -> ServiceStatus:
        """Poll `cubrid service status` until it satisfies `predicate`.

        Returns the LAST reading whether or not the predicate held, and never
        raises. A component that did not reach the expected state is a finding
        about the PRODUCT and belongs in the test that asserts it -- with the
        raw status beside it -- not in an exception that reads like the harness
        broke. That is the same choice `conftest._provision` makes when an
        install does not settle.

        `cubrid service start` and `stop` are synchronous, so this normally
        returns on its first reading; it exists so that a product that reports
        a transition slightly late is a slow pass rather than a false failure,
        and so that nothing in this layer ever sleeps for a fixed duration.
        """
        deadline = time.time() + timeout
        while True:
            status = self.service_status()
            if predicate(status) or time.time() >= deadline:
                return status
            time.sleep(interval)

    # ----------------------------------------------------------------- #
    # SQL
    # ----------------------------------------------------------------- #
    def csql(self, sql: str, database: str, *, standalone: bool = False,
             user: str = constants.CSQL_DBA,
             timeout: int | None = None) -> CsqlResult:
        """Run SQL through csql and read the result.

        `standalone` picks csql's mode, and the choice is not cosmetic:

        * **`-S` (standalone)** opens the database files directly and needs no
          running server -- but it takes them EXCLUSIVELY, so it cannot be used
          while a server has the database open.
        * **`-C` (client-server, the default)** is the path a real client uses,
          and it requires `cubrid server start <database>` first.

        So a case that wants both reads standalone FIRST, then starts the
        server, then connects client-server. Reversing that order fails on the
        exclusive lock, not on anything about the product.

        The user is always passed explicitly: csql prompts for credentials it
        was not given, and a prompt in an unattended run is a hang.
        """
        mode = "-S" if standalone else "-C"
        command = (f"csql {mode} -u {shlex.quote(user)} "
                   f"-c {shlex.quote(sql)} {shlex.quote(database)}")
        return parse_csql(self.run(command, timeout=timeout))

    def scalar(self, expression: str, database: str, *, source: str = "",
               **kwargs: Any) -> CsqlResult:
        """`SELECT <marked expression> [FROM <source>]` -- one readable value.

        The value comes back in `result.value`; see `marked()` for why the
        query marks it rather than the parser guessing at csql's table layout.
        """
        clause = f" FROM {source}" if source else ""
        return self.csql(f"SELECT {marked(expression)}{clause}",
                         database, **kwargs)

    # ----------------------------------------------------------------- #
    # Databases
    # ----------------------------------------------------------------- #
    def databases(self) -> tuple[str, ...]:
        """Every database registered in databases.txt.

        `|| true` so that "there is no databases.txt" is an ANSWER rather than
        a command error -- the same reason `state.read_cubrid` does it.
        """
        result = self.run(
            'cat "$CUBRID_DATABASES/databases.txt" 2>/dev/null '
            '|| cat "$CUBRID/databases/databases.txt" 2>/dev/null || true')
        return tuple(line.split()[0] for line in result.stdout.splitlines()
                     if line.strip() and not line.strip().startswith("#"))

    # ----------------------------------------------------------------- #
    # The engine itself -- OPS-004
    # ----------------------------------------------------------------- #
    def download(self, url: str, *, name: str | None = None,
                 timeout: int | None = None
                 ) -> tuple[distro.CommandResult, str]:
        """Fetch one file into the guest home. Returns the result and the name.

        `wget` rather than curl because the image HAS wget -- the generated
        Dockerfile installs it and uses it to fetch CUBRID itself -- and the
        workbook's step names it.

        The network here is the DISTRIBUTION's, which is the Windows host's,
        not this framework's. A machine that cannot reach the download host
        cannot run OPS-004, and the case says so rather than failing as though
        the product were at fault.
        """
        filename = name or url.rsplit("/", 1)[-1]
        return (self.run(f"wget --no-verbose -O {shlex.quote(filename)} "
                         f"{shlex.quote(url)}", timeout=timeout),
                filename)

    def engine_locations(self) -> distro.CommandResult:
        """Every CUBRID tree in the guest home, one per line.

        Read only when the engine version did not change as expected, and not a
        restatement of that: a self-extracting installer can put its payload in
        a VERSIONED subdirectory (`CUBRID-11.4.5.1866-.../`) instead of over
        `$CUBRID`, and then `cubrid_rel` still reports the OLD engine because
        PATH still points at the old one. That is indistinguishable from "the
        install did nothing" until you look at the directory names.
        """
        return self.run('ls -1d "$HOME"/CUBRID* 2>/dev/null || echo "<none>"')

    def install_engine(self, filename: str, *,
                       answer: str = constants.ENGINE_INSTALLER_ANSWER,
                       args: str = "",
                       timeout: int | None = None) -> distro.CommandResult:
        """`chmod +x` then run CUBRID's Linux installer, answering its prompts.

        The two workbook steps in one call, because a `chmod` that succeeded
        while the install failed is not a distinction worth a separate
        assertion -- and the installer refuses to be interesting without it.

        **THE BRACES ARE LOAD-BEARING.** `distro.run` appends ` </dev/null` to
        every command, and on a pipeline that redirect binds to the LAST command
        -- the installer -- overriding the pipe. Without the group the installer
        reads EOF at its first prompt and reports

            Do you accept the license? [yN]:
            License not accepted. Exiting ...

        which is indistinguishable from the answers being wrong. Two runs of
        OPS-004 were spent on the answer WORD ("yes", then "y") before the
        SHAPE of the command was suspected; both failed for this reason and
        neither had anything to do with the word. `{ ... ; }` takes the
        redirect and the pipe inside still wins -- verified by running both
        forms against a stub that reads one line.

        `yes <answer> |` then feeds that word to every prompt. `args` passes
        installer options ahead of it, for the case where a prompt needs
        something other than the repeated answer -- CPack self-extracting
        archives, which this is, accept `--skip-license` and
        `--exclude-subdir`, and using them removes the reliance on stdin
        altogether.

        The FULL transcript comes back in the result and is written to the run
        report, which is what makes an unanticipated prompt visible rather than
        silent.

        NOT detached: this must finish before anything reads the result, and it
        leaves no daemon behind -- the service is stopped across it.
        """
        quoted = shlex.quote(filename)
        options = f" {args}" if args else ""
        return self.run(
            f"chmod +x -- {quoted} && "
            f"{{ yes {shlex.quote(answer)} | ./{quoted}{options}; }}",
            timeout=timeout)

    def createdb(self, database: str,
                 locale: str = constants.OPS_TESTDB_LOCALE
                 ) -> distro.CommandResult:
        """`cubrid createdb` in a directory of its own under $CUBRID_DATABASES.

        createdb writes the volumes into the CURRENT directory and registers
        that path in databases.txt, so it is run from a directory made for it.
        Without the `cd`, the volumes land wherever the shell happened to start
        and `deletedb` leaves a directory of debris behind.
        """
        quoted = shlex.quote(database)
        return self.run(
            f'mkdir -p "$CUBRID_DATABASES"/{quoted} && '
            f'cd "$CUBRID_DATABASES"/{quoted} && '
            f"cubrid createdb {quoted} {shlex.quote(locale)}")

    def deletedb(self, database: str) -> distro.CommandResult:
        """`cubrid deletedb`, then remove the directory it leaves behind.

        `rmdir` rather than `rm -rf`: it removes the directory only once
        deletedb has emptied it, so a deletedb that silently left volumes
        behind stays visible instead of being tidied away.
        """
        quoted = shlex.quote(database)
        return self.run(f"cubrid deletedb {quoted} </dev/null; "
                        f'rmdir "$CUBRID_DATABASES"/{quoted} 2>/dev/null; true')


def for_installation(settings: dict[str, Any], wsl_name: str | None) -> CubridCli:
    """Bind the CLI to an installed machine, from the settings and its registry.

    `wsl_name` comes from the product's own key, never from settings -- see
    `CubridCli`.
    """
    if not wsl_name:
        raise distro.DistroError(
            "the product registry key recorded no WslName, so there is no "
            "distribution to run CUBRID in. The install did not complete.")
    cfg = settings.get("distro", {})
    timeouts = settings.get("timeouts", {})
    return CubridCli(
        name=wsl_name,
        user=cfg.get("user") or None,
        env_setup=cfg.get("env_setup", ""),
        # `cubrid service start` brings up three components and `createdb`
        # writes volumes, so neither fits inside the general per-command
        # budget that reads are sized for.
        timeout=int(timeouts.get("cubrid_command_seconds", 300)))
