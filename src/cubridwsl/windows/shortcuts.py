"""The desktop shortcuts the installer creates: the distribution's and Tray's.

`CREATE_SHORTCUT` is ONE toggle controlling both, so a case expects the two to
be present together or absent together.

Both paths come from what the product recorded in its registry key -- never a
hard-coded desktop or name -- so a custom-name install still resolves. Ask
while the product is installed: the uninstall removes that key.

Targets and icons are not asked about. Opening the Tray's shortcut is: whether
the Tray then starts is the proof that its target is right.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import constants
from . import registry


def distro_shortcut_path(wsl_name: str | None) -> Path | None:
    """Where `<WslName>.lnk` belongs on the desktop the product recorded."""
    desktop = registry.desktop_folder()
    if not (desktop and wsl_name):
        return None
    return desktop / f"{wsl_name}.lnk"


def tray_shortcut_path() -> Path | None:
    """Where the Tray's shortcut belongs on the recorded desktop."""
    desktop = registry.desktop_folder()
    if not desktop:
        return None
    return desktop / (registry.tray_shortcut_file()
                      or constants.TRAY_SHORTCUT_FILE)


def distro_shortcut_exists(wsl_name: str | None) -> bool:
    """Is the distribution's desktop shortcut there?"""
    path = distro_shortcut_path(wsl_name)
    return bool(path) and path.is_file()


def tray_shortcut_exists() -> bool:
    """Is the Tray's desktop shortcut there?"""
    path = tray_shortcut_path()
    return bool(path) and path.is_file()


def open_tray_shortcut() -> None:
    """Open the Tray's shortcut as a double-click does: the Shell's "open" verb.

    Returns at once; whatever the shortcut starts is the caller's to wait for.
    """
    path = tray_shortcut_path()
    if not (path and path.is_file()):
        raise FileNotFoundError(f"the Tray's desktop shortcut is not there: {path}")
    os.startfile(str(path))  # type: ignore[attr-defined]
