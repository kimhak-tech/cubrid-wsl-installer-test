"""THE verification layer's eyes: one reading of what is actually true.

Both drivers read the machine through this module and no other. A second copy
of "is demodb present" drifts from the first and doubles maintenance for no
coverage.

Three properties this module must keep:

* It REPORTS, it does not INTERPRET. Whether the observed state is *correct*
  for a given set of options belongs to verify.py. Here the only question is
  what is true.
* It DEGRADES rather than raises. If WSL is unresponsive the snapshot comes back
  with that field's error recorded and every other field intact, so one hiccup
  does not mask the assertion a test actually cared about.
* Some product state is EVENTUALLY CONSISTENT. demodb creation is dispatched
  asynchronously and can finish after the installer has exited, so use
  `wait_until()` rather than sleeping in a test.

Field shapes come from a real installed machine, not from reading the C++:

    Installed / ImageInstalled / TrayAppInstalled /
    StarterInstalled / DocsInstalled      REG_DWORD, so int 1 -- never "1"
    InstallDir                            carries a TRAILING BACKSLASH
    ImageFile / TrayAppFile /
    TrayAppLinkFile                       bare filenames, relative to InstallDir
"""
from __future__ import annotations

import ntpath
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from . import constants, distro as distro_mod
from .preflight import WINDOWS


def normalize_path(value: str | None) -> str | None:
    """Compare Windows paths without tripping over case or a trailing separator.

    `InstallDir` is stored with one, so a direct string compare against an
    expected path fails for a perfectly correct install. ntpath (not os.path)
    keeps this deterministic and testable off Windows.
    """
    if not value:
        return value
    return ntpath.normcase(ntpath.normpath(str(value)))


def read_key(hive_name: str, path: str) -> dict[str, Any] | None:
    """Every value under one registry key, or None if the key is absent."""
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


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegistryState:
    """The product's own key under HKCU."""

    present: bool
    values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def _flag(self, name: str) -> bool:
        return self.values.get(name) == 1            # REG_DWORD, so int

    @property
    def installed(self) -> bool:
        return self._flag("Installed")

    @property
    def wsl_name(self) -> str | None:
        return self.values.get("WslName")

    @property
    def install_dir(self) -> Path | None:
        raw = self.values.get("InstallDir")
        return Path(str(raw)) if raw else None

    @property
    def desktop_folder(self) -> Path | None:
        raw = self.values.get("UsersDesktopFolder")
        return Path(str(raw)) if raw else None


def read_registry() -> RegistryState:
    """Read the product key. Absent means not installed FOR THIS ACCOUNT."""
    try:
        values = read_key("HKCU", constants.PRODUCT_KEY)
    except Exception as exc:
        return RegistryState(present=False, error=f"{type(exc).__name__}: {exc}")
    if values is None:
        return RegistryState(present=False)
    return RegistryState(present=True, values=values)


# --------------------------------------------------------------------------- #
# Startup entries
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StartupState:
    """Two Run entries, and they behave differently.

    `CUBRID_WSL_Starter` is registered unconditionally; `CUBRID_WSL_TrayApp`
    follows the auto-start option.
    """

    tray_app: str | None
    starter: str | None
    error: str | None = None

    @property
    def tray_app_registered(self) -> bool:
        return self.tray_app is not None

    @property
    def starter_registered(self) -> bool:
        return self.starter is not None


def read_startup() -> StartupState:
    """An absent value means not registered, not an error."""
    try:
        values = read_key("HKCU", constants.RUN_KEY) or {}
    except Exception as exc:
        return StartupState(None, None, error=f"{type(exc).__name__}: {exc}")
    return StartupState(values.get(constants.RUN_VALUE_TRAY),
                        values.get(constants.RUN_VALUE_STARTER))


# --------------------------------------------------------------------------- #
# Apps & Features
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArpState:
    """The bundle's Apps & Features entry. The MSI's is hidden by design."""

    present: bool
    display_name: str | None = None
    display_version: str | None = None
    publisher: str | None = None
    uninstall_string: str | None = None
    location: str | None = None
    count: int = 0
    error: str | None = None


def read_arp(display_name: str = constants.ARP_DISPLAY_NAME) -> ArpState:
    """Find the visible uninstall entry for the bundle.

    `count` is reported so a duplicate left behind by a failed uninstall is
    visible rather than silently taking the first match.
    """
    try:
        return _read_arp(display_name)
    except Exception as exc:
        return ArpState(present=False, error=f"{type(exc).__name__}: {exc}")


