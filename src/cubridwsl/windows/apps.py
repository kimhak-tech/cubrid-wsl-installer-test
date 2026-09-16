"""Windows Apps & Features: the entry for the product, and its Uninstall button.

What WINDOWS wrote about the product, as opposed to what the product wrote
about itself (`windows/registry.py`). Only the BUNDLE appears here -- the MSI
carries ARPSYSTEMCOMPONENT=1, which hides it.

This module owns the whole Apps & Features surface, reading AND acting: the
Uninstall button is part of what Apps & Features IS, so `uninstall_cubrid_wsl`
belongs here rather than in a general-purpose uninstall module. The route you
call is the route you are testing:

    apps.uninstall_cubrid_wsl()      the Apps & Features button    LCM-001
    wizard.uninstall_cubrid_wsl()    re-run the bundle, Uninstall  LCM-002
    silent.uninstall_cubrid_wsl()    the bundle's own switch       set-up/clean-up

`wait_until_removed` below is shared by all three, so they cannot drift apart
about what "removed" means.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import constants
from ..drivers import silent
from ..wsl import distro
from . import registry, tray

Note = Callable[[str], None]

# How long to keep asking whether the product has finished coming apart after
# the uninstaller has reported that it is done. The Tray exits on a signal of
# its own and the distribution is unregistered by a child process, so a reading
# taken the instant the uninstaller returns can catch either one mid-exit and
# call a correct teardown a failure. Short on purpose: past this, still-there
# is the finding.
REMOVAL_SETTLE_SECONDS = 60.0


@dataclass(frozen=True)
class Entry:
    """The Apps & Features entry, as Windows shows it."""

    present: bool
    display_name: str | None = None
    display_version: str | None = None
    publisher: str | None = None
    uninstall_string: str | None = None
    location: str | None = None
    # How many entries matched. A duplicate left behind by a failed uninstall
    # is worth seeing rather than silently taking the first one.
    count: int = 0
    error: str | None = None

    def describe(self) -> str:
        if not self.present:
            return "no Apps & Features entry"
        return (f"{self.display_name!r} {self.display_version} by "
                f"{self.publisher!r} at {self.location}"
                + (f" ({self.count} entries!)" if self.count > 1 else ""))


def read() -> Entry:
    """The product's Apps & Features entry. Never raises."""
    try:
        return _read(constants.ARP_DISPLAY_NAME)
    except Exception as exc:
        return Entry(present=False, error=f"{type(exc).__name__}: {exc}")


def _read(display_name: str) -> Entry:
    import winreg

    matches: list[Entry] = []
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
                    entry_values = registry.read_key(
                        hive_name, f"{base}\\{sub}") or {}
                    if entry_values.get("DisplayName") != display_name:
                        continue
                    if entry_values.get("SystemComponent") == 1:  # hidden by design
                        continue
                    matches.append(Entry(
                        present=True,
                        display_name=entry_values.get("DisplayName"),
                        display_version=entry_values.get("DisplayVersion"),
                        publisher=entry_values.get("Publisher"),
                        uninstall_string=entry_values.get("UninstallString"),
                        location=f"{hive_name}\\{base}\\{sub}"))
        except OSError:
            continue

    if not matches:
        return Entry(present=False)
    first = matches[0]
    return Entry(present=True, display_name=first.display_name,
                 display_version=first.display_version,
                 publisher=first.publisher,
                 uninstall_string=first.uninstall_string,
                 location=first.location, count=len(matches))


def is_listed() -> bool:
    """Is CUBRID For WSL listed in Windows Apps & Features / Installed Apps?"""
    return read().present


def uninstall_command() -> str | None:
    """The command Windows runs for the Uninstall button, or None.

    Read from the machine rather than composed here: a command this framework
    built would be exercising a removal route Windows does not use.
    """
    return read().uninstall_string


def uninstall_cubrid_wsl(settings: dict[str, Any], log_path: Path, *,
                         note: Note | None = None) -> silent.RunResult:
    """Remove the product the way the Apps & Features Uninstall button does.

    Runs the machine's own `UninstallString`, then WAITS until the removal has
    landed. The wait is inside rather than left to the caller because an
    uninstall that returns while its child processes are still exiting makes
    every verification after it a race -- and a case that flakes is worse than
    one that fails.

    /quiet is added, whatever mode the install used. Under /passive Burn draws
    a progress window that can outlive its own process, and the wizard driver
    then refuses to start because it cannot tell that leftover apart from a
    window it is about to open. Nothing is lost: /passive versus /quiet decides
    whether ActionEnvironmentCheck runs, and that sequence is INSTALL-only.

    It does NOT assert. Whether the machine is clean afterwards is the case's
    question, and every check it needs is a call away in this package.
    """
    say: Note = note or (lambda _text: None)

    command = uninstall_command()
    if not command:
        raise RuntimeError(
            "Apps & Features carries no UninstallString for CUBRID For WSL, so "
            "there is no Uninstall button to press. Windows would show one "
            "that cannot work.")

    # Captured before the command runs -- it removes the key that names the
    # distribution, and the settle wait below has to know which one to watch.
    name = registry.wsl_name()
    say(f"  apps       : running {command!r}")

    result = silent.uninstall_with_command(
        command, log_path, timeout=settings["timeouts"]["uninstall_seconds"],
        mode="quiet")
    say(f"  apps       : {result.describe()}")

    wait_until_removed(name, note=say)
    return result


def wait_until_removed(wsl_name: str | None, *,
                       timeout: float = REMOVAL_SETTLE_SECONDS,
                       note: Note | None = None) -> list[str]:
    """Wait for the product to finish coming apart. Returns what is STILL there.

    Waiting on a TRANSITION the product has just said it made -- not on absence
    in general. This framework never waits for something to STAY absent,
    because "still not there after five minutes" is evidence of patience rather
    than of correctness. Here the uninstaller has already reported that it
    finished, and this is the seconds its last child processes need.

    Shared by all three removal routes -- this module's, `drivers.wizard`'s and
    `drivers.silent`'s -- rather than written once each. Three copies of a
    settle loop drift silently: each keeps working while they wait for
    different things.

    It lives HERE, in the module that owns the Apps & Features route, because
    that is where the only other whole-machine question currently is. If a
    second one appears -- a before/after comparison, say -- both should move to
    a `machine.py` of their own rather than accumulate in this file.

    Never raises. Anything still present when the budget runs out is the
    caller's finding to report, not this function's to decide.
    """
    say: Note = note or (lambda _text: None)
    deadline = time.monotonic() + timeout
    while True:
        remaining = [
            label for label, still_there in (
                ("the registry key", registry.exists()),
                ("the distribution", distro.exists(wsl_name)),
                ("the Tray process", tray.is_running()),
                ("the Apps & Features entry", is_listed()),
            ) if still_there]
        if not remaining:
            say("  removal    : nothing left on the machine")
            return []
        if time.monotonic() >= deadline:
            say(f"  removal    : still present after {timeout:.0f}s: "
                f"{', '.join(remaining)}")
            return remaining
        time.sleep(5)
