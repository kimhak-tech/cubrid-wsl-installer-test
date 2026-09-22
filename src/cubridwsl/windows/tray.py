"""The CUBRID WSL Tray application: as a process, as a file, and as a UI to drive.

"Is it running" needs no UI-automation library: the Tray's MUTEX exists exactly
while the process does -- the Tray creates it at startup and Windows destroys
it when the process exits, so its existence IS the answer.

Driving it -- the notification-area menu, the tooltip, About and Guide -- does
need the real UI. The menu cannot be read cross-process: the Tray builds it in
`ShowContextMenu()` and destroys it before returning, so it exists only while
it is open, and every read here opens it first.

The menu is opened with the message the Shell sends the Tray on a right-click
(`WM_TRAYICON` / `WM_RBUTTONUP`) and chosen from with the `WM_COMMAND` a click
on an item produces, so the product runs exactly the code a user's click runs.
The physical click itself is NOT exercised: real input into the Windows 11
hidden-icons flyout did not reliably reach the icon.

The driving functions perform actions and expose UI facts. They never judge
CUBRID's state: a case checks that through `wsl/cubrid.py`, so the Tray cannot
vouch for its own actions.
"""
from __future__ import annotations

import ctypes
import html
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import constants
from . import registry


class ProbeError(RuntimeError):
    """The Tray could not be probed at all -- Windows refused the question.

    RAISED rather than reported as "not running", and that choice is the whole
    reason this class exists. A case asserting the Tray is GONE would pass on a
    probe that simply failed, which is a green result for a machine nobody
    looked at. A framework fault should stop the run loudly; only the PRODUCT
    gets to fail a test.

    In practice this never fires: the mutex probe treats "no such mutex" as not
    running and "access denied" as running, so only an unexpected Win32 error
    reaches here.
    """


class TrayError(RuntimeError):
    """The Tray window, popup menu, or expected control could not be used."""


_SYNCHRONIZE = 0x00100000                 # the least OpenMutexW can ask for
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5


# =========================================================================== #
# The process and the file
# =========================================================================== #
def is_running() -> bool:
    """Is the Tray process up? Raises ProbeError if that cannot be established.

    Always a plain True or False, so every caller reads the same way:

        assert not tray.is_running(), "the Tray is still running"
    """
    try:
        return _mutex_exists(constants.TRAY_MUTEX)
    except Exception as exc:
        raise ProbeError(
            f"could not probe the Tray mutex {constants.TRAY_MUTEX!r}: "
            f"{type(exc).__name__}: {exc}") from exc


def binary_path() -> Path | None:
    """Where the product says its Tray executable is, while it is installed.

    Read BEFORE an uninstall: the registry value that names it is one of the
    things the uninstall removes.
    """
    return registry.tray_binary_path()


def binary_exists(path: Path | None) -> bool:
    """Is the Tray binary still on disk at the path the product recorded?

    Takes the path rather than looking it up, so it still answers after the
    registry key that named it has gone -- which is exactly when it is asked.
    """
    return bool(path) and Path(path).is_file()


def _mutex_exists(name: str) -> bool:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # A HANDLE is POINTER-sized. ctypes defaults every function's restype to
    # c_int, which truncates one on 64-bit Windows -- and the truncated value
    # is then what CloseHandle is handed, so the real handle stays open for the
    # life of the run.
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
    # A mutex you are not allowed to open is a mutex that EXISTS.
    if error == _ERROR_ACCESS_DENIED:
        return True
    raise OSError(error, f"OpenMutexW({name!r}) failed with error {error}")