def _read_arp(display_name: str) -> ArpState:
    import winreg

    matches: list[ArpState] = []
    for hive_name, base in constants.UNINSTALL_KEYS:
        hive = (winreg.HKEY_CURRENT_USER if hive_name == "HKCU"
                else winreg.HKEY_LOCAL_MACHINE)
        try:
            with winreg.OpenKey(hive, base) as key:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    values = read_key(hive_name, f"{base}\\{sub}") or {}
                    if values.get("DisplayName") != display_name:
                        continue
                    if values.get("SystemComponent") == 1:   # hidden by design
                        continue
                    matches.append(ArpState(
                        present=True,
                        display_name=values.get("DisplayName"),
                        display_version=values.get("DisplayVersion"),
                        publisher=values.get("Publisher"),
                        uninstall_string=values.get("UninstallString"),
                        location=f"{hive_name}\\{base}\\{sub}"))
        except OSError:
            continue

    if not matches:
        return ArpState(present=False)
    first = matches[0]
    return ArpState(present=True, display_name=first.display_name,
                    display_version=first.display_version,
                    publisher=first.publisher,
                    uninstall_string=first.uninstall_string,
                    location=first.location, count=len(matches))


# --------------------------------------------------------------------------- #
# Desktop shortcuts
#
# A .lnk that exists is not a .lnk that works. INS-001 requires each shortcut to
# "resolve to a target that exists on disk with the correct icon -- not merely
# present by filename", so the file is PARSED.
#
# Parsed in pure Python rather than through WScript.Shell, deliberately. COM
# would mean pywin32 or comtypes, and comtypes arrives with pywinauto -- a
# UI-track-only extra. The shortcuts are asserted on the SILENT track too
# (INS-002 applies INS-001's whole post-install set), and that track is defined
# by the workbook as having "no UI dependency". A COM-based reader would quietly
# give it one.
#
# The format is Microsoft's [MS-SHLLINK]. Only three fields are needed: the
# target (LinkInfo -> LocalBasePath + CommonPathSuffix), the arguments and the
# icon location (both StringData). Everything else is skipped by length.
# --------------------------------------------------------------------------- #
_LNK_MAGIC_HEADER_SIZE = 0x0000004C

# LinkFlags bits that decide what follows the header, and in what order.
_HAS_LINK_TARGET_ID_LIST = 0x00000001
_HAS_LINK_INFO = 0x00000002
_HAS_NAME = 0x00000004
_HAS_RELATIVE_PATH = 0x00000008
_HAS_WORKING_DIR = 0x00000010
_HAS_ARGUMENTS = 0x00000020
_HAS_ICON_LOCATION = 0x00000040
_IS_UNICODE = 0x00000080
_FORCE_NO_LINK_INFO = 0x00000100

# LinkInfoFlags
_VOLUME_ID_AND_LOCAL_BASE_PATH = 0x00000001


@dataclass(frozen=True)
class Shortcut:
    """One desktop shortcut, resolved.

    `target`, `arguments` and `icon_location` are None when the file is absent
    or could not be parsed -- never guessed. `error` says which.
    """

    path: Path | None
    exists: bool
    target: str | None = None
    arguments: str | None = None
    icon_location: str | None = None
    error: str | None = None
    # Why `target` is None even though the file parsed. Windows omits the
    # LinkInfo LocalBasePath when it could not bind the path it was given to a
    # real filesystem object at save time, which is what a malformed path does.
    unresolved_reason: str | None = None

    @property
    def target_exists(self) -> bool:
        return bool(self.target) and Path(self.target).is_file()

    @property
    def icon_path(self) -> str | None:
        """The icon file, with the trailing `,<index>` stripped.

        The product calls SetIconLocation(exePath, 0), so Windows stores
        "C:\\path\\to.exe,0" or just the path. Splitting on the LAST comma is
        safe: a Windows path cannot contain one.
        """
        if not self.icon_location:
            return None
        head, sep, tail = self.icon_location.rpartition(",")
        if sep and tail.strip().lstrip("-").isdigit():
            return head
        return self.icon_location

    @property
    def icon_exists(self) -> bool:
        icon = self.icon_path
        return bool(icon) and Path(icon).is_file()

    @property
    def icon_matches_target(self) -> bool:
        """The product sets the icon to the target executable itself.

        `CreateShortcut(..., iconPath)` is always called with the same string
        passed as `exePath` -- see src/CubridCustomActions.cpp, both shortcuts.
        So an icon pointing anywhere else is a product change, not a preference.
        """
        icon, target = self.icon_path, self.target
        if not icon or not target:
            return False
        return normalize_path(icon) == normalize_path(target)

    @property
    def stored_paths(self) -> tuple[str, ...]:
        """Every path string this link actually carries."""
        return tuple(p for p in (self.target, self.icon_location) if p)

    @property
    def doubled_separator(self) -> str | None:
        """A stored path with a doubled separator, or None.

        Checked on the RAW string, never a normalised one -- ntpath.normpath
        collapses `\\` silently, so normalising first would hide exactly the
        defect this looks for.

        Why it matters here: CubridCustomActions.cpp builds the tray target as
        `installDir + "\\" + trayAppFile`, and InstallDir is stored WITH a
        trailing backslash, so the result carries `...\\CUBRID-FOR-WSL\\\\file.exe`.
        The WSL shortcut is built as `wslPath + "\\wsl.exe"` from a path with no
        trailing separator, which is why only one of the two is affected.
        """
        for path in self.stored_paths:
            head = path[2:] if len(path) > 2 and path[1] == ":" else path
            if "\\\\" in head:
                return path
        return None

    @property
    def resolution(self) -> bool | str:
        """True when the target resolved, False when there is no link at all,
        and a DESCRIPTION when the link exists but names no target.

        Three values rather than a bool because the third case is the one worth
        reading: a bare False says the check failed, while the description says
        the link carries no LocalBasePath and shows the malformed path that is
        the likeliest reason.
        """
        if not self.exists:
            return False
        if self.error:
            return f"<unreadable: {self.error}>"
        if self.target:
            return True
        reason = self.unresolved_reason or "the link names no target"
        malformed = self.doubled_separator
        if malformed:
            reason += (f"; its stored path {malformed!r} carries a DOUBLED "
                       "separator, which is what stops Windows binding it to a "
                       "real file. Built by CubridCustomActions.cpp as "
                       '`installDir + "\\" + trayAppFile` -- and InstallDir is '
                       "stored with a trailing backslash")
        return f"<{reason}>"

    def describe(self) -> str:
        if not self.exists:
            return f"{self.path} (absent)"
        return (f"{self.path} -> {self.target!r} args={self.arguments!r} "
                f"icon={self.icon_location!r}")


