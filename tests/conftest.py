"""Fixtures for the whole suite.

Machine state is expressed as a FIXTURE, not a marker. A marker only labels a
test; a fixture PROVIDES the state and lets pytest group every test that shares
it behind one setup. That is the difference between one install for the three
silent cases and three installs.

There are two kinds of fixture here:

* read-only ones that describe the run -- `settings`, `installer`, `run_dir`;
* provisioning ones that INSTALL the product to reach a named state. Those are
  destructive, need an elevated shell, and each begins by making the machine
  clean, so it is safe for more than one to exist in a session.
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from xml.etree import ElementTree

from cubridwsl import config as config_mod
from cubridwsl import constants, preflight, reset, state as state_mod, verify
from cubridwsl.drivers import silent, wizard

# Lines that must survive a PASSING run. pytest hides print() output unless a
# test fails, which is exactly when the numbers matter least.
_NOTES: list[str] = []

# Facts the run's results depend on, written to reports/<run>/run.json. A result
# is only quotable if you can say which binary produced it, under which account,
# in which mode -- and a line printed to a terminal nobody kept is not that.
_RUN_FACTS: dict[str, Any] = {}
_INSTALLS: list[dict[str, Any]] = []

# The state report of the wizard install, if one happened in THIS session.
# INS-002 diffs against it. Recorded here rather than reached through the
# `wizard_install` fixture on purpose: requesting that fixture would PROVISION a
# wizard install, and INS-002 is a Silent case -- "no UI dependency" -- so
# `run-tests.ps1 silent` must never drive the wizard.
_WIZARD_REPORT: dict[str, Any] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--installer", default=None,
                     help="Path to the installer bundle, overriding installer.path.")


# The order the machine states are established in. INS-002 diffs its machine
# against the one INS-001 left, so the wizard install must happen FIRST -- and
# INS-001's own assertions read the live disk (the uninstall string names a file
# that must exist), so they have to run before the silent install replaces it.
INSTALL_FIXTURE_ORDER = ("wizard_install", "silent_install",
                         "wizard_all_custom_install", "silent_wsl1_install")


# The case ID carried in a test function name, e.g. test_ops_002_... -> 2.
_CASE_ID = re.compile(r"test_[a-z]+_(\d+)_")


def _case_number(item: pytest.Item) -> int:
    """The workbook case number in the test's name, or 0 if it carries none."""
    match = _CASE_ID.match(item.name)
    return int(match.group(1)) if match else 0


def _group_key(item: pytest.Item) -> tuple[int, int, int]:
    """(machine state, observation before action, then case ID).

    Sorted by the LAST fixture in INSTALL_FIXTURE_ORDER the test requests, not
    the first: INS-002 asks for BOTH installs, and what decides when it can run
    is the later one. Sorting on the first would put it in the wizard group and
    run it before INS-001 -- against a machine the silent install had not
    produced yet, diffing a snapshot that did not exist.

    The second component is the workbook's observation-vs-action rule, made
    executable. Within one machine state the cases that only READ what the
    installer left run before the cases that start, stop, connect or create --
    so an OPS case can never hand INS-002 a machine with the service stopped or
    a database it did not install.

    The third runs the action cases in WORKBOOK ORDER. That is load-bearing:
    OPS-001 does not connect to anything, so the evidence that a service cycle
    is non-destructive is OPS-002 connecting immediately after it on the same
    machine, and OPS-004 replaces the engine so it has to come last.

    Both of the last two components exist because the alternative is collection
    order, where `tests/INS/` sorts ahead of `tests/OPS/` because I precedes O,
    and `test_create_database.py` ahead of `test_service_lifecycle.py` because c
    precedes s -- neither of which has anything to do with what the cases need.
    Nothing here keys off a PATH: a case is placed by the machine state it asks
    for and the markers it carries, so moving a file between folders cannot
    change when it runs.
    """
    action = 1 if item.get_closest_marker("action") else 0
    names = set(getattr(item, "fixturenames", ()))
    indices = [i for i, fixture in enumerate(INSTALL_FIXTURE_ORDER)
               if fixture in names]
    return (max(indices) if indices else -1, action, _case_number(item))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the cases in the order their machine states require.

    Collection order is alphabetical by path, which says nothing about what a
    case needs -- see `_group_key`. The sort is stable, so cases that share a
    key keep their collection order.
    """
    items.sort(key=_group_key)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Print the run notes after the test table, pass or fail."""
    if not _NOTES:
        return
    terminalreporter.write_sep("=", "run notes")
    for line in _NOTES:
        terminalreporter.write_line(line)


