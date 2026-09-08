"""Install options in, expected machine state out -- and the difference.

This is the shared verification layer. BOTH drivers assert through it, which is
what makes "a wizard install reaches the same state as a silent install" a
structural fact rather than a claim in a comment: adding a check here
strengthens both tracks at once, and neither can quietly drift from the other.

Checks are FLAT and NAMED (`registry.wsl_name`, `shortcuts.tray_link`) rather
than a mirror of MachineState's shape, so a failure names the thing that is
wrong instead of dumping two nested structures for a human to diff.
"""
from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from typing import Any, Callable

from . import constants
from .state import MachineState, normalize_path


@dataclass(frozen=True)
class Check:
    """One checkable fact: what the options imply, and how to read what is there.

    `area` groups checks so a test can assert only the evidence it is about, and
    get a failure naming one area rather than all of them.

    `requires` names a check this one depends on. When that prerequisite fails,
    this check is reported as SKIPPED rather than as a second failure -- because
    it is not an independent finding. One unreadable `cubrid service status`
    used to produce four failure lines for one cause, and a login shell that
    would not open produced five; the extra lines said nothing the first did not
    and buried the one line that carried the reason.

    Skipping is not the same as passing. A skipped check is printed as `[skip]`
    with the prerequisite that caused it, and it is in the JSON report, so a
    fact that went unverified can never be mistaken for one that was verified.
    """

    name: str
    area: str
    expected: Callable[[dict[str, Any]], Any]
    actual: Callable[[MachineState], Any]
    requires: str | None = None