def read_shortcut(path: Path | None) -> Shortcut:
    """Parse one .lnk. Never raises."""
    if path is None:
        return Shortcut(None, False, error="path could not be derived")
    try:
        if not path.is_file():
            return Shortcut(path, False)
        return _parse_lnk(path, path.read_bytes())
    except Exception as exc:
        return Shortcut(path, True, error=f"{type(exc).__name__}: {exc}")


def _parse_lnk(path: Path, raw: bytes) -> Shortcut:
    import struct

    if len(raw) < 76:
        return Shortcut(path, True, error=f"too short to be a .lnk ({len(raw)} bytes)")
    header_size, = struct.unpack_from("<I", raw, 0)
    if header_size != _LNK_MAGIC_HEADER_SIZE:
        return Shortcut(path, True,
                        error=f"not a shell link (HeaderSize=0x{header_size:08X})")
    flags, = struct.unpack_from("<I", raw, 20)
    unicode_strings = bool(flags & _IS_UNICODE)
    offset = 76

    if flags & _HAS_LINK_TARGET_ID_LIST:
        id_list_size, = struct.unpack_from("<H", raw, offset)
        offset += 2 + id_list_size

    target: str | None = None
    if (flags & _HAS_LINK_INFO) and not (flags & _FORCE_NO_LINK_INFO):
        target, offset = _parse_link_info(raw, offset)

    # StringData, in this fixed order. Each is CountCharacters (2 bytes) then
    # that many characters -- two bytes each when the IsUnicode flag is set.
    strings: dict[str, str] = {}
    for name, bit in (("name", _HAS_NAME),
                      ("relative_path", _HAS_RELATIVE_PATH),
                      ("working_dir", _HAS_WORKING_DIR),
                      ("arguments", _HAS_ARGUMENTS),
                      ("icon_location", _HAS_ICON_LOCATION)):
        if not flags & bit:
            continue
        count, = struct.unpack_from("<H", raw, offset)
        offset += 2
        width = 2 if unicode_strings else 1
        chunk = raw[offset:offset + count * width]
        offset += count * width
        # cp1252 rather than mbcs for the ANSI case: the product writes these
        # through IShellLinkA, and cp1252 keeps the parser testable off Windows
        # where "mbcs" does not exist.
        strings[name] = (chunk.decode("utf-16-le", errors="replace")
                         if unicode_strings
                         else chunk.decode("cp1252", errors="replace"))

    return Shortcut(
        path, True, target=target,
        arguments=strings.get("arguments"),
        icon_location=strings.get("icon_location"),
        unresolved_reason=(None if target else
                           "the LinkInfo block carries no LocalBasePath "
                           "(VolumeIDAndLocalBasePath is not set)"))


