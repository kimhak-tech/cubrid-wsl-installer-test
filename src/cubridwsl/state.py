"""THE verification layer's eyes: one reading of what is actually true.

Both drivers read the machine through this module and no other. A second copy
of "is demodb present" drifts from the first and doubles maintenance for no
coverage.

Three properties this module must keep:

* It REPORTS, it does not INTERPRET. Whether the observed state is *correct*
  for a given set of options belongs to verify.py. Here the only question is
  what is true.
* It DEGRADES rather than raises. If WSL is unresponsive the snapshot comes back
  with that field's error recorded and every other field intact, so one hiccup
  does not mask the assertion a test actually cared about.
* Some product state is EVENTUALLY CONSISTENT. demodb creation is dispatched
  asynchronously and can finish after the installer has exited, so use
  `wait_until()` rather than sleeping in a test.

Field shapes come from a real installed machine, not from reading the C++:

    Installed / ImageInstalled / TrayAppInstalled /
    StarterInstalled / DocsInstalled      REG_DWORD, so int 1 -- never "1"
    InstallDir                            carries a TRAILING BACKSLASH
    ImageFile / TrayAppFile /
    TrayAppLinkFile                       bare filenames, relative to InstallDir
"""
from __future__ import annotations

import ntpath
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import constants, distro as distro_mod


def normalize_path(value: str | None) -> str | None:
    """Compare Windows paths without tripping over case or a trailing separator.

    `InstallDir` is stored with one, so a direct string compare against an
    expected path fails for a perfectly correct install. ntpath (not os.path)
    keeps this deterministic and testable off Windows.
    """
    if not value:
        return value
    return ntpath.normcase(ntpath.normpath(str(value)))


def read_key(hive_name: str, path: str) -> dict[str, Any] | None:
    """Every value under one registry key, or None if the key is absent."""
    import winreg

    hive = (winreg.HKEY_CURRENT_USER if hive_name == "HKCU"
            else winreg.HKEY_LOCAL_MACHINE)
    try:
        with winreg.OpenKey(hive, path) as key:
            out: dict[str, Any] = {}
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                out[name] = value
                index += 1
            return out
    except OSError:                       # includes FileNotFoundError
        return None


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegistryState:
    """The product's own key under HKCU."""

    present: bool
    values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def _flag(self, name: str) -> bool:
        return self.values.get(name) == 1            # REG_DWORD, so int

    @property
    def installed(self) -> bool:
        return self._flag("Installed")

    @property
    def wsl_name(self) -> str | None:
        return self.values.get("WslName")

    @property
    def install_dir(self) -> Path | None:
        raw = self.values.get("InstallDir")
        return Path(str(raw)) if raw else None

    @property
    def desktop_folder(self) -> Path | None:
        raw = self.values.get("UsersDesktopFolder")
        return Path(str(raw)) if raw else None


def read_registry() -> RegistryState:
    """Read the product key. Absent means not installed FOR THIS ACCOUNT."""
    try:
        values = read_key("HKCU", constants.PRODUCT_KEY)
    except Exception as exc:
        return RegistryState(present=False, error=f"{type(exc).__name__}: {exc}")
    if values is None:
        return RegistryState(present=False)
    return RegistryState(present=True, values=values)


# --------------------------------------------------------------------------- #
# Startup entries
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StartupState:
    """Two Run entries, and they behave differently.

    `CUBRID_WSL_Starter` is registered unconditionally; `CUBRID_WSL_TrayApp`
    follows the auto-start option.
    """

    tray_app: str | None
    starter: str | None

    @property
    def tray_app_registered(self) -> bool:
        return self.tray_app is not None

    @property
    def starter_registered(self) -> bool:
        return self.starter is not None


def read_startup() -> StartupState:
    """An absent value means not registered, not an error."""
    values = read_key("HKCU", constants.RUN_KEY) or {}
    return StartupState(values.get(constants.RUN_VALUE_TRAY),
                        values.get(constants.RUN_VALUE_STARTER))


