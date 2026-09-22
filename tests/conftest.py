"""Fixtures for the whole suite.

Two kinds of fixture live here:

* read-only ones that describe the run -- `settings`, `installer`, `run_dir`;
* `check_product_absent`, an assertion set more than one CATEGORY needs. A
  helper only one category uses belongs in that category's own conftest.

Nothing here installs the product. Every case installs for itself, or through
its own category's `suite_installation`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from cubridwsl import config as config_mod
from cubridwsl import constants, preflight
from cubridwsl.windows import apps, files, registry, tray
from cubridwsl.wsl import distro

# Lines that must survive a PASSING run. pytest hides print() output unless a
# test fails, which is exactly when the numbers matter least.
_NOTES: list[str] = []

# Facts the run's results depend on, written to reports/<run>/run.json. A result
# is only quotable if you can say which binary produced it, under which account,
# in which mode -- and a line printed to a terminal nobody kept is not that.
_RUN_FACTS: dict[str, Any] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--installer", default=None,
                     help="Path to the installer bundle, overriding installer.path.")


# The category and case number in a test function name,
# e.g. test_ops_002_... -> ("ops", 2).
_CASE_ID = re.compile(r"test_([a-z]+)_(\d+)_")


def _group_key(item: pytest.Item) -> tuple[int, str, int]:
    """(observation before action, category, case number).

    The first component runs the cases that only READ a machine before the
    ones that start, stop, connect or create against it, so OPS and TRA follow
    INS and LCM.

    The second keeps a category's cases together. Categories reuse the same
    case numbers, so sorting on the number alone would interleave LCM-003 and
    LCM-004 with INS-003 and INS-004 -- and an INS case uninstalls the product
    the module-scoped LCM `suite_installation` shares.

    The third runs a category in WORKBOOK ORDER. OPS-004 replaces the engine
    and must come last on its installation.

    Nothing here keys off a PATH: a case is placed by the markers it carries
    and its name, so moving a file between folders cannot change when it runs.
    """
    action = 1 if item.get_closest_marker("action") else 0
    match = _CASE_ID.match(item.name)
    category, number = (match.group(1), int(match.group(2))) if match else ("", 0)
    return (action, category, number)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the cases in the order their shared installations require.

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
        package = config_mod.resolve_installer(
            settings, request.config.getoption("--installer"))
    except config_mod.ConfigError as exc:
        pytest.fail(str(exc), pytrace=False)
    _record_run_facts(package)
    return package


@pytest.fixture(scope="session")
def alternate_installer(settings, installer) -> config_mod.InstallerPackage:
    """Bundle B, and the proof that it really is a different build.

    Resolved lazily, like `installer`, so a machine that never runs the
    duplicate-install cases needs no second bundle configured at all.

    The SHA-256 comparison is the point of this fixture existing rather than
    being one line inside the cases. Burn's gate is on which BUNDLE is
    registered, so pointing both settings at the same build -- or at two copies
    of one file -- turns LCM-003 and LCM-004 into re-runs of LCM-002 against the
    maintenance page. They would fail, and the failure would read as a product
    defect. The paths are compared by CONTENT because two paths can hold
    identical bytes and the filenames do not distinguish builds at all.
    """
    try:
        alternate = config_mod.resolve_alternate_installer(settings)
    except config_mod.ConfigError as exc:
        pytest.fail(str(exc), pytrace=False)

    if alternate.sha256 == installer.sha256:
        pytest.fail(
            "installer.path and installer.alternate_path name the same BUILD "
            f"(sha256 {alternate.sha256[:16]}...):\n"
            f"  path           = {installer.path}\n"
            f"  alternate_path = {alternate.path}\n"
            "The duplicate-install cases need a bundle the machine does NOT "
            "have registered. Launched with the installed build, the product "
            "offers maintenance instead of refusing, which is LCM-002's "
            "subject rather than theirs.", pytrace=False)

    _note(f"  bundle B   : {alternate.describe()}")
    _RUN_FACTS["alternate_installer"] = {
        "path": str(alternate.path), "sha256": alternate.sha256,
        "size": alternate.size,
    }
    return alternate


@pytest.fixture(scope="session")
def run_dir(request) -> Path:
    """Where this run's artefacts go.

    Aligned with the --junitxml path run-tests.ps1 passes, so the JUnit XML, the
    installer logs and the `dump_json` artefacts land together.
    """
    xmlpath = getattr(request.config.option, "xmlpath", None)
    # Anchored to the repository, not to the current directory: `pytest` run
    # from anywhere else would otherwise scatter reports wherever it was called.
    directory = (Path(xmlpath).parent if xmlpath
                 else config_mod.REPO_ROOT / "reports" / time.strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="session")
def note() -> Callable[[str], None]:
    """Record a line that must appear even when the test passes.

    Session-scoped although it holds no per-test state -- it hands back one
    module-level function -- so that a fixture of ANY scope can request it. A
    function-scoped `note` cannot be used by the provisioning fixtures, and a
    provisioning fixture that cannot talk is three minutes of installing that a
    passing run leaves unaccounted for.
    """
    return _note


@pytest.fixture(scope="session", autouse=True)
def run_report(run_dir, request) -> dict[str, Any]:
    """Record what this run ran against, before and after it runs.

    Written twice on purpose: once at the start, so a session that dies part-way
    still leaves the facts behind, and once at the end with the facts the run
    learned on the way -- which bundle, under which account.
    """
    path = run_dir / "run.json"
    _RUN_FACTS.clear()
    _RUN_FACTS.update({
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        # No run-wide "install mode": each driver pins its own, and every
        # install below records the exact command line it ran. A single
        # top-level field could only repeat ONE of them while claiming to
        # describe the run.
        **preflight.describe(),
    })
    _write_json(path, _RUN_FACTS)

    yield _RUN_FACTS

    _RUN_FACTS["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
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
# Run facts
# --------------------------------------------------------------------------- #
def _record_run_facts(installer: config_mod.InstallerPackage) -> None:
    """Which binary, under which account. A result is only quotable with both.

    Recorded when the `installer` fixture resolves the bundle, so every case
    that runs the installer -- whatever category -- lands in run.json.
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


