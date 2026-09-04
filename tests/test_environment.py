"""Framework self-check. Read-only, needs no installed product, takes seconds.

Not a test case from the xlsx -- it is the thing you run BEFORE trusting any
result. It proves the framework can read its configuration, resolve the bundle
you named, reach WSL and decode its output. If any of this fails, nothing after
it means anything.

    .\\run-tests.ps1 environment
"""
from __future__ import annotations

import pytest

from cubridwsl import config as config_mod
from cubridwsl import distro, preflight, state as state_mod

pytestmark = pytest.mark.environment


def test_this_is_windows():
    """Every layer below drives Windows: wsl.exe, winreg, Win32 windows."""
    assert preflight.WINDOWS, (
        "this framework drives a Windows installer and cannot run elsewhere")


def test_settings_load_and_carry_the_keys_the_framework_reads(settings, note):
    """A missing timeout must fail here, not three minutes into an install."""
    for section in ("installer", "distro", "safety", "timeouts"):
        assert section in settings, f"settings.toml has no [{section}] section"
    for key in ("install_seconds", "uninstall_seconds", "wsl_command_seconds",
                "settle_seconds"):
        assert key in settings["timeouts"], f"settings.toml has no timeouts.{key}"
    note(f"  settings   : local overrides = "
         f"{settings.get('_meta', {}).get('local_overrides', 'none')}")


def test_the_configured_installer_resolves(installer, note):
    """Naming the bundle is the setup step people miss, so it is checked early.

    The version fields are read OUT of the filename and the SHA-256 is what
    actually identifies the build -- the build number is a commit count, so two
    different binaries can share a name.
    """
    assert installer.path.is_file()
    note(f"  installer  : {installer.describe()}")


def test_wsl_responds_and_its_output_decodes(note):
    """wsl.exe emits UTF-16LE for its own output. If decoding were wrong, every
    distribution check would silently see nothing."""
    assert distro.available(), "`wsl --status` did not succeed"
    distros = distro.list_distros()
    for entry in distros:
        note(f"  wsl        : {entry.name} (state={entry.state}, "
             f"v{entry.version}{', default' if entry.default else ''})")
    assert distros, ("`wsl -l -v` listed no distributions at all, which usually "
                     "means the output was not decoded rather than that the "
                     "machine has none")


def test_the_registry_is_readable_and_reports_the_product_state(settings, note):
    """Reads the product's own HKCU key. Absent is a valid answer -- the point is
    that the read works and says which account it read for."""
    registry = state_mod.read_registry()
    assert registry.error is None, f"reading the product key failed: {registry.error}"
    note(f"  account    : {preflight.current_account()} "
         f"(elevated={preflight.is_elevated()})")
    note(f"  product    : {'INSTALLED' if registry.present else 'not installed'} "
         f"for this account"
         + (f", WslName={registry.wsl_name}" if registry.present else ""))