# --------------------------------------------------------------------------- #
# Apps & Features
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArpState:
    """The bundle's Apps & Features entry. The MSI's is hidden by design."""

    present: bool
    display_name: str | None = None
    display_version: str | None = None
    uninstall_string: str | None = None
    location: str | None = None
    count: int = 0


def read_arp(display_name: str = constants.ARP_DISPLAY_NAME) -> ArpState:
    """Find the visible uninstall entry for the bundle.

    `count` is reported so a duplicate left behind by a failed uninstall is
    visible rather than silently taking the first match.
    """
    import winreg

    matches: list[ArpState] = []
    for hive_name, base in constants.UNINSTALL_KEYS:
        hive = (winreg.HKEY_CURRENT_USER if hive_name == "HKCU"
                else winreg.HKEY_LOCAL_MACHINE)
        try:
            with winreg.OpenKey(hive, base) as key:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    values = read_key(hive_name, f"{base}\\{sub}") or {}
                    if values.get("DisplayName") != display_name:
                        continue
                    if values.get("SystemComponent") == 1:   # hidden by design
                        continue
                    matches.append(ArpState(
                        present=True,
                        display_name=values.get("DisplayName"),
                        display_version=values.get("DisplayVersion"),
                        uninstall_string=values.get("UninstallString"),
                        location=f"{hive_name}\\{base}\\{sub}"))
        except OSError:
            continue

    if not matches:
        return ArpState(present=False)
    first = matches[0]
    return ArpState(present=True, display_name=first.display_name,
                    display_version=first.display_version,
                    uninstall_string=first.uninstall_string,
                    location=first.location, count=len(matches))


# --------------------------------------------------------------------------- #
# Desktop shortcuts
#
# Existence only. Resolving a .lnk target needs COM through PowerShell, and no
# case in this starter is about shortcut targets -- INS-016 is, and when someone
# implements it the working code is in cubrid-wsl-installer-test-old.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ShortcutState:
    """Where the two desktop shortcuts should be, and whether they are there."""

    distro_link: Path | None
    tray_link: Path | None
    distro_link_exists: bool
    tray_link_exists: bool


def read_shortcuts(registry: RegistryState) -> ShortcutState:
    """Derive both paths from the registry -- never from a hard-coded name, so a
    custom-name install still resolves."""
    desktop = registry.desktop_folder
    wsl_name = registry.wsl_name
    tray_name = registry.values.get("TrayAppLinkFile") or "cubrid_tray_app.lnk"
    distro_link = (desktop / f"{wsl_name}.lnk") if (desktop and wsl_name) else None
    tray_link = (desktop / str(tray_name)) if desktop else None
    return ShortcutState(
        distro_link=distro_link, tray_link=tray_link,
        distro_link_exists=bool(distro_link and distro_link.is_file()),
        tray_link_exists=bool(tray_link and tray_link.is_file()))


# --------------------------------------------------------------------------- #
# CUBRID, from inside the distribution
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CubridState:
    """What is true inside the distribution."""

    distro_present: bool
    distro_running: bool
    distro_version: int | None
    service_running: bool | None
    cubrid_version: str | None
    demodb_present: bool | None
    databases: tuple[str, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)


