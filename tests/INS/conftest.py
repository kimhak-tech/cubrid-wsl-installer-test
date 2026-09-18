"""Fixtures used only by the Installer Orchestration scenarios."""
from __future__ import annotations

import ntpath
import os
from typing import Any, Callable

import pytest

from cubridwsl import constants
from cubridwsl.windows import apps, registry, shortcuts, tray
from cubridwsl.wsl import cubrid, distro


@pytest.fixture
def check_post_install(installer, settings, note) -> Callable[..., None]:
    """INS-001's post-install assertion set, for any install that succeeded.

    Every INS install case ends with it. The workbook applies INS-001's set to
    INS-002, INS-003 and INS-004 too, so one copy keeps the four from drifting.

    `options` is the FULL option set the machine was installed with --
    `{**constants.INSTALL_OPTIONS, **overrides}` -- and each option-dependent
    expectation follows it, so an inverted option moves its own assertion.
    `install_dir` is the directory a wizard case chose; when None, the
    directory the product derives from the WSL name is expected.

    Only observations: nothing here starts, stops, connects or creates.
    """
    def _check(options: dict[str, Any], *,
               install_dir: str | None = None) -> None:
        expected_wsl_name = str(options[constants.OPTION_WSL_NAME])
        expected_wsl_version = (
            2 if int(options[constants.OPTION_WSL2_MODE]) else 1)
        expected_demodb = bool(int(options["CREATE_DEMODB"]))
        expected_tray_startup = bool(int(options["REG_TRAY_APP"]))
        expected_tray_running = bool(int(options["START_TRAY_APP"]))
        expected_shortcuts = bool(int(options["CREATE_SHORTCUT"]))
        expected_dir = install_dir or ntpath.join(
            os.environ.get("LOCALAPPDATA", ""), expected_wsl_name)

        # -- the product's registry record ------------------------------ #
        assert registry.exists(), (
            rf"the registry key HKCU\{constants.PRODUCT_KEY} is missing")

        assert registry.wsl_name() == expected_wsl_name, (
            f"the registry records WslName {registry.wsl_name()!r}, "
            f"not {expected_wsl_name!r}")

        recorded_dir = registry.install_dir()
        assert (registry.normalize_path(str(recorded_dir))
                == registry.normalize_path(expected_dir)), (
            f"the registry records InstallDir {recorded_dir}, "
            f"not {expected_dir}")

        assert recorded_dir.is_dir(), (
            f"InstallDir {recorded_dir} is not on disk")

        # -- the distribution ------------------------------------------- #
        entry = distro.find(expected_wsl_name)
        note(f"  post-install : wsl -l -v -> {entry}")

        assert entry, f"`wsl -l -v` does not list {expected_wsl_name!r}"

        assert entry.version == expected_wsl_version, (
            f"{expected_wsl_name!r} is registered at WSL {entry.version}, "
            f"not WSL {expected_wsl_version}")

        # -- the environment a fresh login shell gets ------------------- #
        # First among the CUBRID checks: a CRLF ~/.cubrid.sh breaks the
        # version, the service and demodb at once, and this names the cause.
        environment = cubrid.login_environment(expected_wsl_name, settings)
        note(f"  post-install : login environment -> {environment}")

        assert not any("\r" in value for value in environment.values()), (
            "the login environment carries carriage returns, so "
            f"~/.cubrid.sh has CRLF line endings: {environment}")

        assert environment.get("CUBRID") == constants.CUBRID_HOME, (
            f"$CUBRID is {environment.get('CUBRID')!r}, "
            f"not {constants.CUBRID_HOME!r}")

        databases_dir = environment.get("CUBRID_DATABASES")
        assert databases_dir == constants.CUBRID_DATABASES, (
            f"$CUBRID_DATABASES is {databases_dir!r}, "
            f"not {constants.CUBRID_DATABASES!r}")

        cubrid_bin = f"{constants.CUBRID_HOME}/bin"
        assert cubrid_bin in environment.get("PATH", "").split(":"), (
            f"$PATH does not contain {cubrid_bin}: "
            f"{environment.get('PATH')!r}")

        # -- CUBRID itself ---------------------------------------------- #
        version = cubrid.version(expected_wsl_name, settings)
        note(f"  post-install : cubrid_rel -> {version!r} "
             f"(bundle declares {installer.cubrid_version!r})")

        assert version and installer.cubrid_version in version, (
            f"the bundle declares CUBRID {installer.cubrid_version} but "
            f"`cubrid_rel` reports {version!r}")

        status = cubrid.service_status(expected_wsl_name, settings)
        note(f"  post-install : service -> {status.describe()}")

        assert cubrid.is_service_started(expected_wsl_name, settings), (
            f"the CUBRID master is not running:\n{status.raw}")

        assert cubrid.is_broker_started(expected_wsl_name, settings), (
            "the broker is not running with its default brokers:\n"
            f"{status.raw}")

        assert cubrid.is_manager_started(expected_wsl_name, settings), (
            f"the manager is not running:\n{status.raw}")

        has_demodb = cubrid.is_database_exists(
            expected_wsl_name, constants.DEMODB_NAME, settings)
        assert has_demodb == expected_demodb, (
            f"databases.txt {'lists' if has_demodb else 'does not list'} "
            f"{constants.DEMODB_NAME}, but "
            f"CREATE_DEMODB={int(expected_demodb)}: "
            f"{list(cubrid.databases(expected_wsl_name, settings))}")

        # -- the Tray and startup --------------------------------------- #
        tray_running = tray.is_running()
        assert tray_running == expected_tray_running, (
            f"the Tray is {'' if tray_running else 'not '}running, but "
            f"START_TRAY_APP={int(expected_tray_running)}")

        # Only this value follows REG_TRAY_APP. CUBRID_WSL_Starter is written
        # unconditionally, so it says nothing about the option.
        registered = registry.tray_registered_for_startup()
        assert registered == expected_tray_startup, (
            f"the Run value {constants.RUN_VALUE_TRAY} is "
            f"{'present' if registered else 'absent'}, but "
            f"REG_TRAY_APP={int(expected_tray_startup)}")

        # -- desktop shortcuts ------------------------------------------ #
        # One toggle controls both, so both follow CREATE_SHORTCUT.
        state = "is missing" if expected_shortcuts else "exists"
        distro_link = shortcuts.distro_shortcut_exists(expected_wsl_name)
        assert distro_link == expected_shortcuts, (
            f"{shortcuts.distro_shortcut_path(expected_wsl_name)} {state}, "
            f"but CREATE_SHORTCUT={int(expected_shortcuts)}")

        assert shortcuts.tray_shortcut_exists() == expected_shortcuts, (
            f"{shortcuts.tray_shortcut_path()} {state}, but "
            f"CREATE_SHORTCUT={int(expected_shortcuts)}")

        # -- Apps & Features -------------------------------------------- #
        listed = apps.read()
        note(f"  post-install : Apps & Features -> {listed.describe()}")

        assert listed.present, (
            "CUBRID For WSL is not listed in Apps & Features")

        assert listed.publisher == constants.ARP_PUBLISHER, (
            f"Apps & Features names publisher {listed.publisher!r}, "
            f"not {constants.ARP_PUBLISHER!r}")

        # Burn writes a four-part version, so the bundle's is a prefix.
        declared = installer.installer_version
        assert (listed.display_version or "").startswith(declared), (
            f"Apps & Features reports version {listed.display_version!r}, "
            f"but the bundle under test declares {declared!r}")

    return _check
