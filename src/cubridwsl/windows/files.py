r"""What the product leaves on disk: its install directory and the WSL disk image.

Separate from `registry.py` because an uninstall can remove every registry
trace and still leave a folder behind, and one file in that folder matters more
than all the rest:

    <install dir>\ext4.vhdx

That is the virtual disk holding the distribution. `ActionUninstallWsl` is
declared Return="ignore", so the uninstaller can exit 0 having failed to
unregister the distribution -- and a surviving ext4.vhdx HARD-BLOCKS every
future install, because `InstallWslAndCubrid` aborts the moment it finds one.
A machine in that state looks clean in Apps & Features and cannot be
reinstalled, which is why it gets its own named check rather than living inside
a general "leftovers" bag.

Nothing here DELETES anything. A framework that quietly removes a virtual disk
to keep its own suite green is a framework that will one day remove the wrong
one. Detect, report, stop.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path

from . import registry


def install_dirs(wsl_name: str | None = None) -> list[Path]:
    """Every directory the product could have installed itself into.

    Two sources, because they answer at different times. The registry names the
    real one, but the uninstall removes that key -- so `wsl_name`, captured
    while the product was still installed, reconstructs the default location
    (`%LOCALAPPDATA%\\<name>`, which is what `ActionUpdateInstallFolder` derives)
    for the reading taken AFTER.

    Deduplicated case-insensitively: Windows paths differing only in case are
    the same directory, and reporting one twice reads as two problems.
    """
    candidates: list[Path] = []
    recorded = registry.install_dir()
    if recorded:
        candidates.append(recorded)

    local = os.environ.get("LOCALAPPDATA", "")
    for name in (wsl_name, registry.wsl_name()):
        if name and local:
            candidates.append(Path(ntpath.join(local, str(name))))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(registry.normalize_path(str(path)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def leftovers(wsl_name: str | None = None) -> list[str]:
    """Files still on disk that must not be, in plain language.

    Empty is the pass. Each entry says what it is and why it matters, so a
    failing case reports something actionable rather than a path.
    """
    found: list[str] = []
    for path in install_dirs(wsl_name):
        image = path / "ext4.vhdx"
        if image.is_file():
            found.append(
                f"{image} still exists -- this HARD-BLOCKS reinstall, because "
                "InstallWslAndCubrid aborts when it finds an ext4.vhdx. The "
                "distribution was not unregistered, whatever the uninstaller "
                "reported.")
        elif path.is_dir() and any(path.iterdir()):
            found.append(f"{path} still exists and is not empty")
    return found
