"""Framework checks: do the framework's own measuring tools give right answers?

Not test cases, and not about the machine either. Each check feeds a framework
function input built in the test, whose correct answer is known, and compares.
Every INS and OPS assertion reads the product through these functions -- the
.lnk parser, the skip-on-prerequisite rule, the doubled-separator diagnosis,
`state.diff` -- so a bug in one makes a case fail against a healthy product, or
pass against a broken one, on every machine. When a case fails after these
passed, the failure is the product's.

No wsl.exe, no registry, no installer: runs on any OS in under a second. Run it
after editing anything these checks name in `src/cubridwsl/`.

The filename deliberately does not match pytest's `test_*.py`; `run-tests.ps1`
runs it by path, once, after the environment checks and before the cases.

    .\\run-tests.ps1 checks
    python -m pytest tests/framework_checks.py
"""
from __future__ import annotations

import pathlib

from cubridwsl import constants, state as state_mod, verify


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
               unicode_strings: bool, *, link_info_unicode: bool = False,
               ansi_target: str | None = None,
               truncate_unicode_path: bool = False) -> bytes:
    """One [MS-SHLLINK] shell link, assembled by hand.

    Only the parts `read_shortcut` reads: the header, a LinkInfo block carrying
    LocalBasePath, and the ARGUMENTS and ICON_LOCATION StringData sections.

    `link_info_unicode` emits the EXTENDED LinkInfo header (0x24), where the
    Unicode offsets exist and are authoritative. `ansi_target` then fills the
    ANSI fields with something different, so a parser reading the wrong pair of
    offsets returns the wrong string instead of the right one by luck.
    `truncate_unicode_path` drops the string's terminator, which is the shape a
    bounded parser must reject and an unbounded one loops on.
    """
    import struct

    has_link_info, has_args, has_icon, is_unicode = 0x2, 0x20, 0x40, 0x80
    flags = has_link_info | has_args | has_icon | (is_unicode if unicode_strings else 0)
    header = (struct.pack("<I", 0x4C) + b"\x01\x14\x02\x00" + b"\x00" * 12
              + struct.pack("<I", flags) + struct.pack("<I", 0) + b"\x00" * 24
              + struct.pack("<IiiI", 0, 0, 1, 0) + b"\x00" * 10)[:76].ljust(76, b"\x00")

    if link_info_unicode:
        fixed = 0x24                             # the extended LinkInfo header
        ansi_base = (ansi_target or target).encode("cp1252") + b"\x00"
        ansi_suffix = b"\x00"
        wide_base = target.encode("utf-16-le") + b"\x00\x00"
        wide_suffix = b"\x00\x00"
        data = ansi_base + ansi_suffix + wide_base + wide_suffix
        link_info = struct.pack(
            "<IIIIIIIII", fixed + len(data), fixed, 1, 0,
            fixed,                                       # LocalBasePathOffset
            0,                                           # network link offset
            fixed + len(ansi_base),                      # CommonPathSuffix
            fixed + len(ansi_base) + len(ansi_suffix),   # ...Unicode
            fixed + len(ansi_base) + len(ansi_suffix) + len(wide_base),
        ) + data
        if truncate_unicode_path:
            # Cut the file inside the Unicode path, so there is no terminator
            # anywhere after it -- not merely a missing one, which the very next
            # field would supply by accident.
            return (header + link_info)[:len(header) + fixed + len(ansi_base)
                                        + len(ansi_suffix)
                                        + len(wide_base) - 4]
    else:
        base = target.encode("cp1252") + b"\x00"
        fixed = 28                               # LinkInfoHeaderSize 0x1C
        link_info = struct.pack("<IIIIIII", fixed + len(base) + 1, 0x1C, 1, 0,
                                fixed, 0, fixed + len(base)) + base + b"\x00"

    def _string(text: str) -> bytes:
        encoded = (text.encode("utf-16-le") if unicode_strings
                   else text.encode("cp1252"))
        return struct.pack("<H", len(text)) + encoded

    return header + link_info + _string(arguments) + _string(icon)


def test_the_shortcut_parser_reads_the_extended_linkinfo_header(tmp_path, note):
    """A LinkInfo header of 0x24 or more carries UNICODE path offsets.

    `_parse_link_info` switches on that size, and the switch is not cosmetic:
    the two pairs of offsets point at different bytes, so reading the wrong pair
    yields a plausible-looking string rather than an error. The link below
    therefore carries a DELIBERATELY WRONG ANSI path -- a parser that ignores
    the extended header returns that, and the assertion names it.

    Bounded, too. A Unicode string with no terminator must raise and be reported
    by `read_shortcut`, never scanned past the end of the buffer: that branch
    cannot use `bytes.index` to bound itself, and a parser that loops is one an
    exception handler cannot rescue.
    """
    target = r"C:\Users\USER\AppData\Local\CUBRID-FOR-WSL\cubrid_tray_app.exe"
    path = tmp_path / "extended.lnk"
    path.write_bytes(_build_lnk(
        target=target, arguments="--minimised", icon=target,
        unicode_strings=True, link_info_unicode=True,
        ansi_target=r"C:\WRONG\ansi-offsets-were-read.exe"))

    shortcut = state_mod.read_shortcut(path)
    note(f"  shortcut   : extended header -> {shortcut.describe()}")
    assert shortcut.error is None, shortcut.error
    assert shortcut.target == target, (
        "the Unicode offsets are authoritative once LinkInfoHeaderSize >= 0x24; "
        f"got {shortcut.target!r}")

    truncated = tmp_path / "truncated.lnk"
    truncated.write_bytes(_build_lnk(
        target=target, arguments="--minimised", icon=target,
        unicode_strings=True, link_info_unicode=True,
        truncate_unicode_path=True))
    broken = state_mod.read_shortcut(truncated)
    note(f"  shortcut   : unterminated UTF-16 path -> {broken.error}")
    assert broken.exists and broken.error and broken.target is None, (
        "an unterminated UTF-16 path must be reported as an error and no "
        "target -- and it must RETURN, because read_shortcut can catch an "
        "exception but not a loop")


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
