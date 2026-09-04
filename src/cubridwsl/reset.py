"""Bringing the machine to a known starting state.

This is the one module allowed to change the machine outside a driver's own
install and uninstall, and it works inside a narrow, stated boundary:

* it removes the PRODUCT, through the product's own uninstaller, having first
  stopped the product's own Tray so it is not holding files open;
* it never touches a protected distribution and never issues `wsl --shutdown`;
* when the machine is still dirty afterwards it REFUSES, and names the exact
  remediation, instead of escalating on its own.

That last rule is the important one. `ActionUninstallWsl` is declared
`Return="ignore"`, so a zero exit code is not evidence of removal -- and a
leftover `ext4.vhdx` hard-blocks every future install, because
`InstallWslAndCubrid` aborts the moment it finds one. A framework that quietly
deletes a virtual disk to keep its own suite green is a framework that will one
day delete the wrong one. So: detect, report, stop.

The uninstaller used is the one Apps & Features would run -- the cached bundle --
and only falls back to the configured build when there is no ARP entry.
Installer filenames are not unique in this product, so "the installer I was
pointed at" and "the installer that is actually installed" are not reliably the
same binary.
"""
from __future__ import annotations

import ntpath
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import constants, config as config_mod, preflight, state as state_mod
from .drivers import silent

Note = Callable[[str], None]


class DirtyMachineError(RuntimeError):
    """The machine could not be brought to a clean state, and going further
    would mean deleting things this framework must not delete."""


@dataclass
class CleanResult:
    """What ensure_clean did."""

    already_clean: bool
    uninstall: silent.RunResult | None = None
    removed: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.already_clean:
            return "machine was already clean; no uninstaller run"
        return self.uninstall.describe() if self.uninstall else "cleaned"