def stop() -> bool:
    """Stop a running Tray. True if one was running.

    Needed before a CLEANUP uninstall: by the second run of any suite there is
    a Tray holding the install directory open, and a process with a handle on
    that directory is the likeliest reason for a leftover folder -- which would
    then read as a product defect rather than as our own doing.

    Asks the Tray to Exit first and kills it only if it does not. A KILLED Tray
    never removes its notification icon, and the ghost it leaves keeps showing
    its last tooltip -- which a later tooltip read could mistake for the live
    Tray's.

    NEVER call this before an uninstall that is under test. Whether the product
    copes with its own running Tray is exactly what LCM-001 asks.
    """
    hwnd = window() if hasattr(ctypes, "windll") else None
    if hwnd:
        _user32().PostMessageW(hwnd, _WM_COMMAND, constants.TRAY_EXIT_COMMAND, 0)
        if wait_until_exited():
            return True
    try:
        result = subprocess.run(["taskkill", "/F", "/IM", constants.TRAY_EXE],
                                capture_output=True, timeout=30)
    except Exception:
        return False
    # taskkill returns 128 when no such process is running, which is the normal
    # case and not a failure.
    return result.returncode == 0


# =========================================================================== #
# Driving the UI
# =========================================================================== #
_WM_TRAYICON = 0x0401  # WM_USER + 1 in cubrid_tray_app.h
_WM_RBUTTONUP = 0x0205
_WM_COMMAND = 0x0111
_WM_CLOSE = 0x0010
_WM_CANCELMODE = 0x001F
_WM_KEYDOWN = 0x0100
_SMTO_ABORTIFHUNG = 0x0002
_MN_GETHMENU = 0x01E1
_VK_ESCAPE = 0x1B
_VK_CONTROL = 0x11
_VK_W = 0x57
_KEYEVENTF_KEYUP = 0x0002
_MF_BYPOSITION = 0x00000400
_MF_DISABLED = 0x00000002
_MF_GRAYED = 0x00000001
_POPUP_MENU_CLASS = "#32768"
_BROWSER_CLASSES = {"Chrome_WidgetWin_1", "MozillaWindowClass",
                    "ApplicationFrameWindow"}


@dataclass(frozen=True)
class MenuItem:
    text: str
    command: int                             # the WM_COMMAND id a click sends
    enabled: bool


@dataclass(frozen=True)
class AboutInfo:
    text: str
    window: int


def _user32():
    if not hasattr(ctypes, "windll"):
        raise TrayError("driving the CUBRID Tray requires Windows")
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _poll(predicate: Callable[[], bool], *, timeout: float,
          interval: float = 0.25) -> bool:
    """Ask `predicate` until it holds or `timeout` runs out. Whether it held.

    Never raises: the caller decides whether not getting there is a failure.
    """
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _window_title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value


def _enum_windows(*, class_name: str | None = None, title: str | None = None,
                  visible: bool | None = None) -> list[int]:
    from ctypes import wintypes
    user32 = _user32()
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if class_name is not None and _window_class(hwnd) != class_name:
            return True
        if title is not None and _window_title(hwnd) != title:
            return True
        if visible is not None and bool(user32.IsWindowVisible(hwnd)) != visible:
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def window() -> int | None:
    """The Tray's hidden message window -- what the menu messages are sent to,
    and what its notification icon is registered against.

    Separate from `is_running()`: the process holds its mutex before it has
    created this window, so "running" does not yet mean "can be driven".
    """
    windows = _enum_windows(class_name=constants.TRAY_WINDOW_CLASS)
    if len(windows) > 1:
        raise TrayError(f"multiple {constants.TRAY_WINDOW_CLASS} windows exist: {windows}")
    return windows[0] if windows else None


def _require_window() -> int:
    hwnd = window()
    if not hwnd:
        raise TrayError("the CUBRID Tray application is not running")
    return hwnd


def is_icon_shown(hwnd: int) -> bool:
    """Is the notification icon registered against `hwnd` in the tray right now?

    Asked of the Shell by window handle and icon ID, so it answers for THIS Tray
    only -- never for a ghost icon a killed Tray left behind -- and wherever the
    icon sits, taskbar or overflow. Pass the handle from `window()`; after an
    Exit, pass the one read before it.
    """
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONIDENTIFIER(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("guidItem", GUID)]

    identifier = NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(identifier)
    identifier.hWnd = hwnd
    identifier.uID = 1                       # the Tray's only icon: nid.uID = 1
    rect = wintypes.RECT()
    result = ctypes.windll.shell32.Shell_NotifyIconGetRect(  # type: ignore[attr-defined]
        ctypes.byref(identifier), ctypes.byref(rect))
    return result == 0                       # S_OK


