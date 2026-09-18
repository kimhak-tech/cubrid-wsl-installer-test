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

Two functions at the end -- `install_cubrid_wsl` and `uninstall_cubrid_wsl` --
go one step further than the rest: they WAIT for the machine to reach the state
they name, so they read `windows` and `wsl` to know when it has. That still
judges nothing; it only means a test calling them is never racing an install
that has not finished landing. Everything above them runs one command and
returns.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import config as config_mod, constants, preflight
from ..windows import registry, tray
from ..wsl import cubrid, distro

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


def variable_from_log(log_path: Path, name: str) -> str | None:
    """The value Burn RECORDED for one of its variables, from its own log.

        i410: Variable: IS_WSL2_MODE = 0

    Read back rather than restated from the command line, for the same reason
    the wizard driver reads a checkbox back after clicking it: the command line
    says only what was ASKED FOR. A property the bundle never received, or
    stopped forwarding, is invisible to a test that checks the EFFECT alone --
    on a host that already produces that effect by default, such a test passes
    for the wrong reason.

    Matched on the `Variable: ` prefix so the command line Burn echoes near the
    top of the log, which contains the overrides verbatim, cannot answer for it.
    The LAST occurrence wins: Burn dumps its variables once at the end, after
    everything that could have changed them.

    Returns None when the line is absent -- which is itself worth reporting,
    since a bundle that never wrote it may have failed before it started.

    The log is read as bytes and decoded leniently: Burn writes UTF-8 with a
    BOM, and a decode error here must not lose the diagnosis.
    """
    try:
        text = log_path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    value = None
    for match in re.finditer(rf"Variable: {re.escape(name)}\s*=\s*(.*)", text):
        value = match.group(1).strip()
    return value


def text_in_logs(logs: list[Path], fragments: list[str]) -> str:
    """The first line across these logs containing any fragment, or "".

    For the runs whose subject is a REFUSAL. A machine that did not change is
    satisfied equally by a bundle that refused and by one that did nothing at
    all, so the bundle saying why it stopped is the difference between the two.

    Searched across every log the run produced rather than just the bundle's
    own: which file a message lands in is Burn's choice to change.

    The same lenient decode as `variable_from_log` -- Burn writes UTF-8 with a
    BOM, and a decode error must never lose the diagnosis.
    """
    for path in logs:
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if any(fragment in line for fragment in fragments):
                return line.strip()
    return ""


def ui_level_from_log(log_path: Path) -> int | None:
    """The UI level Burn RECORDED for this run: 2 quiet, 3 passive, 4 wizard.

    What the bundle DID, as opposed to what the command line asked for.
    """
    try:
        return int(variable_from_log(log_path, constants.BURN_UI_LEVEL_VARIABLE))
    except (TypeError, ValueError):
        return None


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
                           timeout: int, mode: str = "quiet") -> RunResult:
    """Uninstall through the Apps & Features command, as Windows would run it.

    The string is passed in from observed machine state rather than
    reconstructed here, so this driver keeps no dependency on the verification
    layer.

    `mode` defaults to QUIET, and that default is load-bearing rather than a
    preference. This runs from `reset.ensure_clean`, immediately before the next
    install. Under /passive Burn draws a progress window titled "CUBRID For WSL
    Setup" and its parent process can return while that window is still closing
    -- and the wizard driver refuses to start while any window with that title
    is open, because it cannot tell a leftover apart from the one it is about to
    launch. The result is a run that fails on its own cleanup.

    Nothing is lost by hiding it: /passive vs /quiet changes whether
    ActionEnvironmentCheck runs, and that sequence is INSTALL-only. An uninstall
    behaves identically either way, and the uninstall is not what is under test.
    """
    parts = _split_command(uninstall_string)
    if not parts:
        raise InstallerError(f"could not parse uninstall string: {uninstall_string!r}")
    command = [*parts, f"/{mode}", "/norestart", "/log", str(log_path)]
    return _run("uninstall[arp]", command, log_path, timeout)


