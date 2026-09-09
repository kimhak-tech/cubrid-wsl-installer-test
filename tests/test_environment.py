"""Framework self-check. Read-only, needs no installed product, takes seconds.

Not a test case from the scenario matrix -- it is what you run BEFORE trusting
any result. It proves the framework can read its configuration, resolve the
bundle you named, reach WSL and decode its output. If any of this fails, nothing
after it means anything.

    .\\run-tests.ps1 environment
"""
from __future__ import annotations

import pathlib

import pytest

from cubridwsl import config as config_mod
from cubridwsl import (constants, distro, preflight,
                       state as state_mod, verify)

pytestmark = pytest.mark.environment


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


def test_the_installer_is_not_blocked_by_mark_of_the_web(installer, note):
    """A downloaded bundle is flagged, and an unattended run cannot click past
    the SmartScreen dialog that flag produces.

    Checked here rather than fixed silently: unblocking edits a file you pointed
    us at, and that is your call, not the framework's.
    """
    motw = installer.mark_of_the_web
    assert motw is None, (
        f"{installer.path.name} carries a Mark-of-the-Web (it was downloaded or "
        f"copied from a network share):\n{motw}\n\n"
        "SmartScreen can block an unsigned bundle so flagged, and an unattended "
        "install has nothing to click the dialog with -- it would hang until the "
        "install timeout. Clear it with:\n"
        f"    Unblock-File -Path '{installer.path}'")


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


def test_the_shortcut_parser_reads_a_link_the_way_windows_writes_one(tmp_path, note):
    """`read_shortcut` parses the .lnk binary format, in both string encodings.

    Shortcuts are parsed in pure Python rather than through WScript.Shell,
    because the shortcut assertions run on the SILENT track too -- INS-002
    applies INS-001's whole post-install set -- and a COM reader would give that
    track a pywinauto dependency the workbook defines it as not having.
    That choice is only safe if the parser is right, so it is pinned here --
    off Windows, in milliseconds, against a link built byte by byte.

    Both encodings matter: the product writes shortcuts through IShellLinkA, so
    an ANSI link is what this build produces, but Windows is free to store
    Unicode strings and sets the IsUnicode flag when it does.
    """
    for unicode_strings in (True, False):
        path = tmp_path / f"sample-{unicode_strings}.lnk"
        path.write_bytes(_build_lnk(
            target=r"C:\Windows\System32\wsl.exe",
            arguments="-d CUBRID-FOR-WSL -u cubrid --cd ~",
            icon=r"C:\Windows\System32\wsl.exe,0",
            unicode_strings=unicode_strings))

        shortcut = state_mod.read_shortcut(path)
        note(f"  shortcut   : unicode={unicode_strings} -> {shortcut.describe()}")
        assert shortcut.error is None, shortcut.error
        assert shortcut.target == r"C:\Windows\System32\wsl.exe"
        assert shortcut.arguments == "-d CUBRID-FOR-WSL -u cubrid --cd ~"
        assert shortcut.icon_location == r"C:\Windows\System32\wsl.exe,0"
        # The trailing ",0" is an icon INDEX, not part of the path. Splitting on
        # the last comma is safe: a Windows path cannot contain one.
        assert shortcut.icon_path == r"C:\Windows\System32\wsl.exe"
        assert shortcut.icon_matches_target, (
            "the product sets the icon to the target executable itself, so "
            "these must compare equal after path normalisation")

    missing = state_mod.read_shortcut(tmp_path / "absent.lnk")
    assert missing.exists is False and missing.error is None, (
        "an absent shortcut is an ANSWER, not an error")

    (tmp_path / "junk.lnk").write_bytes(b"not a shell link at all")
    junk = state_mod.read_shortcut(tmp_path / "junk.lnk")
    assert junk.exists and junk.error and junk.target is None, (
        "an unparseable .lnk must report an error and no target -- never a "
        "guessed one")


