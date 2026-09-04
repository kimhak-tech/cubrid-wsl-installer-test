"""Unattended installer driver.

The command line is the product's own, confirmed against wix_src/bundle.wxs:

    <installer>.exe /passive|/quiet /norestart /log <path> [KEY=value ...]
    <installer>.exe /passive|/quiet /norestart /uninstall /log <path>

The overridable variables and their defaults are in constants.INSTALL_OPTIONS.

Note `/log` is not optional: the bundle declares <Log Disable="yes"/>, so
without it there is no bundle log at all and a failure has no diagnosis.

This module reports what happened; it never judges it. In particular it never
treats a zero exit code as proof of success, because the product's own WSL
removal step is declared to ignore its failures.
"""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as config_mod, preflight

# 0 = success, 3010 = success but a reboot is pending. Both are installs that
# happened; anything else is not.
OK_EXIT_CODES = (0, 3010)
REBOOT_REQUIRED = 3010


class InstallerError(RuntimeError):
    """The installer could not be driven -- not the same as an install that ran
    and failed."""


@dataclass
class RunResult:
    """What one installer invocation did."""

    action: str
    command: list[str]
    returncode: int | None
    duration_seconds: float
    log_path: Path
    timed_out: bool = False
    logs: list[Path] = field(default_factory=list)
    # The bundle writes its detail to /log, but it can also fail BEFORE the log
    # exists (a bad command line, a missing file). Without this, "exit=1603" is
    # the entire diagnosis.
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode in OK_EXIT_CODES

    @property
    def reboot_required(self) -> bool:
        return self.returncode == REBOOT_REQUIRED

    def describe(self) -> str:
        if self.timed_out:
            return f"{self.action} TIMED OUT after {self.duration_seconds:.0f}s"
        if self.returncode is None:
            return (f"{self.action} produced no exit code after "
                    f"{self.duration_seconds:.0f}s")
        line = (f"{self.action} exit={self.returncode} "
                f"(0x{self.returncode & 0xFFFFFFFF:08X}) in "
                f"{self.duration_seconds:.0f}s"
                + ("  [reboot pending]" if self.reboot_required else ""))
        if not self.ok and self.stderr:
            line += f"  stderr: {self.stderr.splitlines()[0][:200]}"
        return line

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "command": self.command,
                "returncode": self.returncode,
                "duration_seconds": round(self.duration_seconds, 1),
                "ok": self.ok, "reboot_required": self.reboot_required,
                "timed_out": self.timed_out, "log_path": str(self.log_path),
                "logs": [str(p) for p in self.logs],
                "stdout": self.stdout[:2000], "stderr": self.stderr[:2000]}


def as_properties(options: dict[str, Any]) -> list[str]:
    """Install options as Burn variable overrides, in a stable order."""
    return [f"{key}={value}" for key, value in sorted(options.items())]


def _log_paths(log_path: Path) -> list[Path]:
    """Burn writes <path> plus <path>_<PackageId>.log beside it for the MSI."""
    if not log_path.parent.is_dir():
        return []
    return sorted(p for p in log_path.parent.iterdir()
                  if p.is_file() and p.name.startswith(log_path.stem))


def _text(raw: bytes | None) -> str:
    return raw.decode("utf-8", errors="replace").strip() if raw else ""


def _run(action: str, command: list[str], log_path: Path, timeout: int) -> RunResult:
    """Run an installer command under a timeout, capturing what is needed to
    diagnose it afterwards."""
    preflight.require_elevation()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    try:
        completed = subprocess.run(command, timeout=timeout, capture_output=True)
        returncode: int | None = completed.returncode
        stdout, stderr = _text(completed.stdout), _text(completed.stderr)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode, timed_out = None, True
        stdout, stderr = _text(exc.stdout), _text(exc.stderr)
    elapsed = time.monotonic() - started

    return RunResult(action=action, command=command, returncode=returncode,
                     duration_seconds=elapsed, log_path=log_path,
                     timed_out=timed_out, logs=_log_paths(log_path),
                     stdout=stdout, stderr=stderr)


def install(package: config_mod.InstallerPackage, options: dict[str, Any],
            log_path: Path, *, timeout: int, mode: str = "passive") -> RunResult:
    """Run an unattended installation.

    `timeout` is required on purpose: settings.toml carries the reasoning for
    the figure, and a default here would be a second opinion free to drift.

    `mode` is "passive" or "quiet", and the difference is NOT cosmetic. Measured:
    under /passive the MSI gets UILevel 4, executes InstallUISequence and
    therefore RUNS the environment checks -- their dialogs are merely suppressed.
    /quiet gives UILevel 2 and skips the sequence entirely, so prerequisite
    validation never happens at all.
    """
    command = [str(package.path), f"/{mode}", "/norestart",
               "/log", str(log_path), *as_properties(options)]
    return _run("install", command, log_path, timeout)


def uninstall(package: config_mod.InstallerPackage, log_path: Path, *,
              timeout: int, mode: str = "passive") -> RunResult:
    """Uninstall using the installer bundle itself."""
    command = [str(package.path), f"/{mode}", "/norestart", "/uninstall",
               "/log", str(log_path)]
    return _run("uninstall", command, log_path, timeout)


def uninstall_with_command(uninstall_string: str, log_path: Path, *,
                           timeout: int) -> RunResult:
    """Uninstall through the Apps & Features command, as Windows would run it.

    The string is passed in from observed machine state rather than
    reconstructed here, so this driver keeps no dependency on the verification
    layer.
    """
    parts = _split_command(uninstall_string)
    if not parts:
        raise InstallerError(f"could not parse uninstall string: {uninstall_string!r}")
    command = [*parts, "/passive", "/norestart", "/log", str(log_path)]
    return _run("uninstall[arp]", command, log_path, timeout)


def _split_command(text: str) -> list[str]:
    """Split a Windows uninstall string, honouring the quoted executable path."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""            # backslashes are path separators, not escapes
    return list(lexer)