def wait_for_icon(*, timeout: float = 30) -> bool:
    """Wait until the Tray's window exists and its icon is in the tray.

    Returns whether it got there -- never raises on the timeout, so a case can
    assert on it after starting the Tray some other way, such as its shortcut.
    """
    def shown() -> bool:
        hwnd = window()
        return bool(hwnd) and is_icon_shown(hwnd)

    return _poll(shown, timeout=timeout)


def launch(*, timeout: float = 30) -> None:
    """Start the installed Tray and wait for its icon.

    For a Tray that is NOT running -- check `is_running()` first. A second copy
    would exit at once on the product's single-instance mutex, and this would
    then be waiting on the first.

    The executable is the one the PRODUCT recorded, never one composed here.
    """
    executable = binary_path()
    if not executable or not executable.is_file():
        raise TrayError(f"the Tray executable is missing: {executable}")
    subprocess.Popen([str(executable)], cwd=str(executable.parent))
    if not wait_for_icon(timeout=timeout):
        raise TrayError(f"the CUBRID Tray did not show its icon within {timeout:.0f}s")


def wait_until_exited(*, timeout: float = 30) -> bool:
    """Wait until the Tray process has gone. Returns whether it did; never raises.

    The process can take one monitor interval to join its worker thread.
    """
    return _poll(lambda: not is_running(), timeout=timeout)


def _window_thread(hwnd: int) -> int:
    return int(_user32().GetWindowThreadProcessId(hwnd, None))


def _tray_menus(owner_thread: int) -> dict[int, int]:
    """The live menus the Tray's popup windows are showing, as {HMENU: window}.

    Only a popup created by the Tray's OWN thread counts, so another program's
    menu open at the same moment is never mistaken for this one.

    Only a LIVE menu (`IsMenu`) counts, because the popup WINDOW outlives its
    menu: after a menu is dismissed, Windows keeps the window -- still visible,
    still the Tray's -- answering with the handle of a menu the Tray has since
    destroyed.
    """
    user32 = _user32()
    menus: dict[int, int] = {}
    for popup in _enum_windows(class_name=_POPUP_MENU_CLASS, visible=True):
        if _window_thread(popup) != owner_thread:
            continue
        menu = int(user32.SendMessageW(popup, _MN_GETHMENU, 0, 0))
        if menu and user32.IsMenu(menu):
            menus[menu] = popup
    return menus


def _open_menu(*, timeout: float = 15) -> tuple[int, int]:
    """Open the context menu as the Shell does on a right-click; return
    (HMENU, the popup window showing it).

    The Tray is sent the notification the Shell sends it -- `WM_TRAYICON` with
    `WM_RBUTTONUP` -- so it runs its own `ShowContextMenu()`. Foreground first,
    as the Shell grants it: `TrackPopupMenu` needs its owner in front.
    """
    hwnd = _require_window()
    thread = _window_thread(hwnd)
    # A menu must not already be open. The Tray cannot open a popup menu while
    # one is still up, so the request would produce nothing -- and the open one
    # would be read in its place, with its old greying.
    for menu, popup in _tray_menus(thread).items():
        _close_menu(menu, popup)
    user32 = _user32()
    user32.SetForegroundWindow(hwnd)
    user32.PostMessageW(hwnd, _WM_TRAYICON, 1, _WM_RBUTTONUP)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        menus = _tray_menus(thread)
        if menus:
            return next(iter(menus.items()))
        time.sleep(0.1)
    raise TrayError("the Tray context menu did not appear")