def read_cubrid(settings: dict[str, Any], name: str | None) -> CubridState:
    """Read CUBRID inside the named distribution.

    `name` comes from the registry -- what was ACTUALLY installed. There is
    deliberately no configured fallback: a name in settings could point this at
    a different distribution than the one the product created.

    Records a per-command error instead of raising, so one unresponsive command
    does not hide everything else that was readable.
    """
    cfg = settings.get("distro", {})
    user = cfg.get("user") or None
    env_setup = cfg.get("env_setup", "")
    timeout = settings.get("timeouts", {}).get("wsl_command_seconds", 60)
    errors: dict[str, str] = {}

    try:
        entry = distro_mod.find(name or "")
    except Exception as exc:
        return CubridState(False, False, None, None, None, None,
                           errors={"distro": f"{type(exc).__name__}: {exc}"})
    if entry is None:
        return CubridState(False, False, None, None, None, None)

    def _run(label: str, command: str) -> str | None:
        try:
            result = distro_mod.run(entry.name, command, user=user,
                                    env_setup=env_setup, timeout=timeout)
            if not result.ok:
                errors[label] = f"rc={result.returncode} {result.stderr[:200]}"
            return result.stdout
        except Exception as exc:
            errors[label] = f"{type(exc).__name__}: {exc}"
            return None

    status = _run("service_status", "cubrid service status")
    service_running: bool | None = None
    if status is not None:
        if constants.SERVICE_RUNNING_MARKER in status:
            service_running = True
        elif constants.SERVICE_STOPPED_MARKER in status:
            service_running = False

    version = _run("cubrid_rel", "cubrid_rel")

    databases: tuple[str, ...] = ()
    demodb_present: bool | None = None
    listing = _run("databases",
                   'cat "$CUBRID_DATABASES/databases.txt" 2>/dev/null '
                   '|| cat "$CUBRID/databases/databases.txt" 2>/dev/null')
    if listing is not None:
        databases = tuple(line.split()[0] for line in listing.splitlines()
                          if line.strip() and not line.strip().startswith("#"))
        demodb_present = "demodb" in databases

    return CubridState(distro_present=True,
                       distro_running=entry.state.casefold() == "running",
                       distro_version=entry.version,
                       service_running=service_running,
                       cubrid_version=(version or "").strip() or None,
                       demodb_present=demodb_present,
                       databases=databases, errors=errors)


# --------------------------------------------------------------------------- #
# The whole machine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MachineState:
    """One reading of the whole machine."""

    registry: RegistryState
    startup: StartupState
    arp: ArpState
    shortcuts: ShortcutState
    cubrid: CubridState

    @property
    def installed(self) -> bool:
        return self.registry.present and self.registry.installed

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry": {"present": self.registry.present,
                         "values": self.registry.values,
                         "error": self.registry.error},
            "startup": {"tray_app": self.startup.tray_app,
                        "starter": self.startup.starter},
            "arp": self.arp.__dict__,
            "shortcuts": {"distro_link": str(self.shortcuts.distro_link),
                          "distro_link_exists": self.shortcuts.distro_link_exists,
                          "tray_link": str(self.shortcuts.tray_link),
                          "tray_link_exists": self.shortcuts.tray_link_exists},
            "cubrid": {**self.cubrid.__dict__,
                       "databases": list(self.cubrid.databases)},
        }


def snapshot(settings: dict[str, Any]) -> MachineState:
    """One reading of the whole machine. Never raises."""
    registry = read_registry()
    return MachineState(registry=registry,
                        startup=read_startup(),
                        arp=read_arp(),
                        shortcuts=read_shortcuts(registry),
                        cubrid=read_cubrid(settings, registry.wsl_name))


def wait_until(predicate: Callable[[MachineState], bool],
               settings: dict[str, Any], *, timeout: float = 120,
               interval: float = 3.0,
               description: str = "condition") -> MachineState:
    """Poll until a predicate holds, then return the snapshot that satisfied it.

    Needed because the installer dispatches demodb creation asynchronously and
    can exit before the database exists. Tests express the condition; they never
    sleep.
    """
    deadline = time.time() + timeout
    latest = snapshot(settings)
    while True:
        if predicate(latest):
            return latest
        if time.time() >= deadline:
            raise TimeoutError(f"{description} was not satisfied within "
                               f"{timeout:.0f}s. Last state: {latest.as_dict()}")
        time.sleep(interval)
        latest = snapshot(settings)
