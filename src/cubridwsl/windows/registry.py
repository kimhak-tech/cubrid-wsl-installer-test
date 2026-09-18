r"""The product's own registry footprint: its key, and its two startup entries.

Everything the INSTALLER wrote to the registry. What WINDOWS wrote about the
product -- the Apps & Features entry -- is `windows/apps.py`, a different owner
answering a different question.

The key lives under HKCU, so every answer here is per-ACCOUNT: run elevated as
a different user and a perfectly installed product reads as absent. That is why
`preflight.current_account()` goes into every run report.

Each function asks the machine NOW. Nothing is cached, because a case checking
what an uninstall left behind must be reading the machine after it, not a
reading taken before.
"""
from __future__ import annotations

import ntpath
from pathlib import Path
from typing import Any

from .. import constants


def read_key(hive_name: str, path: str) -> dict[str, Any] | None:
    """Every value under one registry key, or None if the key is absent.

    The shared accessor. `windows/apps.py` uses it too -- there is one way to
    read a registry key and it should not be written twice.
    """
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


def normalize_path(value: str | None) -> str | None:
    """Compare Windows paths without tripping over case or a trailing separator.

    `InstallDir` is stored with one, so a direct string compare against an
    expected path fails for a perfectly correct install. ntpath (not os.path)
    keeps this deterministic and testable off Windows.
    """
    if not value:
        return value
    return ntpath.normcase(ntpath.normpath(str(value)))


def values() -> dict[str, Any]:
    """Every value under the product key, raw. Empty when it is not there.

    For run notes and diagnosis. The questions below are what a case should
    assert on.
    """
    return read_key("HKCU", constants.PRODUCT_KEY) or {}


def exists() -> bool:
    r"""Is HKCU\Software\CUBRID\CUBRID_FOR_WSL there?

    This key IS the product's record that it is installed, so its absence after
    an uninstall is the workbook's "CUBRID-related registry entries are
    removed".
    """
    return read_key("HKCU", constants.PRODUCT_KEY) is not None


def wsl_name() -> str | None:
    """The distribution name the product created, while it is still installed.

    Read BEFORE an uninstall. The uninstall removes the key that carries it,
    and "is the distribution gone" cannot be asked without knowing which one to
    ask about.
    """
    return values().get("WslName")


def install_dir() -> Path | None:
    """Where the product installed itself, as it recorded it."""
    raw = values().get("InstallDir")
    return Path(str(raw)) if raw else None


def tray_binary_path() -> Path | None:
    """Where the product put the Tray executable, as IT recorded it.

    Not composed from `install_dir()` and `constants.TRAY_EXE`: a composed path
    is this framework's opinion of where the file should be, and a case
    asserting the binary was DELETED would then pass by looking in the wrong
    place.
    """
    raw = values().get("TrayAppFile")
    return Path(str(raw)) if raw else None


def desktop_folder() -> Path | None:
    """The desktop the product put its shortcuts on, as it recorded it."""
    raw = values().get("UsersDesktopFolder")
    return Path(str(raw)) if raw else None


def tray_shortcut_file() -> str | None:
    """The Tray's desktop shortcut file name, as the product recorded it."""
    return values().get("TrayAppLinkFile")


def tray_registered_for_startup() -> bool:
    r"""Is CUBRID_WSL_TrayApp under HKCU\...\Run?

    This one follows the auto-start install option, so it is legitimately
    absent on a machine installed with REG_TRAY_APP=0.
    """
    startup = read_key("HKCU", constants.RUN_KEY) or {}
    return constants.RUN_VALUE_TRAY in startup


def starter_registered_for_startup() -> bool:
    """Is CUBRID_WSL_Starter under Run?

    Separate from the Tray's value and it behaves differently: the installer
    registers this one UNCONDITIONALLY, with no option controlling it. So after
    an uninstall it must be gone whatever options the install used.
    """
    startup = read_key("HKCU", constants.RUN_KEY) or {}
    return constants.RUN_VALUE_STARTER in startup