def _close_menu(menu: int, popup: int, *, timeout: float = 5) -> None:
    """Close the Tray's open menu and wait until the Tray has destroyed it.

    NOT with a pressed Escape key: a key goes to the FOREGROUND window, and a
    menu opened by a posted message never gets the foreground -- Windows keeps
    a background program from taking it -- so the key lands in whatever the
    user has open and the menu stays up. Instead the Tray's own window is told
    to cancel its menu (`WM_CANCELMODE`, which `DefWindowProc` handles), and if
    that has not closed it, Escape is posted straight to the menu's window.

    Waits for the MENU to be destroyed, not the window: the popup window
    outlives its menu. Returning early would let the next request arrive while
    this menu is still up, and the Tray opens no second menu over an open one.
    """
    from ctypes import wintypes

    user32 = _user32()
    hwnd = window()
    if hwnd:
        result = wintypes.DWORD()
        user32.SendMessageTimeoutW(hwnd, _WM_CANCELMODE, 0, 0, _SMTO_ABORTIFHUNG,
                                   2000, ctypes.byref(result))
    if _poll(lambda: not user32.IsMenu(menu), timeout=2, interval=0.1):
        return
    user32.PostMessageW(popup, _WM_KEYDOWN, _VK_ESCAPE, 0)
    if not _poll(lambda: not user32.IsMenu(menu), timeout=timeout, interval=0.1):
        raise TrayError(f"the Tray menu did not close within {timeout:.0f}s")


def _read_menu(menu: int) -> list[MenuItem]:
    user32 = _user32()
    items: list[MenuItem] = []
    for position in range(int(user32.GetMenuItemCount(menu))):
        length = int(user32.GetMenuStringW(menu, position, None, 0, _MF_BYPOSITION))
        if length <= 0:
            continue                         # a separator
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetMenuStringW(menu, position, buf, length + 1, _MF_BYPOSITION)
        flags = int(user32.GetMenuState(menu, position, _MF_BYPOSITION))
        items.append(MenuItem(buf.value, int(user32.GetMenuItemID(menu, position)),
                              not bool(flags & (_MF_DISABLED | _MF_GRAYED))))
    return items


def _press(*keys: int) -> None:
    user32 = _user32()
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, _KEYEVENTF_KEYUP, 0)


def menu_items() -> dict[str, MenuItem]:
    """Open the context menu and read it, by label, in menu order."""
    menu, popup = _open_menu()
    try:
        return {item.text: item for item in _read_menu(menu)}
    finally:
        _close_menu(menu, popup)


def menu_item_enabled(label: str) -> bool:
    items = menu_items()
    if label not in items:
        raise TrayError(f"the Tray menu has no {label!r} item; found {list(items)}")
    return items[label].enabled


def select(label: str) -> None:
    """Open the menu and choose `label`: the `WM_COMMAND` a click on it sends.

    Read from the LIVE menu first, so the command id is the one the Tray built
    and a disabled item is refused rather than sent -- Windows would never
    deliver a click on a greyed item.
    """
    menu, popup = _open_menu()
    items = {item.text: item for item in _read_menu(menu)}
    _close_menu(menu, popup)
    item = items.get(label)
    if item is None:
        raise TrayError(f"the Tray menu has no {label!r} item; found {list(items)}")
    if not item.enabled:
        raise TrayError(f"the Tray menu item {label!r} is disabled")
    hwnd = window()
    if not hwnd:
        raise TrayError("the Tray application exited while its menu was open")
    _user32().PostMessageW(hwnd, _WM_COMMAND, item.command, 0)


# --------------------------------------------------------------------------- #
# The icon's status, read from its tooltip through UI Automation
# --------------------------------------------------------------------------- #
# The icon and its tooltip change together (`UpdateTrayStatus()` sets both), so
# the tooltip text IS the icon state, readable without comparing pixels. It
# changes on the Tray's 10-second poll, or when an operation chosen from its
# menu finishes -- never because the menu was opened.
def _control_text(control) -> str:
    try:
        return (control.window_text() or "").strip()
    except Exception:
        return ""


def _status_texts(controls) -> set[str]:
    wanted = {constants.TRAY_TIP_RUNNING, constants.TRAY_TIP_STOPPED,
              constants.TRAY_TIP_ERROR}
    found: set[str] = set()
    for control in controls:
        text = _control_text(control)
        for status in wanted:
            # Windows 11's NotifyItemIcon accessible name prefixes the tooltip
            # with the application name, for example:
            # "CUBRID Service Tray CUBRID Service - Running".
            if text == status or text.endswith(f" {status}"):
                found.add(status)
    return found


