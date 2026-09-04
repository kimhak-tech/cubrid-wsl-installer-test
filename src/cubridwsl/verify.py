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

from .state import MachineState, normalize_path


@dataclass(frozen=True)
class Check:
    """One checkable fact: what the options imply, and how to read what is there.

    `area` groups checks so a test can assert only the evidence it is about, and
    get a failure naming one area rather than all of them.
    """

    name: str
    area: str
    expected: Callable[[dict[str, Any]], Any]
    actual: Callable[[MachineState], Any]


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
          lambda o: True, lambda s: s.registry.installed),
    Check("registry.wsl_name", "identity",
          lambda o: str(o["CUB_DEFAULT_WSL_NAME"]),
          lambda s: s.registry.wsl_name),
    Check("registry.install_dir", "identity",
          _install_dir_for,
          lambda s: normalize_path(str(s.registry.install_dir))
                    if s.registry.install_dir else None),

    # --- the distribution ---------------------------------------------------
    Check("distro.present", "distro", lambda o: True,
          lambda s: s.cubrid.distro_present),
    Check("distro.version", "distro",
          lambda o: 2 if int(o["IS_WSL2_MODE"]) else 1,
          lambda s: s.cubrid.distro_version),

    # --- startup entries ----------------------------------------------------
    # The starter is registered UNCONDITIONALLY, so "no CUBRID entries under
    # Run" would fail against a correct build.
    Check("startup.starter", "startup", lambda o: True,
          lambda s: s.startup.starter_registered),
    Check("startup.tray_app", "startup",
          lambda o: bool(int(o["REG_TRAY_APP"])),
          lambda s: s.startup.tray_app_registered),

    # --- desktop shortcuts --------------------------------------------------
    Check("shortcuts.distro_link", "shortcuts",
          lambda o: bool(int(o["CREATE_SHORTCUT"])),
          lambda s: s.shortcuts.distro_link_exists),
    Check("shortcuts.tray_link", "shortcuts",
          lambda o: bool(int(o["CREATE_SHORTCUT"])),
          lambda s: s.shortcuts.tray_link_exists),

    # --- Apps & Features ----------------------------------------------------
    # The bundle entry. The MSI carries ARPSYSTEMCOMPONENT=1 and is hidden.
    Check("arp.present", "arp", lambda o: True, lambda s: s.arp.present),

    # --- payload ------------------------------------------------------------
    # demodb is created asynchronously: wait for it with state.wait_until()
    # before comparing, never from a bare snapshot taken as the installer exits.
    Check("cubrid.demodb", "payload",
          lambda o: bool(int(o["CREATE_DEMODB"])),
          lambda s: s.cubrid.demodb_present),
)

AREAS = tuple(dict.fromkeys(check.area for check in CHECKS))


@dataclass(frozen=True)
class Result:
    """One check evaluated against a real machine."""

    name: str
    area: str
    expected: Any
    actual: Any

    @property
    def matches(self) -> bool:
        return self.expected == self.actual

    def __str__(self) -> str:
        mark = "ok  " if self.matches else "FAIL"
        return (f"[{mark}] {self.name:<26} expected={self.expected!r:<22} "
                f"actual={self.actual!r}")


@dataclass
class Comparison:
    """Every check evaluated for one set of install options."""

    results: list[Result]

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.matches]

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
        failures = [r for r in wanted if not r.matches]
        return "\n".join(str(r) for r in failures)

    def report(self) -> str:
        return "\n".join(str(r) for r in self.results)

    def as_list(self) -> list[dict[str, Any]]:
        return [r.__dict__ for r in self.results]


def compare(options: dict[str, Any], state: MachineState) -> Comparison:
    """Diff the machine against what the install options imply.

    Neither side is allowed to raise: a check that cannot be evaluated names
    itself in the report and lets every other check report normally.
    """
    results: list[Result] = []
    for check in CHECKS:
        try:
            actual = check.actual(state)
        except Exception as exc:
            actual = f"<error: {type(exc).__name__}: {exc}>"
        try:
            expected = check.expected(options)
        except Exception as exc:
            expected = f"<error: {type(exc).__name__}: {exc}>"
        results.append(Result(check.name, check.area, expected, actual))
    return Comparison(results)
