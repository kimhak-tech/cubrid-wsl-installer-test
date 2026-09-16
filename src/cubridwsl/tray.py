"""Windows driver for the installed CUBRID notification-area application.

The driver performs actions and exposes UI facts. It deliberately does not
judge CUBRID service state; tests verify that independently through state.py.
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import constants


class TrayError(RuntimeError):
    """The Tray window, popup menu, or expected control could not be used."""


@dataclass(frozen=True)
class MenuItem:
    text: str
    command: int
    enabled: bool
    position: int


@dataclass(frozen=True)
class AboutInfo:
    text: str
    window: int


class TrayDriver:
    """Drive one installed CUBRID Tray application."""

    WM_TRAYICON = 0x0401  # WM_USER + 1 in cubrid_tray_app.h
    WM_COMMAND = 0x0111
    WM_RBUTTONUP = 0x0205
    MN_GETHMENU = 0x01E1
    VK_ESCAPE = 0x1B
    VK_CONTROL = 0x11
    VK_W = 0x57
    KEYEVENTF_KEYUP = 0x0002
    MF_BYPOSITION = 0x00000400
    MF_DISABLED = 0x00000002
    MF_GRAYED = 0x00000001
    INPUT_MOUSE = 0
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    def __init__(self, executable: Path):
        self.executable = executable

    @classmethod
    def from_registry(cls, registry) -> "TrayDriver":
        install_dir = registry.install_dir
        filename = registry.values.get("TrayAppFile") or constants.TRAY_EXE
        if not install_dir:
            raise TrayError("the product registry does not contain InstallDir")
        executable = install_dir / str(filename)
        if not executable.is_file():
            raise TrayError(f"the Tray executable is missing: {executable}")
        return cls(executable)

    @staticmethod
    def _user32():
        if not hasattr(ctypes, "windll"):
            raise TrayError("the CUBRID Tray driver requires Windows")
        return ctypes.windll.user32  # type: ignore[attr-defined]

    @staticmethod
    def _enum_windows(*, class_name: str | None = None,
                      title: str | None = None, visible: bool | None = None) -> list[int]:
        from ctypes import wintypes
        user32 = TrayDriver._user32()
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            length = user32.GetWindowTextLengthW(hwnd)
            text_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, text_buf, length + 1)
            if class_name is not None and class_buf.value != class_name:
                return True
            if title is not None and text_buf.value != title:
                return True
            if visible is not None and bool(user32.IsWindowVisible(hwnd)) != visible:
                return True
            found.append(int(hwnd))
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return found

    def window(self) -> int | None:
        windows = self._enum_windows(class_name=constants.TRAY_WINDOW_CLASS)
        if len(windows) > 1:
            raise TrayError(f"multiple {constants.TRAY_WINDOW_CLASS} windows exist: {windows}")
        return windows[0] if windows else None

    def is_running(self) -> bool:
        return self.window() is not None

    @staticmethod
    def wait_until(predicate: Callable[[], bool], *, timeout: float = 30,
                   interval: float = 0.25, description: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise TrayError(f"{description} was not satisfied within {timeout:.0f}s")
            time.sleep(interval)

    def launch(self, *, timeout: float = 30) -> int:
        current = self.window()
        if current:
            return current
        subprocess.Popen([str(self.executable)], cwd=str(self.executable.parent))
        self.wait_until(self.is_running, timeout=timeout,
                        description="the CUBRID Tray process to start")
        return int(self.window())

    def wait_until_exited(self, *, timeout: float = 30) -> None:
        # The process can take one monitor interval to join its worker thread.
        self.wait_until(lambda: not self.is_running(), timeout=timeout,
                        description="the CUBRID Tray process to exit")

    def _tray_icon_rect(self):
        """Return the real Shell notification icon rectangle."""
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        class NOTIFYICONIDENTIFIER(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID),
            ]

        hwnd = self.window()
        if not hwnd:
            raise TrayError("the CUBRID Tray application is not running")
        identifier = NOTIFYICONIDENTIFIER()
        identifier.cbSize = ctypes.sizeof(identifier)
        identifier.hWnd = hwnd
        identifier.uID = 1
        rect = wintypes.RECT()
        result = ctypes.windll.shell32.Shell_NotifyIconGetRect(  # type: ignore[attr-defined]
            ctypes.byref(identifier), ctypes.byref(rect))
        if result != 0:
            raise TrayError(f"Windows could not locate the Tray icon (HRESULT=0x{result & 0xffffffff:08x})")
        return rect

    def _right_click_icon(self) -> None:
        """Right-click the real Shell notification icon by its HWND/uID."""
        rect = self._tray_icon_rect()
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        user32 = self._user32()
        user32.SetCursorPos(x, y)
        user32.mouse_event(self.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        user32.mouse_event(self.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

    def _open_menu(self, *, timeout: float = 15) -> tuple[int, int]:
        hwnd = self.window()
        if not hwnd:
            raise TrayError("the CUBRID Tray application is not running")
        user32 = self._user32()
        existing = set(self._enum_windows(class_name="#32768", visible=True))
        user32.SetForegroundWindow(hwnd)
        user32.PostMessageW(hwnd, self.WM_TRAYICON, 1, self.WM_RBUTTONUP)

        popup: int | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = self._enum_windows(class_name="#32768", visible=True)
            foreground = int(user32.GetForegroundWindow())
            candidates.sort(key=lambda candidate: (
                candidate != foreground, candidate in existing))
            for candidate in candidates:
                menu = int(user32.SendMessageW(candidate, self.MN_GETHMENU, 0, 0))
                if menu:
                    popup = candidate
                    break
            if popup:
                break
            time.sleep(0.1)
        if not popup:
            raise TrayError("the Tray context menu did not appear")
        menu = int(user32.SendMessageW(popup, self.MN_GETHMENU, 0, 0))
        if not menu:
            raise TrayError("the Tray popup did not expose its menu handle")
        return popup, menu

    @staticmethod
    def _menu_items(menu: int) -> list[MenuItem]:
        user32 = TrayDriver._user32()
        count = int(user32.GetMenuItemCount(menu))
        items: list[MenuItem] = []
        for position in range(count):
            length = int(user32.GetMenuStringW(menu, position, None, 0,
                                               TrayDriver.MF_BYPOSITION))
            if length <= 0:
                continue
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetMenuStringW(menu, position, buf, length + 1,
                                  TrayDriver.MF_BYPOSITION)
            state = int(user32.GetMenuState(menu, position, TrayDriver.MF_BYPOSITION))
            command = int(user32.GetMenuItemID(menu, position))
            items.append(MenuItem(buf.value, command,
                                  not bool(state & (TrayDriver.MF_DISABLED |
                                                    TrayDriver.MF_GRAYED)),
                                  position))
        return items

    @classmethod
    def _dismiss_menu(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls.VK_ESCAPE, 0, 0, 0)
        user32.keybd_event(cls.VK_ESCAPE, 0, cls.KEYEVENTF_KEYUP, 0)

    def menu_items(self) -> dict[str, MenuItem]:
        _popup, menu = self._open_menu()
        try:
            return {item.text: item for item in self._menu_items(menu)}
        finally:
            self._dismiss_menu()

    def select(self, label: str) -> None:
        _popup, menu = self._open_menu()
        items = {item.text: item for item in self._menu_items(menu)}
        item = items.get(label)
        if item is None:
            self._dismiss_menu()
            raise TrayError(f"the Tray menu has no {label!r} item; found {list(items)}")
        if not item.enabled:
            self._dismiss_menu()
            raise TrayError(f"the Tray menu item {label!r} is disabled")
        hwnd = self.window()
        if not hwnd:
            self._dismiss_menu()
            raise TrayError("the Tray application exited while its menu was open")
        user32 = self._user32()
        self._dismiss_menu()
        user32.PostMessageW(hwnd, self.WM_COMMAND, item.command, 0)

    def menu_item_enabled(self, label: str) -> bool:
        items = self.menu_items()
        if label not in items:
            raise TrayError(f"the Tray menu has no {label!r} item; found {list(items)}")
        return items[label].enabled

    @staticmethod
    def _control_text(control) -> str:
        try:
            return (control.window_text() or "").strip()
        except Exception:
            return ""

    @classmethod
    def _status_texts(cls, controls) -> set[str]:
        wanted = {constants.TRAY_TIP_RUNNING, constants.TRAY_TIP_STOPPED,
                  constants.TRAY_TIP_ERROR}
        found: set[str] = set()
        for control in controls:
            text = cls._control_text(control)
            for status in wanted:
                # Windows 11's NotifyItemIcon accessible name prefixes the
                # tooltip with the application name, for example:
                # "CUBRID Service Tray CUBRID Service - Running".
                if text == status or text.endswith(f" {status}"):
                    found.add(status)
        return found

    def _open_hidden_icons(self, *, timeout: float = 10):
        """Click the taskbar's Show Hidden Icons button and return its panel."""
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")

        def overflow_window():
            for window in desktop.windows():
                class_name = ""
                try:
                    class_name = window.class_name() or ""
                except Exception:
                    pass
                title = self._control_text(window)
                normalized = f"{class_name} {title}".casefold()
                if ("notifyiconoverflowwindow" in normalized or
                        "overflowxaml" in normalized or
                        "system tray overflow" in normalized):
                    return window
            return None

        existing = overflow_window()
        if existing is not None:
            return existing

        taskbars = desktop.windows(class_name="Shell_TrayWnd")
        if not taskbars:
            raise TrayError("Windows taskbar was not found")

        button = None
        for taskbar in taskbars:
            for candidate in taskbar.descendants(control_type="Button"):
                if self._control_text(candidate).casefold() == "show hidden icons":
                    button = candidate
                    break
            if button is not None:
                break
        if button is None:
            raise TrayError("the taskbar has no 'Show Hidden Icons' button")

        button.invoke()
        deadline = time.monotonic() + timeout
        last_windows: list[str] = []
        while time.monotonic() < deadline:
            windows = desktop.windows()
            last_windows = []
            for window in windows:
                class_name = ""
                try:
                    class_name = window.class_name() or ""
                except Exception:
                    pass
                title = self._control_text(window)
                last_windows.append(f"{class_name}:{title}")
                normalized = f"{class_name} {title}".casefold()
                if ("notifyiconoverflowwindow" in normalized or
                        "overflowxaml" in normalized or
                        "system tray overflow" in normalized):
                    return window
            time.sleep(0.1)
        raise TrayError(
            "the hidden-icons panel did not appear; visible windows="
            f"{last_windows!r}")

    @classmethod
    def _close_hidden_icons(cls) -> None:
        from pywinauto import Desktop

        user32 = cls._user32()
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                identity = f"{window.class_name()} {window.window_text()}".casefold()
            except Exception:
                continue
            if ("notifyiconoverflowwindow" in identity or
                    "overflowxaml" in identity or
                    "system tray overflow" in identity):
                user32.SetForegroundWindow(window.handle)
                break
        user32.keybd_event(cls.VK_ESCAPE, 0, 0, 0)
        user32.keybd_event(cls.VK_ESCAPE, 0, cls.KEYEVENTF_KEYUP, 0)

    def wait_for_tooltip(self, expected: str, *, timeout: float = 25) -> str:
        """Require the CUBRID icon's tooltip-backed accessible name."""
        if expected not in (constants.TRAY_TIP_RUNNING,
                            constants.TRAY_TIP_STOPPED,
                            constants.TRAY_TIP_ERROR):
            raise TrayError(f"unsupported CUBRID Tray tooltip: {expected!r}")
        panel = self._open_hidden_icons()
        deadline = time.monotonic() + timeout
        observed: set[str] = set()
        try:
            while time.monotonic() < deadline:
                try:
                    controls = [panel, *panel.descendants()]
                except Exception:
                    controls = []
                observed.update(self._status_texts(controls))
                if expected in observed:
                    return expected
                time.sleep(0.25)
            raise TrayError(
                f"Tray tooltip {expected!r} was not observed within {timeout:.0f}s; "
                f"observed={sorted(observed)!r}; "
                f"the hidden-icons panel did not expose the expected status")
        finally:
            self._close_hidden_icons()

    def wait_for_status(self, running: bool, *, timeout: float = 25) -> str:
        start_enabled = not running

        def reflected() -> bool:
            try:
                return self.menu_item_enabled("CUBRID Start") == start_enabled
            except TrayError:
                return False

        self.wait_until(reflected, timeout=timeout,
                        description=f"the Tray to reflect {'running' if running else 'stopped'}")
        return "menu-state"

    def about(self, *, timeout: float = 15) -> AboutInfo:
        self.select("About")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            dialogs = self._enum_windows(title=constants.TRAY_ABOUT_TITLE, visible=True)
            if dialogs:
                hwnd = dialogs[0]
                return AboutInfo(self._window_text(hwnd), hwnd)
            time.sleep(0.1)
        raise TrayError("the Tray About dialog did not appear")

    @staticmethod
    def _window_text(hwnd: int) -> str:
        from ctypes import wintypes
        user32 = TrayDriver._user32()
        texts: list[str] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(child, _lparam):
            length = user32.GetWindowTextLengthW(child)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(child, buf, length + 1)
                if buf.value.strip():
                    texts.append(buf.value.strip())
            return True

        user32.EnumChildWindows(hwnd, callback_type(callback), 0)
        return "\n".join(texts)

    @staticmethod
    def close_dialog(hwnd: int) -> None:
        TrayDriver._user32().PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    def open_guide(self, *, timeout: float = 15) -> tuple[Path, int]:
        guide = self.executable.parent / constants.TRAY_GUIDE_FILE
        if not guide.is_file():
            raise TrayError(f"the installed guide is missing: {guide}")
        browser_classes = {"Chrome_WidgetWin_1", "MozillaWindowClass",
                           "ApplicationFrameWindow"}

        def browsers() -> dict[int, str]:
            user32 = self._user32()
            result: dict[int, str] = {}
            for hwnd in self._enum_windows(visible=True):
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                if class_buf.value not in browser_classes:
                    continue
                length = user32.GetWindowTextLengthW(hwnd)
                title_buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title_buf, length + 1)
                result[hwnd] = title_buf.value
            return result

        before = browsers()
        self.select("Guide")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            after = browsers()
            for hwnd, title in after.items():
                if hwnd not in before or title != before.get(hwnd):
                    return guide, hwnd
            time.sleep(0.25)
        raise TrayError("Guide did not bring an external viewer to the foreground")

    def close_viewer_tab(self, hwnd: int, *, timeout: float = 5) -> None:
        """Close the Guide tab/window opened by ``open_guide`` with Ctrl+W."""
        user32 = self._user32()
        if not user32.IsWindow(hwnd):
            return
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        previous_title = title_buf.value

        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        user32.keybd_event(self.VK_CONTROL, 0, 0, 0)
        user32.keybd_event(self.VK_W, 0, 0, 0)
        user32.keybd_event(self.VK_W, 0, self.KEYEVENTF_KEYUP, 0)
        user32.keybd_event(self.VK_CONTROL, 0, self.KEYEVENTF_KEYUP, 0)

        def closed_or_changed() -> bool:
            if not user32.IsWindow(hwnd):
                return True
            current_length = user32.GetWindowTextLengthW(hwnd)
            current_buf = ctypes.create_unicode_buffer(current_length + 1)
            user32.GetWindowTextW(hwnd, current_buf, current_length + 1)
            return current_buf.value != previous_title

        self.wait_until(closed_or_changed, timeout=timeout,
                        description="the Guide browser tab to close")