def _is_overflow_panel(top) -> bool:
    try:
        class_name = top.class_name() or ""
    except Exception:
        class_name = ""
    identity = f"{class_name} {_control_text(top)}".casefold()
    return ("notifyiconoverflowwindow" in identity or "overflowxaml" in identity
            or "system tray overflow" in identity)


def _hidden_icons_button(desktop):
    """The taskbar button that opens and closes the hidden-icons panel, or None.

    Matched on "hidden icons" rather than one exact label: its name changes
    while the panel is open, and the same button is what closes it.
    """
    for taskbar in desktop.windows(class_name="Shell_TrayWnd"):
        for candidate in taskbar.descendants(control_type="Button"):
            if "hidden icons" in _control_text(candidate).casefold():
                return candidate
    return None


def _overflow_panel(desktop):
    """The hidden-icons panel while it is open, or None -- its window exists
    only while the panel is showing."""
    for top in desktop.windows():
        if _is_overflow_panel(top):
            return top
    return None


def _open_hidden_icons(*, timeout: float = 10):
    """Open the taskbar's hidden-icons panel, if it is not open, and return it."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    panel = _overflow_panel(desktop)
    if panel is not None:
        return panel

    button = _hidden_icons_button(desktop)
    if button is None:
        raise TrayError("the taskbar has no 'Show Hidden Icons' button")

    button.invoke()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel = _overflow_panel(desktop)
        if panel is not None:
            return panel
        time.sleep(0.1)
    raise TrayError(f"the hidden-icons panel did not appear within {timeout:.0f}s")


def _close_hidden_icons(*, timeout: float = 5) -> None:
    """Close the hidden-icons panel, if it is open, the way a user does: by
    pressing the same taskbar button again.

    Escape does not close it, and pressed with no panel there it would land on
    whatever else has the foreground. Best effort: a panel left open is untidy,
    not a failure.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    if _overflow_panel(desktop) is None:
        return
    button = _hidden_icons_button(desktop)
    if button is None:
        return
    button.invoke()
    _poll(lambda: _overflow_panel(desktop) is None, timeout=timeout, interval=0.1)