def _build_lnk(target: str, arguments: str, icon: str,
               unicode_strings: bool) -> bytes:
    """One [MS-SHLLINK] shell link, assembled by hand.

    Only the parts `read_shortcut` reads: the header, a LinkInfo block carrying
    LocalBasePath, and the ARGUMENTS and ICON_LOCATION StringData sections.
    """
    import struct

    has_link_info, has_args, has_icon, is_unicode = 0x2, 0x20, 0x40, 0x80
    flags = has_link_info | has_args | has_icon | (is_unicode if unicode_strings else 0)
    header = (struct.pack("<I", 0x4C) + b"\x01\x14\x02\x00" + b"\x00" * 12
              + struct.pack("<I", flags) + struct.pack("<I", 0) + b"\x00" * 24
              + struct.pack("<IiiI", 0, 0, 1, 0) + b"\x00" * 10)[:76].ljust(76, b"\x00")

    base = target.encode("cp1252") + b"\x00"
    fixed = 28                                   # LinkInfoHeaderSize 0x1C
    link_info = struct.pack("<IIIIIII", fixed + len(base) + 1, 0x1C, 1, 0,
                            fixed, 0, fixed + len(base)) + base + b"\x00"

    def _string(text: str) -> bytes:
        encoded = (text.encode("utf-16-le") if unicode_strings
                   else text.encode("cp1252"))
        return struct.pack("<H", len(text)) + encoded

    return header + link_info + _string(arguments) + _string(icon)


def test_one_broken_thing_produces_one_failure_line(note):
    """A failed prerequisite SUPPRESSES the checks that depend on it.

    Without this, one root cause fanned out into several failure lines that all
    said the same thing: an unreadable `cubrid service status` produced four,
    and a login shell that would not open produced five -- burying the single
    line that carried the reason.

    Suppression is not the same as passing, and the difference is the whole
    reason this test exists: a suppressed check is reported as `[skip]` naming
    its prerequisite, and it is in the JSON report. A fact that went unverified
    must never be mistaken for one that was verified.
    """
    options = dict(constants.INSTALL_OPTIONS)

    # Nothing installed at all: registry.present is the only real finding.
    nothing = _bare_machine()
    comparison = verify.compare(options, nothing)
    identity = [r for r in comparison.results if r.area == "identity"]
    failed = [r for r in identity if r.failed]
    skipped = [r for r in identity if r.skipped]
    note(f"  suppression: nothing installed -> {len(failed)} failure, "
         f"{len(skipped)} skipped")

    assert [r.name for r in failed] == ["registry.present"], (
        f"one missing product key should be ONE finding, got "
        f"{[r.name for r in failed]}")
    assert {r.name for r in skipped} == {
        "registry.Installed", "registry.wsl_name", "registry.install_dir",
        "install_dir.exists"}, (
        f"the dependent identity checks should all be skipped, got "
        f"{[r.name for r in skipped]}")

    # Skipped is NOT passed: it must still be visible as unverified.
    for result in skipped:
        assert not result.failed and result.skipped_because, result
        assert "[skip]" in str(result), (
            f"a skipped check must render as [skip], got {str(result)!r}")

    # Every skipped check must name a prerequisite that is ITSELF a finding --
    # failed, or skipped for the same reason further down. Asserted as an
    # invariant rather than as a specific chain on purpose: which link is named
    # depends on the machine (off Windows there is no %LOCALAPPDATA%, so
    # registry.install_dir compares None to None, matches, and the chain
    # collapses by one). The invariant holds either way; a hard-coded link
    # would make this test pass or fail on where it happened to run.
    by_name = {r.name: r for r in comparison.results}
    for result in skipped:
        blocker = by_name[result.skipped_because]
        assert blocker.failed or blocker.skipped, (
            f"{result.name} was skipped because of {result.skipped_because}, "
            "which is neither failed nor skipped -- so nothing justified "
            "suppressing it, and a real finding has gone unreported.")


def _bare_machine() -> state_mod.MachineState:
    """A machine with nothing installed, for the suppression check above."""
    return state_mod.MachineState(
        registry=state_mod.RegistryState(present=False),
        startup=state_mod.StartupState(None, None),
        arp=state_mod.ArpState(present=False),
        shortcuts=state_mod.ShortcutState(
            state_mod.Shortcut(None, False), state_mod.Shortcut(None, False)),
        tray=state_mod.TrayState(None, None),
        # Keyword arguments, deliberately: CubridState has ten fields and
        # positional construction silently shifts every one of them when a
        # field is added or removed.
        cubrid=state_mod.CubridState(
            distro_present=False, distro_running=False, distro_version=None,
            cubrid_version=None, demodb_present=None))