def _parse_link_info(raw: bytes, start: int) -> tuple[str | None, int]:
    """The LinkInfo block: LocalBasePath + CommonPathSuffix is the target."""
    import struct

    size, header_size, info_flags = struct.unpack_from("<III", raw, start)
    if not info_flags & _VOLUME_ID_AND_LOCAL_BASE_PATH:
        # No VolumeID/LocalBasePath. Either a network link, or -- far more
        # likely for this product -- Windows could not bind the path it was
        # given to a filesystem object when the link was saved.
        return None, start + size

    def _cstring(at: int, wide: bool) -> str:
        """One NUL-terminated string, BOUNDED by the end of the file.

        A truncated or malformed .lnk must FAIL, never hang: `read_shortcut`
        catches an exception and reports it, and it cannot catch a loop. The
        ANSI branch is bounded for free -- `bytes.index` raises when there is no
        terminator. The wide branch has to bound itself, because a slice taken
        past the end returns b"" and b"" never equals the terminator, so the
        obvious loop runs forever.
        """
        if not 0 <= at < len(raw):
            raise ValueError(f"string offset {at} lies outside the file")
        if wide:
            end = at
            while end + 2 <= len(raw) and raw[end:end + 2] != b"\x00\x00":
                end += 2
            if end + 2 > len(raw):
                raise ValueError(
                    "a UTF-16 string runs off the end of the file with no "
                    "terminator")
            return raw[at:end].decode("utf-16-le", errors="replace")
        end = raw.index(b"\x00", at)
        return raw[at:end].decode("cp1252", errors="replace")

    # The Unicode offsets only exist on the extended header (>= 0x24), and are
    # authoritative when present.
    if header_size >= 0x24:
        base_offset, suffix_offset = struct.unpack_from("<II", raw, start + 28)
        base = _cstring(start + base_offset, True)
        suffix = _cstring(start + suffix_offset, True)
    else:
        base_offset, = struct.unpack_from("<I", raw, start + 16)
        suffix_offset, = struct.unpack_from("<I", raw, start + 24)
        base = _cstring(start + base_offset, False)
        suffix = _cstring(start + suffix_offset, False)
    return (base + suffix) or None, start + size


def _shortcut_as_dict(shortcut: Shortcut) -> dict[str, Any]:
    """Everything about one shortcut, including the derived readings -- so a
    failure can be diagnosed from the report without reinstalling."""
    return {**shortcut.__dict__, "path": str(shortcut.path),
            "resolution": shortcut.resolution,
            "target_exists": shortcut.target_exists,
            "icon_path": shortcut.icon_path,
            "icon_is_the_target": shortcut.icon_matches_target,
            "doubled_separator": shortcut.doubled_separator}


@dataclass(frozen=True)
class ShortcutState:
    """Both desktop shortcuts, resolved."""

    distro: Shortcut
    tray: Shortcut
    error: str | None = None


def read_shortcuts(registry: RegistryState) -> ShortcutState:
    """Derive both paths from the registry -- never from a hard-coded name, so a
    custom-name install still resolves -- then parse each file."""
    try:
        desktop = registry.desktop_folder
        wsl_name = registry.wsl_name
        tray_name = registry.values.get("TrayAppLinkFile") or "cubrid_tray_app.lnk"
        distro_link = (desktop / f"{wsl_name}.lnk") if (desktop and wsl_name) else None
        tray_link = (desktop / str(tray_name)) if desktop else None
        return ShortcutState(distro=read_shortcut(distro_link),
                             tray=read_shortcut(tray_link))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return ShortcutState(Shortcut(None, False, error=error),
                             Shortcut(None, False, error=error), error=error)


# --------------------------------------------------------------------------- #
# The Tray application
#
# Two independent probes, neither needing a UI-automation library. The MUTEX
# exists exactly while the process does. The WINDOW (class CUBRIDTrayApp) is
# HIDDEN, so FindWindow is the only way to it. They are reported separately
# because a disagreement is itself a finding: a mutex with no window is a Tray
# that started and failed to initialise.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TrayState:
    """Whether the Tray process is up, by two independent readings."""

    running: bool | None
    window_present: bool | None
    error: str | None = None


_SYNCHRONIZE = 0x00100000                 # the least OpenMutexW can ask for
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5


def read_tray() -> TrayState:
    """Is the Tray running?

    `running` is None, never False, when the answer could not be established.
    A probe that reported its own failure as "not running" would let a case
    asserting the Tray is DOWN pass for the wrong reason.

    ERROR_ACCESS_DENIED counts as RUNNING: a mutex you are not allowed to open
    is a mutex that exists.
    """
    if not WINDOWS:
        return TrayState(None, None, error="not Windows")
    try:
        return TrayState(running=_mutex_exists(constants.TRAY_MUTEX),
                         window_present=_tray_window_exists())
    except Exception as exc:
        return TrayState(None, None, error=f"{type(exc).__name__}: {exc}")


def _mutex_exists(name: str) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # A HANDLE is POINTER-sized. ctypes defaults every function's restype to
    # c_int, which truncates one on 64-bit Windows -- and the truncated value is
    # then what CloseHandle is handed, so the real handle stays open for the
    # life of the run. Declared for the same reason FindWindowW is below.
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                    wintypes.LPCWSTR)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    error = ctypes.get_last_error()
    if error == _ERROR_FILE_NOT_FOUND:
        return False
    if error == _ERROR_ACCESS_DENIED:
        return True
    raise OSError(error, f"OpenMutexW({name!r}) failed with error {error}")