# --------------------------------------------------------------------------- #
# The product is not on this machine
# --------------------------------------------------------------------------- #
@pytest.fixture
def check_product_absent(note) -> Callable[..., None]:
    """Verify CUBRID for WSL is not on the machine after an uninstall, a
    cancelled install or a refused install.

    Checks the registry key, the WSL distribution, both Run values, the Apps
    & Features entry, the Tray process and binary, and the install directory.
    """
    def _check(wsl_name: str, *, after: str,
               tray_binary: Path | None = None) -> None:
        note(f"  absent       : registry={registry.exists()} "
             f"distro={distro.exists(wsl_name)} "
             f"starter={registry.starter_registered_for_startup()} "
             f"tray={registry.tray_registered_for_startup()} "
             f"arp={apps.is_listed()} running={tray.is_running()}")

        assert not registry.exists(), (
            rf"the registry key HKCU\{constants.PRODUCT_KEY} exists after "
            f"{after}")

        assert not distro.exists(wsl_name), (
            f"the WSL distribution {wsl_name!r} is registered after {after}")

        # The Starter is registered UNCONDITIONALLY by the installer, so it
        # must be gone whatever options the install used.
        assert not registry.starter_registered_for_startup(), (
            f"the Run value {constants.RUN_VALUE_STARTER} exists after "
            f"{after}")

        assert not registry.tray_registered_for_startup(), (
            f"the Run value {constants.RUN_VALUE_TRAY} exists after {after}")

        assert not apps.is_listed(), (
            f"listed in Apps & Features after {after}")

        assert not tray.is_running(), f"the Tray is running after {after}"

        if tray_binary is not None:
            assert not tray.binary_exists(tray_binary), (
                f"the Tray binary is still on disk at {tray_binary} after "
                f"{after}")

        # A leftover ext4.vhdx blocks every later install.
        leftovers = files.leftovers(wsl_name)
        assert not leftovers, (
            f"{len(leftovers)} artifact(s) on disk after {after}:\n"
            + "\n".join(f"      {item}" for item in leftovers))

    return _check
