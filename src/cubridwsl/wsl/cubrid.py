"""CUBRID itself, seen from inside the distribution the installer created.

Deliberately SMALL for now. It carries only what a lifecycle case needs: is
CUBRID up, and is demodb there -- the two questions that decide whether an
install has actually finished. The service and server control the OPS cases
need will move here when those cases are migrated.

`cubrid service status` is parsed rather than trusted as an exit code, because
the product's own code is no help: both the Tray and the Starter decide the
whole service is up from the master line alone. An unknown component reads
None, never False -- a parser that guessed would report a component as DOWN on
the strength of this framework failing to understand the output, which is a
fabricated defect report.
"""
from __future__ import annotations

from typing import Any

from .. import constants
from . import distro


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


def _running_brokers(output: str) -> tuple[str, ...]:
    """The brokers listed as running, by name.

    A running broker service prints a TABLE, not a sentence, so a data row is
    identified by its SHAPE -- a name followed by a numeric PID -- rather than
    by column position. A column being added or reordered then does not
    silently stop finding brokers. The leading `*` marks a running broker and
    is stripped; the header and the `====` rule fail the numeric-PID test.
    """
    names: list[str] = []
    for line in _sections(output).get("broker", []):
        tokens = line.lstrip("*+= ").split()
        if len(tokens) >= 2 and tokens[1].isdigit():
            names.append(tokens[0])
    return tuple(names)


def parse_service_status(output: str) -> dict[str, bool | None]:
    """Split `cubrid service status` into a per-component verdict.

    Rules, in order, within each section:

    * "is not running" wins over everything -- a section carrying both lines
      must resolve to not-running;
    * "is running" means running;
    * the SERVER section prints neither, listing `Server <db> (rel ...)` per
      started database, so that is its positive signal -- and an EMPTY server
      section is False, not None: no lines means no database is started;
    * the BROKER section prints neither either; it is running when it lists at
      least one broker. A broker service with an empty table serves nothing.

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
            verdicts[component] = bool(_running_brokers(output))
        else:
            verdicts[component] = None
    return verdicts


def _run(name: str, command: str, settings: dict[str, Any]):
    """One command inside the distribution, the way the PRODUCT runs them.

    `env_setup` from settings is prepended, mirroring how the installer and the
    Tray invoke WSL. Whether a plain LOGIN shell also exports CUBRID is a
    different question, and an install case owns it.
    """
    return distro.run(
        name, command,
        user=settings["distro"]["user"],
        env_setup=settings["distro"]["env_setup"],
        timeout=settings["timeouts"]["wsl_command_seconds"])


def service_status(name: str, settings: dict[str, Any]) -> dict[str, bool | None]:
    """Per-component verdicts from `cubrid service status`.

    Returns an EMPTY dict when the command could not be run at all -- never
    fabricated verdicts. A caller asking "is everything up" then correctly gets
    no, and a caller asking "is anything down" does not get a false yes.
    """
    try:
        result = _run(name, "cubrid service status", settings)
    except Exception:
        return {}
    return parse_service_status(result.stdout + result.stderr)


def databases(name: str, settings: dict[str, Any]) -> tuple[str, ...]:
    """Every database registered in the distribution's databases.txt.

    `|| true` so a MISSING databases.txt reads as no databases rather than as a
    command error -- which is the correct answer on a machine installed with
    CREATE_DEMODB=0.
    """
    try:
        result = _run(
            name, "cat $CUBRID_DATABASES/databases.txt 2>/dev/null || true",
            settings)
    except Exception:
        return ()
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line.split()[0])
    return tuple(names)


def is_ready(name: str, settings: dict[str, Any]) -> bool:
    """Has a fresh install finished landing inside the distribution?

    Two things, and both arrive AFTER the bundle has exited:

    * `ActionCreateDemodb` is dispatched asynchronously;
    * `ActionStartCubridService` is Return="asyncNoWait" and runs
      cubrid_starter.exe, which returns as soon as the MASTER is up -- without
      waiting for the broker or the manager.

    So a machine read the moment the installer returns is a machine that has
    not finished installing, and uninstalling one mid-write is not the scenario
    any lifecycle case describes.
    """
    status = service_status(name, settings)
    if not all(status.get(component) is True
               for component in constants.SERVICE_COMPONENTS_AWAITED):
        return False
    return constants.DEMODB_NAME in databases(name, settings)