def _tray_window_exists() -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    return bool(user32.FindWindowW(constants.TRAY_WINDOW_CLASS,
                                   constants.TRAY_WINDOW_TITLE))


def _service_sections(output: str) -> dict[str, list[str]]:
    """`cubrid service status` split into its per-component sections.

    Every line starting `@ cubrid ` opens a section; the lines after it are its
    body. Names match by PREFIX because the manager's header reads
    "@ cubrid manager server status".

    Components not named in constants.SERVICE_COMPONENTS are dropped. On build
    11.4-1.0.0-0003 that is `pl` and `gateway`: neither is named by INS-001 and
    neither is in CUBRID's default `service=` line, so both being down is
    configuration rather than a finding.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in output.splitlines():
        line = raw.strip().lower()
        if line.startswith(constants.SERVICE_SECTION_PREFIX):
            rest = line[len(constants.SERVICE_SECTION_PREFIX):]
            current = next((c for c in constants.SERVICE_COMPONENTS
                            if rest.startswith(c)), None)
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None and line:
            sections[current].append(line)
    return sections


def parse_running_brokers(output: str) -> tuple[str, ...]:
    """The brokers listed as running, by name.

    A running broker service prints a TABLE, not a sentence:

          NAME              PID  PORT   AS  JQ ...
        =============================================
        * query_editor       97 30000    5   0 ...
        * broker1           116 33000    5   0 ...

    A data row is identified by its shape -- a name followed by a numeric PID --
    rather than by position, so a column being added or reordered does not
    silently stop finding brokers. The leading `*` marks a running broker and is
    stripped; the header and the `====` rule fail the numeric-PID test.
    """
    names: list[str] = []
    for line in _service_sections(output).get("broker", []):
        tokens = line.lstrip("*+= ").split()
        if len(tokens) >= 2 and tokens[1].isdigit():
            names.append(tokens[0])
    return tuple(names)


def parse_started_databases(output: str) -> tuple[str, ...]:
    """The databases `cubrid service status` lists as started, by name.

    The SERVER section names one line per started database:

        @ cubrid server status
         Server demodb (rel 11.4, pid 1234)

    An EMPTY section is the normal state after a default install -- stock
    `cubrid.conf` leaves `server=` commented out, so nothing starts a database
    and `parse_service_status` reports the server component False. This reads
    WHICH databases are up, which is what a case needs in order to start one,
    use it, and put the machine back as it found it.

    Names come back LOWER-CASED, because the section splitter folds case to
    match component headers. Every database this suite names is lower case;
    compare case-insensitively if that ever stops being true.
    """
    names: list[str] = []
    for line in _service_sections(output).get("server", []):
        if not line.startswith(constants.SERVICE_SERVER_RUNNING_PREFIX):
            continue
        tokens = line.split()
        if len(tokens) >= 2:
            names.append(tokens[1])
    return tuple(names)


def parse_service_command(output: str) -> dict[str, bool | None]:
    """`cubrid service start` / `stop` output, as a per-component verdict.

    Same section shape as `status`, a different body:

        @ cubrid master stop
        ++ cubrid master stop: success

    OPS-001 requires the stop and start ACTIONS to be confirmed per component
    rather than inferred from an exit code, and this is the product stating what
    it did. Failure is checked FIRST so a section carrying both markers resolves
    to failed.

    A section with neither marker is None -- unknown, NOT failed. The server
    section is legitimately empty when no database was started, and reporting
    that as a failed stop would be a fabricated defect.
    """
    verdicts: dict[str, bool | None] = {}
    for component, lines in _service_sections(output).items():
        body = " ".join(lines)
        if constants.SERVICE_COMMAND_FAILURE_MARKER in body:
            verdicts[component] = False
        elif constants.SERVICE_COMMAND_SUCCESS_MARKER in body:
            verdicts[component] = True
        else:
            verdicts[component] = None
    return verdicts


def parse_service_status(output: str) -> dict[str, bool | None]:
    """Split `cubrid service status` into a per-component verdict.

    INS-001 asserts the components individually, and the product's own code is
    no help: both the Tray and the Starter decide the whole service is up from
    the master line alone.

    Rules, in order, within each section:

    * "is not running" wins over everything -- a section carrying both lines
      must resolve to not-running;
    * "is running" means running;
    * the SERVER section prints neither, listing `Server <db> (rel ...)` per
      started database, so that is its positive signal -- and an EMPTY server
      section is False, not None: no lines means no database is started;
    * the BROKER section prints neither either; it is running when it lists at
      least one broker (see parse_running_brokers). A broker service with an
      empty table serves nothing, so an empty table is not "running".

    Anything else is None -- unknown, NOT false. A parser that guessed here
    would report a component as DOWN on the strength of the framework failing
    to understand the output, which is a fabricated defect report.
    """
    verdicts: dict[str, bool | None] = {}
    for component, lines in _service_sections(output).items():
        body = " ".join(lines)
        if constants.SERVICE_NOT_RUNNING_MARKER in body:
            verdicts[component] = False
        elif constants.SERVICE_IS_RUNNING_MARKER in body:
            verdicts[component] = True
        elif component == "server":
            verdicts[component] = any(
                line.startswith(constants.SERVICE_SERVER_RUNNING_PREFIX)
                for line in lines)
        elif component == "broker":
            verdicts[component] = bool(parse_running_brokers(output))
        else:
            verdicts[component] = None
    return verdicts


# --------------------------------------------------------------------------- #
# CUBRID, from inside the distribution
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CubridState:
    """What is true inside the distribution."""

    distro_present: bool
    distro_running: bool
    distro_version: int | None
    cubrid_version: str | None
    demodb_present: bool | None
    databases: tuple[str, ...] = ()
    # Per-component verdicts from `cubrid service status`, and the raw text they
    # were read from. INS-001 asserts the components individually, and a failure
    # needs the output in the report -- reproducing it means reinstalling.
    service_components: dict[str, bool | None] = field(default_factory=dict)
    # The brokers listed as running, by name -- query_editor and broker1 on a
    # default install. A broker service that is up with no brokers running
    # serves nothing, so the names are checked, not just the section.
    brokers: tuple[str, ...] = ()
    service_status_raw: str = ""
    errors: dict[str, str] = field(default_factory=dict)


def read_cubrid(settings: dict[str, Any], name: str | None, *,
                include_guest: bool = True) -> CubridState:
    """Read CUBRID inside the named distribution.

    `name` comes from the registry -- what was ACTUALLY installed. There is
    deliberately no configured fallback: a name in settings could point this at
    a different distribution than the one the product created.

    Records a per-command error instead of raising, so one unresponsive command
    does not hide everything else that was readable.

    `include_guest=False` reads only what `wsl -l -v` reports -- whether the
    distribution exists, its state and its version -- and skips the three
    commands run INSIDE it. That matters while a distribution is being
    unregistered: each in-guest command can hang to its own timeout against a
    distro that is going away, so a poll loop asking "is it gone yet" would take
    up to three minutes per iteration to answer a question `wsl -l -v` answers
    in one call.
    """
    cfg = settings.get("distro", {})
    user = cfg.get("user") or None
    env_setup = cfg.get("env_setup", "")
    timeout = settings.get("timeouts", {}).get("wsl_command_seconds", 60)
    errors: dict[str, str] = {}

    try:
        entry = distro_mod.find(name or "")
    except Exception as exc:
        return CubridState(False, False, None, None, None,
                           errors={"distro": f"{type(exc).__name__}: {exc}"})
    if entry is None:
        return CubridState(False, False, None, None, None)

    present = CubridState(distro_present=True,
                          distro_running=entry.state.casefold() == "running",
                          distro_version=entry.version,
                          cubrid_version=None, demodb_present=None)
    if not include_guest:
        return present

    def _run(label: str, command: str) -> str | None:
        try:
            result = distro_mod.run(entry.name, command, user=user,
                                    env_setup=env_setup, timeout=timeout)
            if not result.ok:
                errors[label] = f"rc={result.returncode} {result.stderr[:200]}"
            return result.stdout
        except Exception as exc:
            errors[label] = f"{type(exc).__name__}: {exc}"
            return None

    status = _run("service_status", "cubrid service status")
    service_components: dict[str, bool | None] = {}
    brokers: tuple[str, ...] = ()
    if status is not None:
        service_components = parse_service_status(status)
        brokers = parse_running_brokers(status)

    version = _run("cubrid_rel", "cubrid_rel")

    databases: tuple[str, ...] = ()
    demodb_present: bool | None = None
    # `|| true` so that "there is no databases.txt" is an ANSWER (no databases)
    # rather than a recorded command error. Without it, a scenario with
    # CREATE_DEMODB=0 records a spurious error, and cubrid.errors is compared.
    listing = _run("databases",
                   'cat "$CUBRID_DATABASES/databases.txt" 2>/dev/null '
                   '|| cat "$CUBRID/databases/databases.txt" 2>/dev/null '
                   '|| true')
    if listing is not None:
        databases = tuple(line.split()[0] for line in listing.splitlines()
                          if line.strip() and not line.strip().startswith("#"))
        demodb_present = "demodb" in databases

    return CubridState(distro_present=True,
                       distro_running=entry.state.casefold() == "running",
                       distro_version=entry.version,
                       cubrid_version=(version or "").strip() or None,
                       demodb_present=demodb_present,
                       databases=databases,
                       service_components=service_components,
                       brokers=brokers,
                       service_status_raw=(status or ""),
                       errors=errors)

# --------------------------------------------------------------------------- #
# The environment a real user session gets
#
# Deliberately NOT part of snapshot(). `wait_until()` calls snapshot() every few
# seconds while an install settles, and this costs an extra `wsl ... bash -lc`
# round-trip each time -- to answer a question that cannot change while the
# framework watches. It is read ONCE, by the install fixture, after the machine
# has settled.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LoginEnvironment:
    """$CUBRID, $CUBRID_DATABASES and $PATH as a FRESH login shell sees them.

    Two deliberate choices, and the reading is worthless without either:

    * a **login** shell (`bash -lc`). The image writes ~/.cubrid.sh and has
      ~/.bash_profile source it, and .bash_profile is read by login shells --
      which is what "read in a FRESH shell session, not the install-time
      session" means in practice;
    * **no `env_setup` prefix.** Everywhere else the framework mirrors the
      product and prepends `. ~/.cubrid.sh`. Doing it here would source the file
      ourselves and answer a different question: the environment would look
      correct even if nothing set it up for a real user session.

    `carriage_return` is the specific defect this exists to catch. A CRLF
    ~/.cubrid.sh makes every exported value end in a CR, so $CUBRID/bin and
    $CUBRID_DATABASES both name directories that do not exist -- while the
    install still reports success. This has happened; it is not hypothetical.
    """

    read: bool
    values: dict[str, str] = field(default_factory=dict)
    carriage_return: bool | None = None
    error: str | None = None

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    @property
    def path_entries(self) -> tuple[str, ...]:
        return tuple((self.values.get("PATH") or "").split(":"))


def read_login_environment(settings: dict[str, Any],
                           name: str | None) -> LoginEnvironment:
    """Open one fresh login shell and read the environment out of it."""
    if not name:
        return LoginEnvironment(read=False,
                                error="the registry recorded no WslName")
    try:
        result = distro_mod.run(
            name,
            'echo "CUBRID=$CUBRID"; '
            'echo "CUBRID_DATABASES=$CUBRID_DATABASES"; '
            'echo "PATH=$PATH"',
            user=(settings.get("distro", {}).get("user") or None),
            login=True,                  # reads ~/.bash_profile
            env_setup="",                # deliberately NOT sourced by us
            timeout=settings.get("timeouts", {}).get("wsl_command_seconds", 60))
    except Exception as exc:
        return LoginEnvironment(read=False, error=f"{type(exc).__name__}: {exc}")

    if not result.ok:
        return LoginEnvironment(
            read=False,
            error=f"a fresh login shell inside {name!r} failed: "
                  f"rc={result.returncode} {result.stderr[:300]}")

    values = dict(line.split("=", 1) for line in result.stdout.splitlines()
                  if "=" in line)
    return LoginEnvironment(read=True, values=values,
                            carriage_return="\r" in result.stdout)


# --------------------------------------------------------------------------- #
# The whole machine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MachineState:
    """One reading of the whole machine."""

    registry: RegistryState
    startup: StartupState
    arp: ArpState
    shortcuts: ShortcutState
    tray: TrayState
    cubrid: CubridState
    # Read once by the install fixture, not by snapshot(): see
    # read_login_environment() for why it stays out of the poll loop. A state
    # that never had it carries read=False, and the check over it says so
    # rather than reporting empty values as wrong ones.
    login_environment: LoginEnvironment = field(
        default_factory=lambda: LoginEnvironment(
            read=False, error="not read -- snapshot() does not read the login "
                              "environment; the install fixture does"))

    def with_login_environment(self,
                               environment: LoginEnvironment) -> "MachineState":
        """The same reading, plus the login environment.

        A copy, not a mutation: MachineState is frozen because a snapshot is a
        statement about one moment, and a test that could edit the state it was
        handed is a test that can make itself pass.
        """
        return replace(self, login_environment=environment)

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry": {"present": self.registry.present,
                         "values": self.registry.values,
                         "error": self.registry.error},
            "startup": {"tray_app": self.startup.tray_app,
                        "starter": self.startup.starter,
                        "error": self.startup.error},
            "arp": self.arp.__dict__,
            "tray": self.tray.__dict__,
            "login_environment": self.login_environment.__dict__,
            "shortcuts": {
                "distro": _shortcut_as_dict(self.shortcuts.distro),
                "tray": _shortcut_as_dict(self.shortcuts.tray),
                "error": self.shortcuts.error},
            "cubrid": {**self.cubrid.__dict__,
                       "databases": list(self.cubrid.databases)},
        }


def snapshot(settings: dict[str, Any], *,
             include_guest: bool = True) -> MachineState:
    """One reading of the whole machine. Never raises.

    That is a promise `wait_until` depends on: it calls this in a loop for up to
    fifteen minutes while an install or an uninstall is changing the very keys
    being read, and one transient failure must not end the wait. Every reader
    below records its own error and returns a partial answer instead.
    """
    registry = read_registry()
    return MachineState(registry=registry,
                        startup=read_startup(),
                        arp=read_arp(),
                        shortcuts=read_shortcuts(registry),
                        tray=read_tray(),
                        cubrid=read_cubrid(settings, registry.wsl_name,
                                           include_guest=include_guest))


# The version of the report `MachineState.as_dict()` produces.
#
# INS-002 diffs its machine against a state-wizard.json that may have been
# written by an EARLIER run, and `diff_reports` renders a key one side does not
# have as `<absent>`. So a report written by a different version of this module
# does not read as "no baseline available" -- it reads as a wall of INS-002
# findings against a healthy install, which is worse than having no baseline at
# all. `conftest._usable_reference` refuses a baseline whose run.json does not
# carry this exact number.
#
# BUMP IT whenever `as_dict()` gains, loses or renames a field. The cost of
# forgetting is one confusing run; the cost of bumping unnecessarily is one
# wizard install.
REPORT_SCHEMA = 1

# Fields that legitimately differ between two installs of the same bundle, and
# so must be excluded from a state-to-state diff. Keep this list SHORT and
# justified: every entry is a fact INS-002 stops checking.
DIFF_IGNORE = (
    # Contains process IDs. The components and broker NAMES are compared; the
    # PIDs behind them cannot be equal across two installs and mean nothing.
    "cubrid.service_status_raw",
    # Per-run command failures, already reported by the checks themselves.
    "cubrid.errors",
)


def diff_reports(left: dict[str, Any], right: dict[str, Any], *,
                 ignore: tuple[str, ...] = DIFF_IGNORE) -> list[str]:
    """Diff two state REPORTS -- `as_dict()` output, live or loaded from JSON.

    Separate from `diff` so INS-002 can compare against a snapshot recovered
    from an earlier run's state-wizard.json without re-provisioning the machine
    that produced it. JSON round-trips tuples to lists, so both sides are
    normalised before comparing; otherwise every tuple field would read as a
    difference and the diff would be noise.
    """
    differences: list[str] = []

    def _normalise(value: Any) -> Any:
        if isinstance(value, tuple):
            return [_normalise(v) for v in value]
        if isinstance(value, list):
            return [_normalise(v) for v in value]
        if isinstance(value, dict):
            return {k: _normalise(v) for k, v in value.items()}
        return value

    def _walk(path: str, a: Any, b: Any) -> None:
        if path in ignore:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                child = f"{path}.{key}" if path else str(key)
                _walk(child, a.get(key, "<absent>"), b.get(key, "<absent>"))
            return
        if _normalise(a) != _normalise(b):
            differences.append(f"{path}: {a!r} != {b!r}")

    _walk("", left, right)
    return differences


def diff(left: MachineState, right: MachineState, *,
         ignore: tuple[str, ...] = DIFF_IGNORE) -> list[str]:
    """Every field where two machines disagree, as dotted paths.

    This is what INS-002 is FOR. The workbook is explicit: "assert by comparing
    state snapshots, not by re-listing the assertions" -- re-listing INS-001's
    assertions against a silent install would recreate exactly the duplication
    the matrix was cleaned up to remove. A diff instead catches divergence
    nobody thought to write a check for, which is the class of defect an
    undocumented default produces.

    Compares the REPORT form (`as_dict`) rather than the objects, so anything
    that reaches the JSON artefact is compared and nothing can silently drop
    out of the comparison by being unserialisable.
    """
    return diff_reports(left.as_dict(), right.as_dict(), ignore=ignore)


def wait_until(predicate: Callable[[MachineState], bool],
               settings: dict[str, Any], *, timeout: float = 120,
               interval: float = 3.0,
               description: str = "condition",
               include_guest: bool = True) -> MachineState:
    """Poll until a predicate holds, then return the snapshot that satisfied it.

    Needed because the installer dispatches demodb creation asynchronously and
    can exit before the database exists. Tests express the condition; they never
    sleep.

    Pass `include_guest=False` when the predicate does not read anything from
    inside the distribution -- see `read_cubrid`, which explains why that is
    worth caring about during an uninstall.
    """
    deadline = time.time() + timeout
    latest = snapshot(settings, include_guest=include_guest)
    while True:
        if predicate(latest):
            return latest
        if time.time() >= deadline:
            raise TimeoutError(f"{description} was not satisfied within "
                               f"{timeout:.0f}s. Last state: {latest.as_dict()}")
        time.sleep(interval)
        latest = snapshot(settings, include_guest=include_guest)