def _split_command(text: str) -> list[str]:
    """Split a Windows uninstall string, honouring the quoted executable path."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""            # backslashes are path separators, not escapes
    return list(lexer)


# --------------------------------------------------------------------------- #
# The two verbs a test calls
#
# Everything above runs ONE command and reports what happened. These two put
# the machine into a named state and do not return until it is there -- because
# an install that returns while demodb is still being written, or an uninstall
# that returns while its child processes are still exiting, makes every step
# after it a race, and a case that flakes is worse than one that fails.
#
# Neither asserts. What the machine looks like afterwards is the case's
# question, and every check it needs is one call away in `windows` and `wsl`.
# --------------------------------------------------------------------------- #
Note = Callable[[str], None]

# How long a fresh install is given to finish landing. The matching budget for
# a REMOVAL belongs to `windows.apps.wait_until_removed`, which every route
# shares.
READY_SETTLE_SECONDS = 300.0


def install_cubrid_wsl(package: config_mod.InstallerPackage,
                       settings: dict[str, Any], log_path: Path, *,
                       options: dict[str, Any] | None = None,
                       note: Note | None = None) -> RunResult:
    """Install CUBRID for WSL unattended, and wait until it is actually usable.

    /quiet. `options` are passed as property overrides and should name ONLY
    what a case changes: `constants.INSTALL_OPTIONS` IS the set of shipping
    defaults, so passing them all would test that the command line works
    rather than that the defaults do.

    It REPORTS rather than raises when the bundle does not report success: some
    cases launch a bundle they EXPECT to be rejected, and a verb that raised
    could not serve them. Nothing is waited for then -- there is no install to
    settle, and the full budget spent on a machine never installed is five
    minutes of nothing.
    """
    say: Note = note or (lambda _text: None)
    preflight.require_windows()
    preflight.require_elevation()
    options = dict(options or {})

    say(f"  install    : {package.path.name} {as_properties(options)}")
    result = install(package, options, log_path,
                     timeout=settings["timeouts"]["install_seconds"],
                     mode="quiet")
    say(f"  install    : {result.describe()}")

    if result.ok:
        wait_until_ready(settings, {**constants.INSTALL_OPTIONS, **options},
                         note=say)
    else:
        say("  install    : the bundle did not report success, so nothing is "
            "waited for -- there is no install to settle")
    return result


def wait_until_ready(settings: dict[str, Any], options: dict[str, Any], *,
                     note: Note | None = None) -> None:
    """Poll until an installation made with `options` has finished landing.

    Three effects land AFTER the bundle exits -- demodb (ActionCreateDemodb),
    the Tray (ActionLaunchTrayApp, asyncNoWait) and CUBRID's broker and manager
    (ActionStartCubridService returns once the MASTER is up) -- so a reading
    taken the moment the installer returns reports a startup in progress as
    components that failed. demodb and the Tray are waited for only when the
    options ask for them: "still absent" is never waited for.

    Shared by both install routes, so they cannot disagree about "installed".

    Reports rather than raises on a timeout. A component that never came up is
    a finding about the PRODUCT and belongs to whichever case asserts it, not
    to a set-up error that reads like the framework broke.
    """
    say: Note = note or (lambda _text: None)
    want_demodb = bool(int(options["CREATE_DEMODB"]))
    want_tray = bool(int(options["START_TRAY_APP"]))
    awaited = constants.SERVICE_COMPONENTS_AWAITED
    deadline = time.monotonic() + READY_SETTLE_SECONDS
    while True:
        name = registry.wsl_name()
        ready = (registry.exists() and distro.exists(name)
                 and (tray.is_running() or not want_tray)
                 and cubrid.service_status(name, settings).all_running(awaited)
                 and (cubrid.is_database_exists(name, constants.DEMODB_NAME,
                                                settings) or not want_demodb))
        if ready:
            say(f"  install    : settled -- distro {name!r}, CUBRID running"
                + (", Tray up" if want_tray else "")
                + (", demodb present" if want_demodb else ""))
            return
        if time.monotonic() >= deadline:
            say(f"  install    : did NOT fully settle in "
                f"{READY_SETTLE_SECONDS:.0f}s -- registry={registry.exists()} "
                f"distro={distro.exists(name)} tray={tray.is_running()} "
                f"cubrid={cubrid.service_status(name, settings).describe()}. "
                "Continuing so the case reports exactly what is and is not "
                "there.")
            return
        time.sleep(10)


def uninstall_cubrid_wsl(package: config_mod.InstallerPackage,
                         settings: dict[str, Any], log_path: Path, *,
                         note: Note | None = None) -> RunResult | None:
    """Remove any CUBRID for WSL on this machine. For SET-UP and CLEAN-UP only.

    Returns None when there was nothing to remove, so a first run on a clean
    machine costs nothing.

    Two things it does that `windows.apps.uninstall_cubrid_wsl` deliberately
    does NOT, which is why they are separate functions rather than one:

    * **it stops the Tray first.** By the second run of any suite there is a
      Tray holding the install directory open, and that is the likeliest cause
      of a leftover folder -- which would then read as a product defect rather
      than as our own doing. A case that is TESTING an uninstall must never do
      this: whether the product copes with its own running Tray is the
      question.
    * **it prefers the machine's own cached bundle** over the configured one.
      Installer filenames are not unique in this product, so "the installer I
      was pointed at" and "the installer that is actually installed" are not
      reliably the same binary -- and Burn will not uninstall a bundle it does
      not have registered.
    """
    say: Note = note or (lambda _text: None)
    preflight.require_windows()

    # Imported HERE, not at module scope. `windows.apps` needs this module to
    # run the command it finds, so a module-level import each way would be a
    # cycle. This is the only place the two meet, and it meets at call time.
    from ..windows import apps

    if not (registry.exists() or apps.is_listed()):
        say("  cleanup    : nothing installed")
        return None

    preflight.require_elevation()
    if tray.stop():
        say(f"  cleanup    : stopped a running {constants.TRAY_EXE}")

    name = registry.wsl_name()
    command = apps.uninstall_command()
    if command:
        say(f"  cleanup    : uninstalling via {command!r}")
        result = uninstall_with_command(
            command, log_path,
            timeout=settings["timeouts"]["uninstall_seconds"], mode="quiet")
    else:
        say(f"  cleanup    : no Apps & Features entry; using {package.path.name}")
        result = uninstall(package, log_path,
                           timeout=settings["timeouts"]["uninstall_seconds"],
                           mode="quiet")
    say(f"  cleanup    : {result.describe()}")

    apps.wait_until_removed(name, note=say)
    return result