def _install_dir_for(options: dict[str, Any]) -> str | None:
    """Silent mode derives InstallDir from the WSL name and OVERWRITES any
    INSTALLFOLDER passed on the command line (ActionUpdateInstallFolder, under
    `NOT Installed AND UILevel < 5`). That is why a custom-install-path case is
    a wizard-only test."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    return normalize_path(ntpath.join(local, str(options["CUB_DEFAULT_WSL_NAME"])))


CHECKS: tuple[Check, ...] = (
    # --- identity ----------------------------------------------------------
    Check("registry.present", "identity",
          lambda o: True, lambda s: s.registry.present),
    Check("registry.Installed", "identity",
          lambda o: True, lambda s: s.registry.installed,
          requires="registry.present"),
    Check("registry.wsl_name", "identity",
          lambda o: str(o["CUB_DEFAULT_WSL_NAME"]),
          lambda s: s.registry.wsl_name,
          requires="registry.present"),
    Check("registry.install_dir", "identity",
          _install_dir_for,
          lambda s: normalize_path(str(s.registry.install_dir))
                    if s.registry.install_dir else None,
          requires="registry.present"),
    # The registry can name a directory that is not on disk -- a partially
    # rolled-back install looks exactly like a good one from the registry alone.
    # The dev team's prototype cross-checks the same two facts.
    Check("install_dir.exists", "identity", lambda o: True,
          lambda s: bool(s.registry.install_dir and s.registry.install_dir.is_dir()),
          requires="registry.install_dir"),

    # --- the distribution ---------------------------------------------------
    Check("distro.present", "distro", lambda o: True,
          lambda s: s.cubrid.distro_present),
    Check("distro.version", "distro",
          lambda o: 2 if int(o["IS_WSL2_MODE"]) else 1,
          lambda s: s.cubrid.distro_version,
          requires="distro.present"),

    # --- startup entries ----------------------------------------------------
    # The starter is registered UNCONDITIONALLY, so "no CUBRID entries under
    # Run" would fail against a correct build.
    Check("startup.starter", "startup", lambda o: True,
          lambda s: s.startup.starter_registered),
    Check("startup.tray_app", "startup",
          lambda o: bool(int(o["REG_TRAY_APP"])),
          lambda s: s.startup.tray_app_registered),

    # --- desktop shortcuts --------------------------------------------------
    # CREATE_SHORTCUT is a SINGLE toggle controlling both together, not two
    # independent options, so both take their expectation from it and one
    # appearing without the other is a product defect.
    #
    # EXISTENCE ONLY, deliberately, and this is a REDUCTION from what INS-001
    # asks for. The workbook requires each shortcut to "resolve to a target
    # that exists on disk with the correct
    # icon -- not merely present by filename". Those three checks per shortcut
    # were implemented, they worked, and they were REMOVED on 2026-09-08 at
    # Kimhak's direction because the product does not yet do this correctly:
    #
    #   the tray shortcut names NO target at all. CubridCustomActions.cpp builds
    #   it as `installDir + "\\" + trayAppFile` and InstallDir is stored WITH a
    #   trailing backslash, so the path carries a doubled separator and Windows
    #   saves the link with no LinkInfo LocalBasePath. The WSL shortcut is
    #   unaffected -- it is built from wslPath, which has no trailing separator,
    #   and it resolved correctly every run.
    #
    # So this is a KNOWN COVERAGE GAP against a KNOWN DEFECT, not a decision
    # that targets do not matter. `state.read_shortcut()` still parses and
    # reports both links in every run's notes and JSON, so the evidence keeps
    # arriving; restoring the assertions is re-adding the three Checks below.
    # INS-001 surfaces the gap on every run, passing or failing.
    #
    #   Check(f"shortcuts.{name}_resolvable",         requires=f"..._link")
    #   Check(f"shortcuts.{name}_target_exists",      requires=f"..._resolvable")
    #   Check(f"shortcuts.{name}_icon_is_the_target", requires=f"..._resolvable")
    #
    Check("shortcuts.distro_link", "shortcuts",
          lambda o: bool(int(o["CREATE_SHORTCUT"])),
          lambda s: s.shortcuts.distro.exists),
    Check("shortcuts.tray_link", "shortcuts",
          lambda o: bool(int(o["CREATE_SHORTCUT"])),
          lambda s: s.shortcuts.tray.exists),

    # --- Apps & Features ----------------------------------------------------
    # The bundle entry. The MSI carries ARPSYSTEMCOMPONENT=1 and is hidden.
    # INS-001 asserts name, version and publisher are correct with a functional
    # uninstall string. The VERSION is compared in the test instead of here,
    # because it is checked against the bundle under test -- and `compare()`
    # sees only options and state, of which the installer is neither.
    #
    # There is deliberately NO `arp.display_name` check. `read_arp` finds the
    # entry BY DisplayName, so such a check could only ever restate
    # `arp.present`: it would be True whenever the entry was found and None
    # whenever it was not. A check that cannot fail on its own is worse than no
    # check -- it reads like coverage.
    Check("arp.present", "arp", lambda o: True, lambda s: s.arp.present),
    Check("arp.publisher", "arp",
          lambda o: constants.ARP_PUBLISHER, lambda s: s.arp.publisher,
          requires="arp.present"),

    # --- the Tray -----------------------------------------------------------
    # "Tray auto-start + immediate launch" are two DIFFERENT options: the Run
    # entry above is REG_TRAY_APP, this is START_TRAY_APP. The Tray is launched
    # asynchronously (ActionLaunchTrayApp is asyncNoWait), so the install
    # fixture waits for this rather than snapshotting as the installer exits.
    Check("tray.running", "tray",
          lambda o: bool(int(o["START_TRAY_APP"])),
          lambda s: s.tray.running),

    # --- payload ------------------------------------------------------------
    # demodb is created asynchronously: wait for it with state.wait_until()
    # before comparing, never from a bare snapshot taken as the installer exits.
    Check("cubrid.demodb", "payload",
          lambda o: bool(int(o["CREATE_DEMODB"])),
          lambda s: s.cubrid.demodb_present),

    # --- CUBRID is up -------------------------------------------------------
    # The install auto-starts CUBRID, so RUNNING is the expected state with no
    # start command issued -- which is what keeps this an observation rather
    # than a Category 03 action case.
    #
    # Each component separately, as INS-001 requires: a zero exit code and a
    # running master say nothing about the broker, and the product's own code
    # never looks past the master line.
    #
    # There is deliberately NO "the status output was readable" check. That is
    # a fact about this framework's parser, not about the product, and it does
    # not belong in a product assertion. When the output cannot be classified a
    # component reads None -- never False -- and None != True, so it fails with
    # the raw text alongside it in the run notes rather than passing quietly.
    #
    # Timing: master, broker and manager start ASYNCHRONOUSLY, so the install
    # fixture waits for them (SERVICE_COMPONENTS_AWAITED). `server` is asserted
    # but never waited for -- nothing starts a database server asynchronously.
    *(Check(f"service.{component}", "service", lambda o: True,
            lambda s, c=component: s.cubrid.service_components.get(c))
      for component in constants.SERVICE_COMPONENTS_EXPECTED_RUNNING),

    # The broker service being "up" is not the same as the brokers running. A
    # broker service with an empty table serves nothing, so the two brokers a
    # default install ships are checked BY NAME -- and a failure then says which
    # one is missing instead of just "broker".
    #
    # They depend on the service itself, so a broker service that is down is one
    # finding rather than three.
    *(Check(f"service.broker.{broker}", "service", lambda o: True,
            lambda s, b=broker: b in s.cubrid.brokers,
            requires="service.broker")
      for broker in constants.SERVICE_EXPECTED_BROKERS),

    # --- the environment a real user session gets ---------------------------
    # Read in a FRESH LOGIN shell, and NOT sourced by us: see
    # state.read_login_environment() for why either shortcut would answer a
    # different question.
    Check("environment.read", "environment", lambda o: True,
          lambda s: (True if s.login_environment.read
                     else f"<not read: {s.login_environment.error}>")),
    Check("environment.CUBRID", "environment",
          lambda o: constants.CUBRID_HOME,
          lambda s: s.login_environment.get("CUBRID"),
          requires="environment.read"),
    Check("environment.CUBRID_DATABASES", "environment",
          lambda o: constants.CUBRID_DATABASES,
          lambda s: s.login_environment.get("CUBRID_DATABASES"),
          requires="environment.read"),
    # Membership, not equality -- so a failure names the missing entry rather
    # than dumping the whole PATH as a "value".
    Check("environment.PATH_has_cubrid_bin", "environment", lambda o: True,
          lambda s: f"{constants.CUBRID_HOME}/bin" in s.login_environment.path_entries,
          requires="environment.read"),
    # A CRLF ~/.cubrid.sh makes every exported value end in a carriage return,
    # so $CUBRID/bin and $CUBRID_DATABASES both name directories that do not
    # exist -- while the install still reports success. Found for real on
    # 2026-09-01, which is why it is a standing check and not a comment.
    Check("environment.no_carriage_return", "environment", lambda o: False,
          lambda s: s.login_environment.carriage_return,
          requires="environment.read"),
)

_BY_NAME = {check.name: check for check in CHECKS}

_missing = sorted({c.requires for c in CHECKS if c.requires} - set(_BY_NAME))
if _missing:                                    # pragma: no cover - import-time
    raise RuntimeError(
        f"these checks require {_missing}, which do not exist. A renamed "
        "prerequisite would silently stop suppressing anything, and the "
        "duplicate failure lines would quietly come back.")

AREAS = tuple(dict.fromkeys(check.area for check in CHECKS))


@dataclass(frozen=True)
class Result:
    """One check evaluated against a real machine."""

    name: str
    area: str
    expected: Any
    actual: Any
    # Set when a prerequisite check failed, so this one was not an independent
    # question. `matches` stays honest -- it still reports whether the values
    # agreed -- but a skipped check is never counted as a failure.
    skipped_because: str | None = None

    @property
    def matches(self) -> bool:
        return self.expected == self.actual

    @property
    def skipped(self) -> bool:
        return self.skipped_because is not None

    @property
    def failed(self) -> bool:
        """Whether this is a finding. Skipped checks are not."""
        return not self.skipped and not self.matches

    def __str__(self) -> str:
        if self.skipped:
            return (f"[skip] {self.name:<26} not evaluated: "
                    f"{self.skipped_because} failed")
        mark = "ok  " if self.matches else "FAIL"
        return (f"[{mark}] {self.name:<26} expected={self.expected!r:<22} "
                f"actual={self.actual!r}")


@dataclass
class Comparison:
    """Every check evaluated for one set of install options."""

    results: list[Result]

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.failed]

    @property
    def skipped(self) -> list[Result]:
        return [r for r in self.results if r.skipped]

    @property
    def ok(self) -> bool:
        return not self.failures

    def problems(self, *areas: str) -> str:
        """What went wrong in the named areas -- empty string means all good.

        With no areas, reports on everything. An unknown area is reported as
        loudly as a failure: a renamed area would otherwise turn an assertion
        into an assertion over nothing at all, which passes.
        """
        if areas:
            unknown = [a for a in areas if a not in AREAS]
            if unknown:
                return (f"no such check area(s): {unknown}. Known: {list(AREAS)}")
            wanted = [r for r in self.results if r.area in areas]
        else:
            wanted = self.results
        return "\n".join(str(r) for r in wanted if r.failed)

    def as_list(self) -> list[dict[str, Any]]:
        return [{**r.__dict__, "matches": r.matches, "skipped": r.skipped,
                 "failed": r.failed} for r in self.results]


def compare(options: dict[str, Any], state: MachineState, *,
            expected: dict[str, Any] | None = None) -> Comparison:
    """Diff the machine against what the install options imply.

    Neither side is allowed to raise: a check that cannot be evaluated names
    itself in the report and lets every other check report normally.

    `expected` overrides individual checks by name, for facts the OPTIONS
    cannot imply. Today that is exactly one: a wizard-chosen install directory.
    `_install_dir_for` derives the expected path from LOCALAPPDATA and the WSL
    name because that is what the SILENT install always produces -- but the
    wizard lets the user choose, and `ActionUpdateInstallFolder` only overwrites
    the choice when UILevel < 5. So for INS-004 the expectation comes from the
    scenario rather than from a derivation that only holds silently.

    An override for a check that does not exist is refused: it would silently
    do nothing, and the case would then assert the derived default it was
    written to replace.

    Evaluated in TWO passes. The first reads every check; the second demotes any
    check whose `requires` prerequisite failed to SKIPPED. Two passes rather
    than one so the order of CHECKS never decides the outcome -- a prerequisite
    written below its dependents would otherwise silently stop working.
    """
    overrides = dict(expected or {})
    unknown = sorted(set(overrides) - set(_BY_NAME))
    if unknown:
        raise KeyError(
            f"expected overrides name {unknown}, which are not checks. Known "
            f"checks: {sorted(_BY_NAME)}")

    evaluated: dict[str, Result] = {}
    for check in CHECKS:
        try:
            actual = check.actual(state)
        except Exception as exc:
            actual = f"<error: {type(exc).__name__}: {exc}>"
        if check.name in overrides:
            wanted = overrides[check.name]
        else:
            try:
                wanted = check.expected(options)
            except Exception as exc:
                wanted = f"<error: {type(exc).__name__}: {exc}>"
        evaluated[check.name] = Result(check.name, check.area, wanted, actual)

    def _blocked_by(name: str, seen: frozenset[str] = frozenset()) -> str | None:
        """The NEAREST failing prerequisite, or None if this check stands alone.

        Nearest rather than root, so a chain stays readable: with
        install_dir.exists -> registry.install_dir -> registry.present, each
        skipped check names the one directly beneath it and the report can be
        followed down to the real cause.

        `seen` guards against a cycle in `requires`. A cycle would otherwise
        recurse forever at import-time-adjacent code, which is a far worse
        failure than the mistake that caused it.
        """
        check = _BY_NAME[name]
        if check.requires is None or name in seen:
            return None
        if evaluated[check.requires].failed:
            return check.requires
        # The prerequisite passed on its own terms but may itself be blocked --
        # in which case this check is equally moot.
        return _blocked_by(check.requires, seen | {name})

    results = []
    for check in CHECKS:
        blocker = _blocked_by(check.name)
        result = evaluated[check.name]
        results.append(result if blocker is None
                       else Result(result.name, result.area, result.expected,
                                   result.actual, skipped_because=blocker))
    return Comparison(results)
