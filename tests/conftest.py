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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from cubridwsl import config as config_mod
from cubridwsl import constants, preflight, reset, state as state_mod, verify
from cubridwsl.drivers import silent, wizard

# Lines that must survive a PASSING run. pytest hides print() output unless a
# test fails, which is exactly when the numbers matter least.
_NOTES: list[str] = []


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--installer", default=None,
                     help="Path to the installer bundle, overriding installer.path.")
    parser.addoption("--install-mode", default="passive", choices=["passive", "quiet"],
                     help="Bundle UI mode. Not cosmetic: /passive gives the MSI "
                          "UILevel 4 and runs the environment checks; /quiet "
                          "gives UILevel 2 and skips them.")


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

    Resolved lazily, as a fixture, so the read-only environment check can run on
    a machine that has no build at all -- only the tests that install need one.
    """
    try:
        return config_mod.resolve_installer(
            settings, request.config.getoption("--installer"))
    except config_mod.ConfigError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="session")
def install_mode(request) -> str:
    """'passive' or 'quiet' for this run."""
    return request.config.getoption("--install-mode")


@pytest.fixture(scope="session")
def run_dir(request) -> Path:
    """Where this run's artefacts go.

    Aligned with the --junitxml path run-tests.ps1 passes, so the JUnit XML, the
    installer logs and the state snapshots land together.
    """
    xmlpath = getattr(request.config.option, "xmlpath", None)
    import time
    directory = (Path(xmlpath).parent if xmlpath
                 else Path("reports") / time.strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def note() -> Callable[[str], None]:
    """Record a line that must appear even when the test passes."""
    return _note


@pytest.fixture(scope="session")
def dump_json(run_dir) -> Callable[[str, Any], Path]:
    """Write a named JSON artefact into this run's report directory.

    Diagnosing a failure should not require reproducing the run -- which here
    means reinstalling the product.
    """
    def _dump(name: str, payload: Any) -> Path:
        path = run_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
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
    result: Any = None
    _comparison: verify.Comparison | None = field(default=None, repr=False)

    @property
    def wsl_name(self) -> str:
        return str(self.options["CUB_DEFAULT_WSL_NAME"])

    @property
    def comparison(self) -> verify.Comparison:
        """The shared verification layer's verdict, computed once.

        It lives here rather than in each test module because every track needs
        exactly this object built from exactly these two fields. Two copies of
        that construction is how the silent and wizard tracks would begin to
        diverge without anyone noticing.
        """
        if self._comparison is None:
            self._comparison = verify.compare(self.options, self.state)
        return self._comparison


def _provision(driver_name: str, install_fn, settings, installer, run_dir,
               install_mode) -> Installation:
    """Clean the machine, install once, and hand the result to a whole group.

    `install_fn(options, log_path) -> result` is the only part that differs
    between the two drivers -- which is the point. If the tracks disagree about
    the resulting machine, the shared verification layer is wrong, and finding
    that out is what INS-001 is for.
    """
    preflight.require_windows()
    preflight.require_elevation()
    options = dict(constants.INSTALL_OPTIONS)
    wsl_name = str(options["CUB_DEFAULT_WSL_NAME"])

    _note(f"== provisioning a default install via the {driver_name} driver ==")
    _note(f"  installer  : {installer.describe()}")
    _note(f"  account    : {preflight.current_account()} "
          f"(elevated={preflight.is_elevated()}) -- this decides which HKCU "
          f"hive the assertions read")

    reset.ensure_clean(settings, installer, run_dir / f"reset-{driver_name}.log",
                       mode=install_mode, expected_name=wsl_name, note=_note)

    result = install_fn(options, run_dir / f"install-{driver_name}.log")
    _note(f"  install    : {result.describe()}")
    assert not result.timed_out, (
        f"the {driver_name} install timed out after {result.duration_seconds:.0f}s. "
        "The product's own WSL import step runs with no timeout of its own, so "
        "ours is the only backstop.")
    assert result.ok, f"the {driver_name} install failed: {result.describe()}"

    # Settling, not sleeping: demodb creation is dispatched asynchronously and
    # can finish after the installer has already exited.
    want_demodb = bool(int(options["CREATE_DEMODB"]))
    machine = state_mod.wait_until(
        lambda s: (s.registry.present and s.cubrid.distro_present
                   and (s.cubrid.demodb_present or not want_demodb)),
        settings, timeout=settings["timeouts"]["settle_seconds"],
        description=f"the {driver_name} install to settle")

    (run_dir / f"state-{driver_name}.json").write_text(
        json.dumps(machine.as_dict(), indent=2, default=str), encoding="utf-8")
    _note(f"  provisioned: distro v{machine.cubrid.distro_version}, "
          f"cubrid {machine.cubrid.cubrid_version}")

    return Installation(driver=driver_name, options=options, state=machine,
                        result=result)


@pytest.fixture(scope="session")
def silent_install(settings, installer, run_dir, install_mode) -> Installation:
    """A default install driven through the unattended CLI.

    DESTRUCTIVE and elevated. Session-scoped, so every test that asks for it
    shares one install cycle.
    """
    return _provision(
        "silent",
        lambda options, log: silent.install(
            installer, options, log,
            timeout=settings["timeouts"]["install_seconds"], mode=install_mode),
        settings, installer, run_dir, install_mode)


@pytest.fixture(scope="session")
def wizard_install(settings, installer, run_dir, install_mode) -> Installation:
    """A default install driven by clicking through the real wizard.

    Same provisioning path and same settle logic as `silent_install` -- only the
    install step differs. If the two produce machines that disagree, the shared
    verification layer is wrong, and detecting that is what INS-001 exists for.

    DESTRUCTIVE, elevated, and it drives the real mouse and keyboard: the
    machine cannot be used for anything else while it runs.
    """
    return _provision(
        "wizard",
        lambda options, log: wizard.install(
            installer, options, log,
            timeout=settings["timeouts"]["install_seconds"], note=_note),
        settings, installer, run_dir, install_mode)
