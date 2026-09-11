"""Environment checks: is THIS machine ready to run the test cases?

Not test cases. Nothing here maps to a workbook row -- including the ENV-xxx
rows, which test the INSTALLER's prerequisite handling. These check the machine
the suite runs on: that the framework can read its configuration, resolve the
bundle you named, reach WSL and decode its output, and read the product's
registry key. The answer depends on the machine, so the same check can pass on
one box and fail on another. If any of this fails, no case result means anything.

The filename deliberately does not match pytest's `test_*.py`, so an ordinary
collection never counts these among the cases. `run-tests.ps1` runs this file
by path, once, before every suite and every -Case run, and stops before any
case if it fails -- one second instead of a failed two-minute install.
Read-only, needs no installed product. Alone:

    .\\run-tests.ps1 checks
"""
from __future__ import annotations

from cubridwsl import config as config_mod
from cubridwsl import distro, preflight, state as state_mod


def test_this_is_windows():
    """Every layer below drives Windows: wsl.exe, winreg, Win32 windows."""
    assert preflight.WINDOWS, (
        "this framework drives a Windows installer and cannot run elsewhere")


def test_settings_load_and_carry_the_keys_the_framework_reads(settings, note):
    """A missing timeout must fail here, not three minutes into an install."""
    for section in ("installer", "distro", "safety", "timeouts", "upgrade"):
        assert section in settings, f"settings.toml has no [{section}] section"
    assert settings["upgrade"].get("url"), (
        "settings.toml has no upgrade.url -- OPS-004 has nothing to install")
    for key in ("install_seconds", "uninstall_seconds", "wsl_command_seconds",
                "settle_seconds", "cubrid_command_seconds", "upgrade_seconds"):
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


def test_the_installer_path_is_not_in_the_committed_config(note):
    """Your bundle path belongs in settings.local.toml, which is gitignored.

    Put it in the tracked settings.toml instead and it works on your machine
    while handing every teammate a path that does not exist -- and the
    repository grows a reference to your D: drive.
    """
    committed = config_mod.committed_installer_path()
    assert not committed, (
        f"config/settings.toml (which IS committed) sets installer.path to "
        f"{committed!r}.\nMove it into config/settings.local.toml, which is "
        "gitignored and merged over it:\n\n"
        "  [installer]\n"
        f'  path = "{committed}"\n\n'
        "and set the tracked file back to an empty path.")
    note("  installer  : path comes from settings.local.toml, as it should")


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