def wait_for_icon_status(expected: str, *, timeout: float = 25) -> set[str]:
    """Wait until the tray shows the CUBRID icon in status `expected` -- and ONLY
    that status -- and return the statuses the last read showed.

    `expected` is one of the `constants.TRAY_TIP_*` texts. Never raises on the
    timeout: the case asserts `== {expected}` on what comes back, so a failure
    names what the tray showed instead.

    ONLY, because a status is recognised by its text, not by which icon carries
    it: a killed Tray leaves a ghost icon still showing its last status. A read
    that shows the expected status beside another is not an answer.

    Scans the visible taskbar, and the overflow panel when the taskbar has one:
    the user decides where the icon sits, and with every icon pinned Windows
    shows no hidden-icons button at all. The default timeout covers the worst
    case for a change made outside the Tray: one 10-second poll plus the
    5-second status call it makes.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    has_overflow = _hidden_icons_button(desktop) is not None
    roots = [_open_hidden_icons()] if has_overflow else []
    roots += desktop.windows(class_name="Shell_TrayWnd")
    deadline = time.monotonic() + timeout
    shown: set[str] = set()
    try:
        while True:
            controls = []
            for root in roots:
                try:
                    controls += [root, *root.descendants()]
                except Exception:
                    pass
            shown = _status_texts(controls)
            if shown == {expected} or time.monotonic() >= deadline:
                return shown
            time.sleep(0.25)
    finally:
        if has_overflow:
            _close_hidden_icons()


# --------------------------------------------------------------------------- #
# About and Guide
# --------------------------------------------------------------------------- #
def _child_texts(hwnd: int) -> str:
    from ctypes import wintypes
    texts: list[str] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(child, _lparam):
        text = _window_title(child).strip()
        if text:
            texts.append(text)
        return True

    _user32().EnumChildWindows(hwnd, callback_type(callback), 0)
    return "\n".join(texts)


def about(*, timeout: float = 15) -> AboutInfo:
    """Choose About and return the text of the dialog it opens."""
    select("About")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dialogs = _enum_windows(title=constants.TRAY_ABOUT_TITLE, visible=True)
        if dialogs:
            return AboutInfo(_child_texts(dialogs[0]), dialogs[0])
        time.sleep(0.1)
    raise TrayError("the Tray About dialog did not appear")


def close_dialog(hwnd: int) -> None:
    _user32().PostMessageW(hwnd, _WM_CLOSE, 0, 0)


def _guide_title() -> str:
    """What a browser shows in its window title for the installed guide: the
    page's own <title>, read from the file the Tray opens -- or, for a page
    without one, its file name.

    Read from the INSTALLED file rather than recorded here, so a regenerated
    guide with a new title is still recognised.
    """
    install_dir = registry.install_dir()
    try:
        text = (install_dir / constants.TRAY_GUIDE_FILE).read_text(
            encoding="utf-8", errors="replace") if install_dir else ""
    except OSError:
        text = ""
    match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    title = html.unescape(match.group(1)).strip() if match else ""
    return title or constants.TRAY_GUIDE_FILE


def _guide_tabs(marker: str) -> dict[int, int]:
    """How many tabs show the guide, per browser window: {window: tabs}.

    Only windows whose TITLE shows the guide are searched -- a tab the Tray just
    opened is the active one, so its window says so -- which keeps editors and
    chat apps that share Chrome's window class out of it. Tabs are counted
    through UI Automation, where Chrome and Edge name each one after its page;
    a window that exposes none counts as one.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    counts: dict[int, int] = {}
    for hwnd in _enum_windows(visible=True):
        if (_window_class(hwnd) not in _BROWSER_CLASSES
                or marker not in _window_title(hwnd)):
            continue
        try:
            tabs = desktop.window(handle=hwnd).descendants(control_type="TabItem")
            count = sum(marker in _control_text(tab) for tab in tabs)
        except Exception:
            count = 0
        counts[hwnd] = max(count, 1)
    return counts


def open_guide(*, timeout: float = 15) -> int | None:
    """Choose Guide; return the browser window that gained a guide tab, or None.

    Recognised by the GUIDE'S OWN TITLE, not by any browser-class window whose
    title changed: editors and chat apps share Chrome's window class and retitle
    themselves constantly. And by a tab COUNT that went up, not by a window that
    was not showing the guide before: a browser adds the guide as a new tab to
    the window it already has open -- beside a guide tab an earlier run left,
    whose title it shares. A guide missing from the install directory opens
    nothing -- the Tray shows an error box instead -- so None covers that too.
    """
    marker = _guide_title()
    before = _guide_tabs(marker)
    select("Guide")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for hwnd, count in _guide_tabs(marker).items():
            if count > before.get(hwnd, 0):
                return hwnd
        time.sleep(0.5)
    return None


def close_viewer_tab(hwnd: int, *, timeout: float = 5) -> bool:
    """Close the guide's tab, found by `open_guide`, with Ctrl+W -- best effort.

    Returns whether it closed. Never raises: this is clean-up, and the case has
    already asserted what it came for.

    Ctrl+W is a KEYSTROKE, and a keystroke goes to whatever window is in the
    foreground. Windows often refuses to hand the foreground to another
    program's window, and then Ctrl+W would close a tab in the user's editor or
    terminal. So it is typed only once the guide's window is CONFIRMED in front,
    and still showing the guide; otherwise the tab is left open.
    """
    user32 = _user32()
    marker = _guide_title()
    tabs = _guide_tabs(marker).get(hwnd, 0)
    if not user32.IsWindow(hwnd) or not tabs:
        return not user32.IsWindow(hwnd)
    user32.SetForegroundWindow(hwnd)
    if not _poll(lambda: int(user32.GetForegroundWindow()) == hwnd,
                 timeout=1, interval=0.05):
        return False
    _press(_VK_CONTROL, _VK_W)
    return _poll(lambda: not user32.IsWindow(hwnd)
                 or _guide_tabs(marker).get(hwnd, 0) < tabs,
                 timeout=timeout, interval=0.5)