def test_a_shortcut_with_a_doubled_separator_is_reported_precisely(note):
    """The tray shortcut defect found on build 11.4-1.0.0-0003, pinned.

    `CubridCustomActions.cpp` builds the tray target as
    `installDir + "\\" + trayAppFile`, and InstallDir is stored WITH a trailing
    backslash -- so the path carries a doubled separator and Windows saves the
    link with no LinkInfo LocalBasePath. The WSL shortcut is unaffected: it is
    built from `wslPath`, which has no trailing separator.

    Without the `_resolvable` check that produced two bare `False`s --
    tray_target_exists and tray_icon_is_the_target -- with nothing saying why.
    Now it is one failure carrying the malformed path and where it is built.
    """
    icon = ("C:\\Users\\USER\\AppData\\Local\\CUBRID-FOR-WSL\\"
            "\\cubrid_tray_app.exe")
    broken = state_mod.Shortcut(
        pathlib.Path("C:\\Users\\USER\\Desktop\\cubrid_tray_app.lnk"),
        exists=True, target=None, icon_location=icon,
        unresolved_reason="the LinkInfo block carries no LocalBasePath")

    assert broken.doubled_separator == icon, (
        "the doubled separator must be found in the RAW string -- normalising "
        "first would collapse it and hide the defect")
    resolution = broken.resolution
    assert isinstance(resolution, str), (
        "a link that exists but names no target must resolve to a DESCRIPTION, "
        f"not a bare bool, got {resolution!r}")
    assert "DOUBLED separator" in resolution and "CubridCustomActions" in resolution
    note(f"  shortcut   : broken tray link -> {resolution[:80]}...")

    # A clean link is unaffected, and a drive letter's own colon-backslash must
    # never be mistaken for a doubled separator.
    good = state_mod.Shortcut(pathlib.Path("x.lnk"), exists=True,
                              target="C:\\Windows\\System32\\wsl.exe",
                              icon_location="C:\\Windows\\System32\\wsl.exe")
    assert good.doubled_separator is None and good.resolution is True


def test_the_state_diff_compares_everything_except_what_it_says_it_ignores(note):
    """`state.diff` is the whole of INS-002's value, so it is pinned here.

    INS-002 asserts the silent install produced the same machine as the wizard
    install, and the workbook insists that be done by COMPARING SNAPSHOTS rather
    than by re-listing INS-001's assertions. A diff that silently compared
    nothing would pass forever and prove nothing -- which is the failure mode
    worth catching in a second rather than after two install cycles.
    """
    left = _bare_machine()

    assert state_mod.diff(left, left) == [], (
        "a machine must not differ from itself")

    # Every ignored path must be a path that EXISTS in the report. A typo here
    # would ignore nothing and go unnoticed, because ignoring a field that is
    # not there looks exactly like ignoring one that is.
    report = left.as_dict()
    for path in state_mod.DIFF_IGNORE:
        node = report
        for part in path.split("."):
            assert isinstance(node, dict) and part in node, (
                f"DIFF_IGNORE names {path!r}, which is not a field in the state "
                f"report -- it ignores nothing. Stopped at {part!r}.")
            node = node[part]
    note(f"  diff       : ignoring {list(state_mod.DIFF_IGNORE)}, all present")

    # A real difference is found, and named by its dotted path.
    changed = state_mod.MachineState(
        registry=state_mod.RegistryState(present=True, values={"WslName": "OTHER"}),
        startup=left.startup, arp=left.arp, shortcuts=left.shortcuts,
        tray=left.tray, cubrid=left.cubrid)
    differences = state_mod.diff(left, changed)
    assert any("registry.present" in d for d in differences), differences
    assert any("registry.values.WslName" in d for d in differences), differences

    # An ignored field differing does NOT register.
    noisy = state_mod.MachineState(
        registry=left.registry, startup=left.startup, arp=left.arp,
        shortcuts=left.shortcuts, tray=left.tray,
        cubrid=state_mod.CubridState(
            distro_present=False, distro_running=False, distro_version=None,
            cubrid_version=None, demodb_present=None,
            service_status_raw="different pids every run"))
    assert state_mod.diff(left, noisy) == [], (
        "cubrid.service_status_raw carries process IDs and must be ignored; "
        f"got {state_mod.diff(left, noisy)}")
