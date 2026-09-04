"""The single choke point for all WSL execution.

No other module may invoke wsl.exe. Everything that talks to a distribution
comes through here, so encoding, exit codes, timeouts and the safety rules are
solved once.

Two safety rules are enforced here rather than left to callers:

* `wsl --shutdown` is machine-global and would terminate every distribution on
  the box, including a developer's Docker Desktop. It is never issued.
* The default distribution is never used implicitly. Every call names its target
  with -d, because the default on a developer machine is somebody else's distro.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable

WSL = "wsl.exe"


class DistroError(RuntimeError):
    """A WSL operation failed, or was refused by the safety rules."""


class ProtectedDistroError(DistroError):
    """Refused an operation against a distribution the framework must not touch."""


@dataclass(frozen=True)
class Distro:
    """One registered distribution, as `wsl -l -v` reports it."""

    name: str
    state: str
    version: int
    default: bool


@dataclass(frozen=True)
class CommandResult:
    """One wsl.exe invocation, with its output already decoded."""

    returncode: int
    stdout: str
    stderr: str
    command: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _decode(raw: bytes) -> str:
    """wsl.exe emits UTF-16LE for its OWN output (-l -v, --status) but passes
    guest output through as bytes. Detect rather than assume."""
    if not raw:
        return ""
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _wsl(args: list[str], timeout: int) -> CommandResult:
    """Invoke wsl.exe. The one place the safety rules are enforced."""
    if "--shutdown" in args:
        raise ProtectedDistroError(
            "wsl --shutdown is machine-global and is never permitted by this "
            "framework; it would terminate every distribution on the host.")
    completed = subprocess.run([WSL, *args], capture_output=True, timeout=timeout)
    return CommandResult(returncode=completed.returncode,
                         stdout=_decode(completed.stdout).strip(),
                         stderr=_decode(completed.stderr).strip(),
                         command=" ".join([WSL, *args]))


def available(timeout: int = 30) -> bool:
    """Whether wsl.exe responds at all. Never raises."""
    try:
        return _wsl(["--status"], timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def list_distros(timeout: int = 30) -> list[Distro]:
    """Every registered distribution.

    Rows are split from the RIGHT: STATE and VERSION are single tokens, but a
    NAME may contain spaces -- Microsoft's own image registers itself as
    "Ubuntu 22.04 LTS". Reading the name as the first token made int() raise on
    such a row, the row was skipped, and that distribution vanished from the
    list entirely: silently, and worst on the machines that have co-tenants.
    """
    result = _wsl(["-l", "-v"], timeout)
    if not result.ok and not result.stdout:
        raise DistroError(f"`wsl -l -v` failed: {result.stderr or result.returncode}")

    distros: list[Distro] = []
    for line in result.stdout.splitlines()[1:]:          # drop the header row
        raw = line.rstrip()
        if not raw.strip():
            continue
        default = raw.lstrip().startswith("*")
        parts = raw.replace("*", " ", 1).split()
        if len(parts) < 3:
            continue
        *name_parts, state, version = parts
        if not name_parts:
            continue
        try:
            version_num = int(version)
        except ValueError:
            continue
        distros.append(Distro(name=" ".join(name_parts), state=state,
                              version=version_num, default=default))
    return distros


def find(name: str, timeout: int = 30) -> Distro | None:
    """The distribution with this name, or None. Case-insensitive."""
    for distro in list_distros(timeout):
        if distro.name.casefold() == name.casefold():
            return distro
    return None


def assert_not_protected(name: str, protected: Iterable[str]) -> None:
    """Guard for any mutating operation. Call before unregister/terminate."""
    if name.casefold() in {p.casefold() for p in protected}:
        raise ProtectedDistroError(
            f"{name!r} is listed in safety.protected_distros and must not be "
            "modified by the test framework.")


def run(name: str, command: str, *, user: str | None = None,
        env_setup: str = "", login: bool = False,
        timeout: int = 60) -> CommandResult:
    """Run a shell command inside a distribution.

    By default this mirrors how the product itself invokes WSL, so what the
    tests observe is what the installer and the Tray observe:

        wsl.exe -d <name> -u <user> --exec bash -c "<env_setup><command>"

    `login=True` runs `bash -lc` instead, which reads ~/.bash_profile -- the
    file the image uses to source ~/.cubrid.sh. Pass `login=True` WITHOUT
    `env_setup` when the question is whether the environment is set up for a
    real user session; sourcing it ourselves would answer a different question.

    stdin is redirected from /dev/null because a guest command that blocks on
    input otherwise hangs to the timeout with no diagnostic.
    """
    if not name:
        raise DistroError("a distribution name is required; the default "
                          "distribution is never used implicitly")
    args = ["-d", name]
    if user:
        args += ["-u", user]
    args += ["--exec", "bash", "-lc" if login else "-c",
             f"{env_setup}{command} </dev/null"]
    return _wsl(args, timeout)
