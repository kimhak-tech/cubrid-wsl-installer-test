"""Session preconditions.

Preflight REPORTS and REFUSES; it never repairs. Acquiring elevation, cleaning a
machine or installing a dependency mid-run turns a clear failure into a
confusing one.

Two things it exists to catch:

* **Elevation.** The MSI carries a Privileged launch condition, so an unelevated
  bundle raises a UAC prompt -- drawn on the Windows secure desktop, where no
  automation library can reach it. An unattended run simply hangs there.
* **The account.** The product writes its state to HKCU. If the elevated user is
  a different account from the interactive one, the installer's hive is not the
  hive the tests read, and every registry assertion is quietly wrong rather than
  failing.
"""
from __future__ import annotations

import ctypes
import getpass
import os
import platform
import sys

WINDOWS = sys.platform == "win32"

INSTALL_REASON = (
    "the installer's MSI carries a Privileged launch condition, so the bundle "
    "raises a UAC prompt that an unattended run hangs on")
WIZARD_REASON = (
    "the bundle raises a UAC prompt on the Windows secure desktop, which no "
    "automation library can interact with -- an unelevated wizard run hangs on "
    "a dialog pywinauto cannot even see")


def is_elevated() -> bool:
    """Whether this process is elevated. Always False off Windows."""
    if not WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def current_account() -> str:
    """DOMAIN\\user for this process -- which decides the HKCU hive it reads."""
    domain = os.environ.get("USERDOMAIN") or platform.node()
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME", "?")
    return f"{domain}\\{user}"


def require_windows() -> None:
    """Refuse plainly rather than failing later with a confusing import error."""
    if not WINDOWS:
        raise RuntimeError(
            f"this framework drives a Windows installer; it cannot run on "
            f"{platform.system()}.")


def require_elevation(reason: str = INSTALL_REASON) -> None:
    """Demand elevation at the point of use.

    Called by the fixtures that genuinely need it rather than gating the whole
    session, so the read-only checks stay runnable from an ordinary shell.
    """
    if not is_elevated():
        raise RuntimeError(
            f"this test requires an elevated process; running as "
            f"{current_account()} without elevation.\n"
            f"Reason: {reason}.\n"
            "Re-run from an Administrator PowerShell.")


def describe() -> dict[str, object]:
    """The facts a result depends on, for the run report."""
    return {
        "python": f"{platform.python_version()} at {sys.executable}",
        "platform": f"{platform.system()} {platform.release()}",
        "account": current_account(),
        "elevated": is_elevated(),
    }