def _candidate_install_dirs(before: state_mod.MachineState,
                            expected_name: str | None) -> list[Path]:
    """Where a leftover install directory could be.

    Read BEFORE the uninstall, because the registry that names it is one of the
    things the uninstall removes. `expected_name` is the distribution this run
    is about to create, so residue from an install this session never saw is
    still found.
    """
    out: list[Path] = []
    if before.registry.install_dir:
        out.append(before.registry.install_dir)
    local = os.environ.get("LOCALAPPDATA", "")
    for name in {before.registry.wsl_name, expected_name or ""}:
        if name and local:
            out.append(Path(ntpath.join(local, str(name))))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(state_mod.normalize_path(str(path)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def find_residue(state: state_mod.MachineState,
                 install_dirs: list[Path]) -> list[str]:
    """Everything still present that must not be, in plain language."""
    found: list[str] = []
    if state.registry.present:
        found.append(rf"registry key HKCU\{constants.PRODUCT_KEY} still exists")
    if state.cubrid.distro_present:
        found.append("the WSL distribution is still registered (`wsl -l -v` lists it)")
    if state.startup.tray_app_registered:
        found.append(f"Run value {constants.RUN_VALUE_TRAY} still exists")
    if state.startup.starter_registered:
        found.append(f"Run value {constants.RUN_VALUE_STARTER} still exists")
    if state.arp.present:
        found.append(f"the Apps & Features entry still exists ({state.arp.location})")
    for path in install_dirs:
        vhdx = path / "ext4.vhdx"
        if vhdx.is_file():
            found.append(f"{vhdx} still exists -- this HARD-BLOCKS reinstall; "
                         "InstallWslAndCubrid aborts when it finds an ext4.vhdx")
        elif path.is_dir() and any(path.iterdir()):
            found.append(f"{path} still exists and is not empty")
    return found


def stop_tray(note: Note | None = None) -> bool:
    """Stop the product's Tray before uninstalling it.

    The default install leaves the Tray running (START_TRAY_APP=1), so by the
    second run of any suite there is one holding the install directory open.
    A process with a handle on that directory is the likeliest reason for the
    leftover-folder residue that `ensure_clean` refuses on -- which would read
    as a product defect rather than as our own doing.
    """
    say: Note = note or (lambda _text: None)
    try:
        result = subprocess.run(["taskkill", "/F", "/IM", constants.TRAY_EXE],
                                capture_output=True, timeout=30)
    except Exception as exc:
        say(f"  reset      : could not stop the Tray ({type(exc).__name__}: {exc})")
        return False
    # taskkill returns 128 when no such process is running, which is the normal
    # case and not a failure.
    if result.returncode == 0:
        say(f"  reset      : stopped a running {constants.TRAY_EXE}")
        return True
    return False


def ensure_clean(settings: dict[str, Any],
                 installer: config_mod.InstallerPackage,
                 log_path: Path, *,
                 mode: str = "passive",
                 expected_name: str | None = None,
                 note: Note | None = None) -> CleanResult:
    """Leave the machine with no CUBRID For WSL on it, or raise saying why not.

    Idempotent: on an already-clean machine it runs no uninstaller at all.
    """
    say: Note = note or (lambda _text: None)
    before = state_mod.snapshot(settings, include_guest=False)
    install_dirs = _candidate_install_dirs(before, expected_name)

    residue = find_residue(before, install_dirs)
    if not residue:
        say("  reset      : machine already clean")
        return CleanResult(already_clean=True)

    preflight.require_elevation()
    say(f"  reset      : {len(residue)} item(s) to remove; uninstalling")
    stop_tray(say)

    timeout = settings["timeouts"]["uninstall_seconds"]
    if before.arp.present and before.arp.uninstall_string:
        result = silent.uninstall_with_command(before.arp.uninstall_string,
                                               log_path, timeout=timeout)
    else:
        result = silent.uninstall(installer, log_path, timeout=timeout, mode=mode)
    say(f"  reset      : {result.describe()}")

    # Never assert on the uninstall exit code -- assert the machine. Removal is
    # not instantaneous, so poll rather than snapshot once.
    try:
        # include_guest=False: find_residue only asks whether the distribution
        # is still REGISTERED, which `wsl -l -v` answers in one call. Running
        # commands inside a distro that is mid-unregister can hang to their own
        # timeout, turning a poll loop into minutes per iteration.
        after = state_mod.wait_until(
            lambda s: not find_residue(s, install_dirs), settings,
            timeout=timeout, description="product fully removed",
            include_guest=False)
    except TimeoutError:
        after = state_mod.snapshot(settings, include_guest=False)

    remaining = find_residue(after, install_dirs)
    if remaining:
        raise DirtyMachineError(
            "the machine is not clean after uninstalling, so no install can be "
            "trusted from here.\n"
            f"The uninstaller reported: {result.describe()}\n"
            "(that is not evidence of anything -- ActionUninstallWsl is declared "
            'Return="ignore")\n\nStill present:\n'
            + "\n".join(f"  - {item}" for item in remaining)
            + "\n\nThis framework will not remove these for you. From an "
              "elevated shell, and only after checking each one:\n"
            + _remediation(after, install_dirs))

    return CleanResult(already_clean=False, uninstall=result, removed=residue)


def _remediation(state: state_mod.MachineState, install_dirs: list[Path]) -> str:
    """The exact commands, so nobody has to reconstruct them under pressure."""
    name = (state.registry.wsl_name if state.cubrid.distro_present else "") or "<WslName>"
    lines: list[str] = []
    if state.cubrid.distro_present:
        lines.append(f'  wsl --unregister "{name}"   '
                     "# check `wsl -l -v` first; NEVER a protected distro")
    for path in install_dirs:
        if path.is_dir():
            lines.append(f'  cmd /c rmdir /s /q "{path}"')
    if state.registry.present:
        lines.append(rf'  reg delete "HKCU\{constants.PRODUCT_KEY}" /f')
    return "\n".join(lines) or "  (nothing scriptable -- inspect by hand)"
