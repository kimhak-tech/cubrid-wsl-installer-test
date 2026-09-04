"""Installer Orchestration -- the silent track.

    INS-002  Select WSL2 mode explicitly (default)

DESTRUCTIVE: uninstalls whatever is on the machine and installs the configured
bundle. Needs an elevated shell.
"""
from __future__ import annotations

import pytest

from cubridwsl import distro

pytestmark = [pytest.mark.silent, pytest.mark.destructive]


def test_ins_002_distribution_is_imported_as_wsl2(silent_install, settings, note):
    """IS_WSL2_MODE=1 must produce a version 2 distribution.

    This matters more than it looks. The product runs `wsl --set-default-version
    2` and then imports WITHOUT `--version 2`; `--set-default-version` is a
    native command, so a non-zero exit does not stop the installer's PowerShell
    script. A silently-WSL1 install is an unguarded path in the product, which
    is why the VERSION column is read directly instead of being inferred from
    the option that was passed.
    """
    # The shared verification layer's verdict, narrowed to the evidence this
    # case is about -- so a failure names the distribution, not everything.
    problem = silent_install.comparison.problems("distro")
    assert not problem, problem

    # Cross-check straight from wsl.exe rather than through the snapshot, so a
    # bug in the verification layer cannot let this case agree with itself.
    name = silent_install.state.registry.wsl_name
    assert name, "the registry did not record a WslName"
    entry = distro.find(name)
    assert entry is not None, f"`wsl -l -v` does not list {name!r}"
    note(f"  INS-002    : {entry.name}  state={entry.state}  VERSION={entry.version}")

    assert entry.version == 2, (
        f"distribution {name!r} was imported at WSL{entry.version} despite "
        "IS_WSL2_MODE=1.")

    protected = settings.get("safety", {}).get("protected_distros", [])
    assert name not in protected, (
        f"the product's distro name {name!r} collides with a protected distro; "
        "every later cleanup would target the wrong distribution")