def _note(text: str) -> None:
    _NOTES.append(text)
    print(text)


# --------------------------------------------------------------------------- #
# Describing the run
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def settings() -> dict[str, Any]:
    """settings.toml with settings.local.toml merged over it."""
    return config_mod.load_settings()


@pytest.fixture(scope="session")
def installer(request, settings) -> config_mod.InstallerPackage:
    """The bundle under test.

    Resolved lazily, as a fixture, so the read-only environment checks can run on
    a machine that has no build at all -- only the tests that install need one.
    """
    try:
        return config_mod.resolve_installer(
            settings, request.config.getoption("--installer"))
    except config_mod.ConfigError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="session")
def run_dir(request) -> Path:
    """Where this run's artefacts go.

    Aligned with the --junitxml path run-tests.ps1 passes, so the JUnit XML, the
    installer logs and the state snapshots land together.
    """
    xmlpath = getattr(request.config.option, "xmlpath", None)
    # Anchored to the repository, not to the current directory: `pytest` run
    # from anywhere else would otherwise scatter reports wherever it was called.
    directory = (Path(xmlpath).parent if xmlpath
                 else config_mod.REPO_ROOT / "reports" / time.strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def note() -> Callable[[str], None]:
    """Record a line that must appear even when the test passes."""
    return _note


@pytest.fixture(scope="session", autouse=True)
def run_report(run_dir, request) -> dict[str, Any]:
    """Record what this run ran against, before and after it runs.

    Written twice on purpose: once at the start, so a session that dies part-way
    still leaves the facts behind, and once at the end with what the installs
    actually did.
    """
    path = run_dir / "run.json"
    _RUN_FACTS.clear()
    _INSTALLS.clear()
    _RUN_FACTS.update({
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        # The format of every state-*.json this run writes. INS-002 may diff
        # against one of them in a LATER run, and a baseline written to a
        # different format reads as product differences -- see
        # state.REPORT_SCHEMA and _usable_reference.
        "state_schema": state_mod.REPORT_SCHEMA,
        # No run-wide "install mode": each driver pins its own, and every
        # install below records the exact command line it ran. A single
        # top-level field could only repeat ONE of them while claiming to
        # describe the run.
        **preflight.describe(),
    })
    _write_json(path, _RUN_FACTS)

    yield _RUN_FACTS

    _RUN_FACTS["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _RUN_FACTS["installs"] = _INSTALLS
    _write_json(path, _RUN_FACTS)


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def dump_json(run_dir) -> Callable[[str, Any], Path]:
    """Write a named JSON artefact into this run's report directory.

    Diagnosing a failure should not require reproducing the run -- which here
    means reinstalling the product.
    """
    def _dump(name: str, payload: Any) -> Path:
        return _write_json(run_dir / f"{name}.json", payload)
    return _dump


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #
@dataclass
class Installation:
    """A machine brought to a known state, and how it got there."""

    driver: str
    options: dict[str, Any]
    state: state_mod.MachineState
    # Expected values the OPTIONS cannot imply. Today only INS-003 uses it: the
    # wizard lets the user choose an install directory, while the silent path
    # always derives one. See verify.compare(expected=...).
    expected: dict[str, Any] = field(default_factory=dict)
    # Either driver's result: both carry ok / timed_out / duration_seconds /
    # describe() / as_dict(), which is what lets the fixture treat them alike.
    result: silent.RunResult | wizard.WizardResult | None = None
    _comparison: verify.Comparison | None = field(default=None, repr=False)

    @property
    def comparison(self) -> verify.Comparison:
        """The shared verification layer's verdict, computed once.

        It lives here rather than in each test module because every track needs
        exactly this object built from exactly these two fields. Two copies of
        that construction is how the silent and wizard tracks would begin to
        diverge without anyone noticing.
        """
        if self._comparison is None:
            self._comparison = verify.compare(self.options, self.state,
                                              expected=self.expected)
        return self._comparison


def _record_run_facts(installer: config_mod.InstallerPackage) -> None:
    """Which binary, under which account. A result is only quotable with both.

    Shared by every fixture that runs the installer -- the ones that expect it
    to succeed and the ones that expect it to refuse. A run whose facts were
    recorded by only some of its fixtures is a report that cannot be read.
    """
    _note(f"  installer  : {installer.describe()}")
    _RUN_FACTS["installer"] = {
        "path": str(installer.path), "sha256": installer.sha256,
        "cubrid_version": installer.cubrid_version,
        "installer_version": installer.installer_version,
        "build": installer.build,
    }
    _note(f"  account    : {preflight.current_account()} "
          f"(elevated={preflight.is_elevated()}) -- this decides which HKCU "
          f"hive the assertions read")


def _provision(driver_name: str, install_fn, settings, installer, run_dir, *,
               options: dict[str, Any] | None = None,
               expected: dict[str, Any] | None = None) -> Installation:
    """Clean the machine, install once, and hand the result to a whole group.

    `install_fn(options, log_path) -> result` is the only part that differs
    between the two drivers -- which is the point. If the tracks disagree about
    the resulting machine, the shared verification layer is wrong, and finding
    that out is what INS-001 is for.
    """
    preflight.require_windows()
    preflight.require_elevation()
    options = {**constants.INSTALL_OPTIONS, **(options or {})}
    wsl_name = str(options["CUB_DEFAULT_WSL_NAME"])

    _note(f"== provisioning the machine via the {driver_name} driver ==")
    _record_run_facts(installer)

    reset.ensure_clean(settings, installer, run_dir / f"reset-{driver_name}.log",
                       expected_name=wsl_name, note=_note)

    result = install_fn(options, run_dir / f"install-{driver_name}.log")
    _note(f"  install    : {result.describe()}")
    # Recorded before the assertions below, so a FAILED install is in the report
    # too -- with its exit code and the log paths it wrote.
    _INSTALLS.append({"driver": driver_name, "options": options,
                      **result.as_dict()})
    assert not result.timed_out, (
        f"the {driver_name} install timed out after {result.duration_seconds:.0f}s. "
        "The product's own WSL import step runs with no timeout of its own, so "
        "ours is the only backstop.")
    assert result.ok, f"the {driver_name} install failed: {result.describe()}"

    # Settling, not sleeping. THREE of the installer's effects land after it has
    # exited, so a snapshot taken the moment the bundle returns is a snapshot of
    # a machine that has not finished installing:
    #
    #   demodb          ActionCreateDemodb, dispatched asynchronously
    #   the Tray        ActionLaunchTrayApp, Return="asyncNoWait"
    #   CUBRID itself   ActionStartCubridService, Return="asyncNoWait", running
    #                   cubrid_starter.exe -- which nohup's `cubrid service
    #                   start` and returns as soon as the MASTER is up, without
    #                   waiting for the broker or the manager
    #
    # That last one is why the service components are waited for here. Reading
    # them once, as the installer exits, reports a startup still in progress as
    # components that failed to start.
    #
    # Only POSITIVE conditions are waited for. Nothing waits for something to
    # stay absent: "still not there after five minutes" is evidence of patience,
    # not of correctness.
    want_demodb = bool(int(options["CREATE_DEMODB"]))
    want_tray = bool(int(options["START_TRAY_APP"]))
    wanted_services = constants.SERVICE_COMPONENTS_AWAITED

    def _settled(s: state_mod.MachineState) -> bool:
        return (s.registry.present and s.cubrid.distro_present
                and (s.cubrid.demodb_present or not want_demodb)
                and (s.tray.running or not want_tray)
                and all(s.cubrid.service_components.get(c) is True
                        for c in wanted_services))

    try:
        machine = state_mod.wait_until(
            _settled, settings, timeout=settings["timeouts"]["settle_seconds"],
            description=(f"the {driver_name} install to settle (registry key, "
                         "the distribution"
                         + (", demodb" if want_demodb else "")
                         + (", the Tray process" if want_tray else "")
                         + f", and CUBRID {', '.join(wanted_services)})"))
    except TimeoutError:
        # Do NOT fail provisioning here. A component that never came up is a
        # finding about the PRODUCT, and it belongs in the test that asserts it
        # -- with the raw `cubrid service status` output beside it -- not in a
        # fixture error that reads like the framework broke. Everything else
        # about the machine is still worth asserting.
        machine = state_mod.snapshot(settings)
        _note(f"  settle     : timed out; CUBRID reported "
              f"{machine.cubrid.service_components}. Continuing so the "
              "assertions can report exactly what is and is not running.")

    # ONE login-shell read, here, after the machine has settled -- never inside
    # the loop above, which would pay an extra WSL round-trip every few seconds
    # to answer a question that cannot change while we watch.
    machine = machine.with_login_environment(
        state_mod.read_login_environment(settings, machine.registry.wsl_name))

    report = machine.as_dict()
    if driver_name == "wizard":
        _WIZARD_REPORT.clear()
        _WIZARD_REPORT.update(report)
    (run_dir / f"state-{driver_name}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    _note(f"  provisioned: distro v{machine.cubrid.distro_version}, "
          f"cubrid {machine.cubrid.cubrid_version}, "
          f"tray running={machine.tray.running}, "
          f"services {machine.cubrid.service_components}")

    return Installation(driver=driver_name, options=options, state=machine,
                        result=result, expected=dict(expected or {}))


@pytest.fixture(scope="session")
def wizard_install(settings, installer, run_dir) -> Installation:
    """Installed:default, established by clicking through the real wizard.

    INS-001's machine, and the reference every other case is measured against:
    its "Applies To" reads *Scenario · establishes Installed:default · OWNS the
    post-install assertion set*. INS-002 diffs its own machine against this one,
    which is why INSTALL_FIXTURE_ORDER runs this first.

    DESTRUCTIVE, elevated, and it drives the real mouse and keyboard: the
    machine cannot be used for anything else while it runs.
    """
    return _provision(
        "wizard",
        lambda options, log: wizard.install(
            installer, options, log,
            timeout=settings["timeouts"]["install_seconds"], note=_note),
        settings, installer, run_dir)


@pytest.fixture(scope="session")
def silent_install(settings, installer, run_dir) -> Installation:
    """Installed:default, established through the unattended CLI. INS-002.

    Two deliberate differences from `wizard_install`:

    * **/quiet, pinned.** INS-002 names it: "run the bundle unattended:
      `/quiet /norestart /log <path>` with NO property overrides". /quiet is
      MSI UILevel 2, which means ActionEnvironmentCheck never runs at all --
      the workbook's SCOPE LIMIT. A green INS-002 is therefore no evidence the
      prerequisite gate works; ENV-008 and ENV-009 own that.
    * **no option overrides.** `constants.INSTALL_OPTIONS` IS the set of
      shipping defaults, and passing them explicitly would test that the
      command line works, not that the defaults do. INS-002 asks for the
      defaults the bundle chooses when told nothing.

    DESTRUCTIVE and elevated. It runs AFTER `wizard_install` (see
    INSTALL_FIXTURE_ORDER) and cleans the machine first, which is what makes
    its own precondition -- a Clean host -- true.
    """
    return _provision(
        "silent",
        lambda options, log: silent.install(
            installer, {}, log,
            timeout=settings["timeouts"]["install_seconds"], mode="quiet"),
        settings, installer, run_dir)


@pytest.fixture
def check_against_bundle(installer, note):
    """The two post-install assertions `verify.compare()` structurally cannot make.

    `compare(options, state)` has exactly two inputs, and both of these compare
    the machine against a THIRD thing -- the bundle under test, which is neither
    an install option nor a piece of machine state.

    A fixture rather than inline code because INS-001 and INS-002 both need
    them: the workbook says the silent install must satisfy "the full
    post-install assertion set defined in INS-001 ... version ... Add/Remove
    entry". Written twice, the two tracks would drift, and drift here is
    invisible -- both copies keep passing while they check different things.

    Returns the problems it found so the caller can collect them and assert
    once, which is what lets one run report every failure instead of the first.
    """
    def _check(installation, case_id: str) -> list[str]:
        problems: list[str] = []

        # Expected values are read out of the bundle's own FILENAME, never
        # hard-coded -- a literal "11.4.6" here would start failing correct
        # builds one release later.
        reported = installation.state.cubrid.cubrid_version
        declared = installer.cubrid_version            # e.g. "11.4"
        note(f"  {case_id}-report    : cubrid_rel -> {reported!r} "
             f"(bundle declares {declared!r})")
        if not reported:
            problems.append(
                "`cubrid_rel` produced no output. That "
                "is the signature of a broken ~/.cubrid.sh: with CRLF line "
                "endings $CUBRID/bin never reaches PATH and every product "
                "feature fails at once, while the install still reports "
                "success. The environment.* checks read the same thing "
                "directly.")
        elif declared not in reported:
            problems.append(
                f"the bundle declares CUBRID {declared} but the distribution "
                f"reports {reported!r}. The image and "
                "the installer that ships it have diverged.")

        arp = installation.state.arp
        arp_declared = installer.installer_version     # e.g. "1.0.0"
        note(f"  {case_id}-arp    : {arp.display_name!r} {arp.display_version} "
             f"by {arp.publisher!r} at {arp.location}")
        note(f"  {case_id}-arp    : uninstall -> {arp.uninstall_string!r}")

        if not (arp.display_version or "").startswith(arp_declared):
            problems.append(
                f"Apps & Features reports version {arp.display_version!r} but "
                f"the bundle under test declares {arp_declared!r}. Burn writes "
                "a four-part version, so a prefix is expected -- a mismatch "
                "means the entry belongs to a different build than the one "
                "this run installed.")

        # "UninstallString is present and resolves to an executable that EXISTS
        # on disk." That the uninstall string actually WORKS is deliberately NOT
        # asserted -- running it is destructive, and LCM-002 covers it.
        if not arp.uninstall_string:
            problems.append("the Apps & Features entry carries no "
                            "UninstallString, so Windows cannot remove the "
                            "product.")
        else:
            executable = Path(_executable_in(arp.uninstall_string))
            if not executable.is_file():
                problems.append(
                    f"the uninstall command names {executable}, which is not "
                    "on disk. Windows would offer an Uninstall button that "
                    "cannot work, and every later run of this suite cleans the "
                    "machine with this exact command.")
        return problems
    return _check


def _executable_in(command: str) -> str:
    """The program a Windows command line names, without its arguments.

    Burn writes the path QUOTED, and this handles that. It does not ASSUME it:
    an unquoted string split on the quote character returns the whole line,
    arguments included, and `is_file()` then fails on a perfectly good uninstall
    command -- reporting a product defect that is a parsing bug here. Quoting is
    the product's choice to change, so it is read, not required.

    A space in an unquoted path is unsplittable by any rule; Windows itself has
    the same problem, so a path like that is broken before this sees it.
    """
    command = command.strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    return command.split(" ", 1)[0]


@dataclass(frozen=True)
class WizardReference:
    """INS-001's machine, as something INS-002 can diff against."""

    report: dict[str, Any]
    source: str


@pytest.fixture(scope="session")
def wizard_reference(installer) -> WizardReference | None:
    """The state INS-001 left, WITHOUT provisioning it.

    INS-002 needs INS-001's snapshot to diff against, and the workbook lists
    that as a PRECONDITION -- "INS-001 has been run, so its state snapshot
    exists to diff against" -- not as work INS-002 performs. Requesting the
    `wizard_install` fixture would perform it, and that is wrong twice over:

    * INS-002 is Automation = Silent, which the Overview defines as "no UI
      dependency". Driving the wizard to satisfy it would make
      `run-tests.ps1 silent` need pywinauto and an untouched mouse.
    * it costs a whole extra install cycle -- ~150 seconds -- every time
      INS-002 is run on its own.

    So this looks for the snapshot instead, newest first:

    1. the wizard install from THIS session, if INS-001 already ran;
    2. otherwise the newest `state-wizard.json` under reports/, accepted ONLY
       when that run used the SAME bundle. The SHA-256 is the gate, not the
       filename: the build number in the name is a commit count, so two
       different binaries can share one. Diffing today's silent install against
       a snapshot of a different build would report product changes as install
       differences.

    Returns None when neither exists, and the case says so rather than
    pretending the comparison happened.

    Session-scoped, so it reports through the module-level `_note` rather than
    the `note` FIXTURE: `note` is function-scoped, and pytest refuses a
    session-scoped fixture that depends on a narrower one.
    """
    if _WIZARD_REPORT:
        _note("  INS-002-diff    : diffing against the wizard install from "
              "this session")
        return WizardReference(dict(_WIZARD_REPORT), "this session")

    for run in sorted(config_mod.REPO_ROOT.glob("reports/*/"), reverse=True):
        candidate = _usable_reference(run, installer)
        if candidate is not None:
            _note(f"  INS-002-diff    : diffing against {candidate.source}")
            return candidate
    return None


def _usable_reference(run: Path,
                      installer: config_mod.InstallerPackage
                      ) -> WizardReference | None:
    """One past run, if its wizard snapshot is a legitimate baseline.

    Four gates, and each rules out a class of false difference -- a diff
    against a bad baseline reports the BASELINE's problems as INS-002 findings,
    which is worse than having no baseline at all:

    * **the same REPORT FORMAT.** `diff_reports` renders a key one side lacks as
      `<absent>`, so a snapshot written before a field was added to
      `MachineState.as_dict()` differs from a healthy machine on every one of
      them. A run.json with no `state_schema`, or a different one, is refused --
      which is why the number must be bumped whenever the format changes.
    * **the same bundle**, by SHA-256 rather than filename. The build number in
      the name is a commit count, so two different binaries can share one.
    * **the same account.** The product writes to HKCU and to that user's
      Desktop, so a snapshot taken as somebody else differs in the registry and
      shortcut paths for reasons that have nothing to do with the driver.
    * **INS-001 PASSED in that run.** Reports are kept for failures too, and
      this repository has several -- runs where the broker and manager were
      still starting, for instance. Diffing a healthy silent install against a
      half-started wizard machine would report the reference's problems.
    """
    snapshot_path, facts_path = run / "state-wizard.json", run / "run.json"
    junit_path = run / "junit.xml"
    if not (snapshot_path.is_file() and facts_path.is_file()):
        return None
    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        if facts.get("state_schema") != state_mod.REPORT_SCHEMA:
            return None
        if facts.get("installer", {}).get("sha256") != installer.sha256:
            return None
        if facts.get("account") != preflight.current_account():
            return None
        if not _ins_001_passed(junit_path):
            return None
        report = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return WizardReference(
        report,
        f"{snapshot_path} (INS-001 passed {facts.get('started')}, same bundle "
        f"and account)")


def _ins_001_passed(junit_path: Path) -> bool:
    """Whether that run recorded INS-001 as passing.

    A run with no junit.xml is rejected rather than trusted: the snapshot may
    be from a session that died part-way, and `_provision` writes the state file
    before the assertions ever execute.
    """
    if not junit_path.is_file():
        return False
    try:
        root = ElementTree.parse(junit_path).getroot()
    except ElementTree.ParseError:
        return False
    for case in root.iter("testcase"):
        if "ins_001" not in (case.get("name") or ""):
            continue
        return not (case.findall("failure") or case.findall("error")
                    or case.findall("skipped"))
    return False


# INS-003's two custom values. Deliberately DIFFERENT from each other: the
# default install directory is [LocalAppDataFolder][CUB_DEFAULT_WSL_NAME], so a
# directory that merely followed the custom name would still BE the default and
# the case would prove nothing.
#
# The name is the one the workbook records as already confirmed by hand for this
# case ("custom WSL name CUBRID-WSL confirmed correct"), so an automated run and
# a manual one are comparing the same thing. It is not a prefix or a suffix of
# the shipping default CUBRID-FOR-WSL, which keeps "the custom name took" and
# "the default was left alone" impossible to confuse for one another.
INS_003_WSL_NAME = "CUBRID-WSL"
INS_003_DIR_NAME = "CUBRID-INS003-Dir"


@pytest.fixture(scope="session")
def wizard_all_custom_install(settings, installer, run_dir) -> Installation:
    """Installed:allCustom -- every option changed, through the wizard. INS-003.

    The five changes the workbook lists: three checkboxes unticked (demodb, Tray
    auto-start, desktop shortcuts) plus a custom distro name and a custom install
    directory. IS_WSL2_MODE stays at 1: the workbook's option list does not
    include it, and the baseline this inherits from INS-001 expects VERSION=2.
    INS-004 owns WSL1.

    START_TRAY_APP also stays at 1, and that is the interesting part. With
    REG_TRAY_APP off and START_TRAY_APP on, the two Tray settings are in
    OPPOSITION -- the configuration that shows they act independently, which
    the workbook folded in from the former trayIndependence case.

    DESTRUCTIVE, elevated, drives the real mouse and keyboard.
    """
    install_dir = ntpath.join(os.environ.get("LOCALAPPDATA", ""),
                              INS_003_DIR_NAME)
    options = {"CUB_DEFAULT_WSL_NAME": INS_003_WSL_NAME,
               "REG_TRAY_APP": 0, "CREATE_SHORTCUT": 0, "CREATE_DEMODB": 0}
    return _provision(
        "wizard-all-custom",
        lambda opts, log: wizard.install(
            installer, opts, log, install_dir=install_dir,
            timeout=settings["timeouts"]["install_seconds"], note=_note),
        settings, installer, run_dir, options=options,
        expected={"registry.install_dir": state_mod.normalize_path(install_dir)})


# INS-004's one override. Deliberately the ONLY property that reaches the
# command line: constants.INSTALL_OPTIONS IS the set of shipping defaults, and
# passing the other five explicitly would test that the command line works
# rather than that this property does. It is also what keeps the resulting
# machine comparable to the one INS-002 leaves -- one variable moved, so one
# attributable difference.
INS_004_OPTIONS = {constants.OPTION_WSL2_MODE: 0}


@pytest.fixture(scope="session")
def silent_wsl1_install(settings, installer, run_dir) -> Installation:
    """Installed:wsl1 -- the distribution imported at WSL 1. INS-004.

    /quiet, pinned, as in `silent_install`: it is MSI UILevel 2, so
    ActionEnvironmentCheck never runs and cannot contribute a difference of its
    own. The only thing that moves between the two machines is IS_WSL2_MODE.

    LAST in INSTALL_FIXTURE_ORDER, and that placement is load-bearing.
    SetupWslDistro reaches WSL 1 by running `wsl --set-default-version 1` and
    then importing WITHOUT `--version`, so the mode is carried by a
    MACHINE-GLOBAL setting that this install leaves behind at 1. Anything
    provisioned afterwards would depend on the product setting it back through
    that same call -- whose exit code the product does not check.

    DESTRUCTIVE and elevated. It cleans the machine first, which is what makes
    its own precondition -- a Clean host -- true.
    """
    return _provision(
        "silent-wsl1",
        lambda options, log: silent.install(
            installer, dict(INS_004_OPTIONS), log,
            timeout=settings["timeouts"]["install_seconds"], mode="quiet"),
        settings, installer, run_dir, options=INS_004_OPTIONS)


# --------------------------------------------------------------------------- #
# Runs that must leave NOTHING installed
# --------------------------------------------------------------------------- #
@dataclass
class CleanRun:
    """A run that was supposed to install nothing, and the machine after it."""

    result: silent.RunResult | wizard.WizardResult
    state: state_mod.MachineState
    # What is present that must not be, in reset's words. Empty is the pass.
    residue: list[str] = field(default_factory=list)
    # The overrides the run was given, so a case reads what it was actually
    # handed rather than importing a value from this file -- two sibling
    # conftests both import as the bare name `conftest`, and the second to load
    # shadows the first.
    options: dict[str, Any] = field(default_factory=dict)


def _attempt(attempt_name: str, run_fn, settings, installer, run_dir, *,
             options: dict[str, Any] | None = None,
             expected_name: str | None = None) -> CleanRun:
    """Clean the machine, run something that must NOT install, and read it back.

    The counterpart to `_provision`, and deliberately not the same function.
    `_provision` asserts the install succeeded, which is exactly what these
    cases require it not to do -- folding them together would mean a flag that
    turns off the one assertion holding the other fixtures up.

    Nothing is waited for afterwards. The install fixtures wait because three of
    the installer's effects land after it exits; here the expectation is
    ABSENCE, and absence is never waited for -- "still not there after five
    minutes" is evidence of patience, not of correctness.
    """
    preflight.require_windows()
    preflight.require_elevation()

    _note(f"== {attempt_name}: a run that must leave nothing installed ==")
    _record_run_facts(installer)

    # The machine must be clean BEFORE, or "nothing was installed" afterwards
    # says nothing at all -- residue left by an earlier run would read exactly
    # like residue this run created.
    reset.ensure_clean(settings, installer,
                       run_dir / f"reset-{attempt_name}.log",
                       expected_name=expected_name, note=_note)

    result = run_fn(run_dir / f"install-{attempt_name}.log")
    _note(f"  attempt    : {result.describe()}")
    _INSTALLS.append({"driver": attempt_name, "options": dict(options or {}),
                      **result.as_dict()})

    state, residue = reset.residue_now(settings, expected_name=expected_name)
    (run_dir / f"state-{attempt_name}.json").write_text(
        json.dumps(state.as_dict(), indent=2, default=str), encoding="utf-8")
    _note(f"  machine    : {len(residue)} item(s) that must not be there")
    return CleanRun(result=result, state=state, residue=residue,
                    options=dict(options or {}))


# The name a run that wrongly installed would install UNDER, so a leftover
# directory is looked for in the right place. Neither case asks for a custom
# name: INS-005 changes no options at all, and INS-006's own name cannot name a
# Windows directory, which is the whole reason the product rejects it.
_DEFAULT_WSL_NAME = str(constants.INSTALL_OPTIONS[constants.OPTION_WSL_NAME])


@pytest.fixture(scope="session")
def wizard_cancelled(settings, installer, run_dir) -> CleanRun:
    """A wizard driven to the last page before Ready to Install, then cancelled.

    INS-005. Requests no install fixture, so the ordering hook runs it before
    every case that installs -- which suits it twice over: it is cheap, and it
    leaves behind exactly the clean machine the install fixtures need.

    DESTRUCTIVE in permission only -- it must be able to install, so it runs
    elevated and drives the real mouse and keyboard, but a passing run installs
    nothing.
    """
    return _attempt(
        "wizard-cancel",
        lambda log: wizard.cancel(
            installer, log, timeout=settings["timeouts"]["install_seconds"],
            note=_note),
        settings, installer, run_dir, expected_name=_DEFAULT_WSL_NAME)


# INS-006's rejected name, and ONE of CheckWslName's seven rules -- it also
# refuses an empty name, one over 64 characters, a leading '-', '.' and '..', a
# trailing '.', and the reserved device names. A disallowed CHARACTER is the
# class the check exists for: a colon cannot name a Windows directory, and the
# WSL name becomes a directory name. It is also safe to carry through a command
# line, since Burn splits an override on its FIRST '='.
INS_006_OPTIONS = {constants.OPTION_WSL_NAME: "CUBRID:FOR:WSL"}


@pytest.fixture(scope="session")
def silent_invalid_name(settings, installer, run_dir) -> CleanRun:
    """An unattended install whose WSL name the product must refuse. INS-006.

    /quiet, as the workbook's step names. It also proves the refusal is not a
    UI behaviour: ActionCheckWslName and ActionInvalidWslNameError are sequenced
    in InstallExecuteSequence, the only sequence a silent install runs at all.

    DESTRUCTIVE in permission only -- a passing run installs nothing.
    """
    return _attempt(
        "silent-invalid-name",
        lambda log: silent.install(
            installer, dict(INS_006_OPTIONS), log,
            timeout=settings["timeouts"]["install_seconds"], mode="quiet"),
        settings, installer, run_dir, options=INS_006_OPTIONS,
        expected_name=_DEFAULT_WSL_NAME)
