"""Wizard installer driver -- the second route to the same machine state.

DELIBERATELY MINIMAL. It launches the bundle, advances the wizard on its
defaults and completes. It does not set options, does not cancel and does not go
Back. The point of this driver is to prove that a wizard install and a silent
install reach a machine that passes the SAME assertions; anything beyond the
default path adds maintenance before that claim is even established.

Four choices worth understanding before changing anything here:

**Windows are found with ctypes, not pywinauto.** Enumerating top-level windows
is cheap and exact; a backend-wide pywinauto search walks the UIA tree of every
process on the desktop and was the slowest thing in the old prototype.

**Controls are filtered to VISIBLE and ENABLED.** WixStdBA -- the Burn
bootstrapper UI -- is a SINGLE window that creates the controls for every page
up front and shows or hides them. Its Install, Modify, Success and Failure pages
each own a `&Close` button, so a search by label alone matches four controls.
Picking the first would be worse than an error: it would click a button
belonging to a page that is not on screen.

**It matches every language at once.** The MSI is multi-language through
transforms, so the language on screen is not simply the system locale. Every
logical string is matched against all languages in constants.WIZARD_STRINGS.

**It never asserts.** Reaching Finish is not evidence of a correct install.

Elevation is a hard precondition here for a reason specific to this driver: the
UAC prompt is drawn on the Windows secure desktop, where no automation library
can reach it. An unelevated run hangs on a dialog pywinauto cannot even see.
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import config as config_mod, constants, preflight


class WizardError(RuntimeError):
    """A window or control was not found, or would not respond."""


# --------------------------------------------------------------------------- #
# Window discovery (ctypes)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Window:
    """A located top-level window."""

    handle: int
    title: str
    class_name: str


def _enumerate_windows() -> list[tuple[int, str, str]]:
    """Every visible top-level window, as (handle, title, class name)."""
    # ctypes.wintypes is Windows-only and raises on import elsewhere, so it is
    # imported here rather than at module scope. That keeps this module (and so
    # pytest collection) importable on any platform.
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    results: list[tuple[int, str, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, 256)
        results.append((int(hwnd), title, class_buffer.value))
        return True

    user32.EnumWindows(callback_type(_callback), 0)
    return results


def find_window(titles: list[str], *, timeout: float = 120,
                interval: float = 0.5) -> Window:
    """Wait for a visible top-level window whose title contains any of these."""
    patterns = [re.compile(f".*{re.escape(t)}.*") for t in titles]
    deadline = time.time() + timeout
    seen: dict[str, str] = {}
    while True:
        for handle, title, class_name in _enumerate_windows():
            if not title:
                continue
            seen[title] = class_name
            if any(rx.search(title) for rx in patterns):
                return Window(handle, title, class_name)
        if time.time() >= deadline:
            break
        time.sleep(interval)
    raise WizardError(
        f"no window matching {titles} appeared within {timeout:.0f}s.\n"
        "Visible top-level windows were:\n"
        + ("\n".join(f"  {t!r} [{c}]" for t, c in sorted(seen.items())) or "  (none)"))


def assert_no_setup_window(titles: list[str]) -> None:
    """Refuse to start while a setup window from an earlier run is still open.

    Discovery matches on title, so a leftover bundle window is indistinguishable
    from the one this run is about to launch -- the driver would attach to the
    corpse of the previous run and click its buttons. A failed wizard run leaves
    exactly that behind.

    Deliberately refuses instead of closing it: something the framework did not
    open may be mid-operation, and an installer killed mid-operation is how a
    machine ends up half-installed.
    """
    patterns = [re.compile(f".*{re.escape(t)}.*") for t in titles]
    stale = [(h, t) for h, t, _c in _enumerate_windows()
             if t and any(rx.search(t) for rx in patterns)]
    if stale:
        raise WizardError(
            "a CUBRID setup window is already open, so this run cannot tell it "
            "apart from the one it is about to start:\n"
            + "\n".join(f"  hwnd={h} {t!r}" for h, t in stale)
            + "\n\nClose it by hand and re-run.")


# --------------------------------------------------------------------------- #
# Controls (pywinauto)
# --------------------------------------------------------------------------- #
def _accelerator_regex(text: str) -> str:
    """A regex for a label whose `&` accelerator may or may not survive.

    `&Install` and 설치(&I) come from the product's own .wxl files. Windows
    reports the ampersand in the control text, but not on every path and not
    through every backend, so it is matched as optional rather than assumed.
    """
    return re.escape(text).replace("&", "&?")


def _connect(handle: int):
    """Attach to a known window handle, trying both pywinauto backends.

    The MSI dialogs are classic Win32 (MsiDialogCloseClass) and the Burn
    bootstrapper is a themed window; neither is reliably better served by one
    backend. Connecting BY HANDLE keeps this cheap -- no tree search either way.
    """
    try:
        from pywinauto.application import Application
    except ImportError as exc:
        raise WizardError("pywinauto is not installed. Install the UI extra:\n"
                          "    python -m pip install -e .[ui]") from exc
    errors: list[str] = []
    for backend in ("win32", "uia"):
        try:
            app = Application(backend=backend).connect(handle=handle, timeout=5)
            return app.window(handle=handle), backend
        except Exception as exc:
            errors.append(f"{backend}: {type(exc).__name__}: {exc}")
    raise WizardError(f"could not attach to window {handle}: " + "; ".join(errors))


def _matching_controls(win, title_re: str, kinds: tuple[str, ...]) -> list:
    """Every VISIBLE, ENABLED control on the current page matching a label.

    Visibility is the whole point of this function; see the module docstring on
    WixStdBA's four hidden `&Close` buttons.
    """
    pattern = re.compile(f"^(?:{title_re})$")
    matches = []
    for control in win.children():
        try:
            info = control.element_info
            class_name = getattr(info, "class_name", "") or ""
            control_type = getattr(info, "control_type", "") or ""
            if class_name not in kinds and control_type not in kinds:
                continue
            if not pattern.match((control.window_text() or "").strip()):
                continue
            if not control.is_visible() or not control.is_enabled():
                continue
        except Exception:
            continue
        matches.append(control)
    return matches


def _describe_controls(win) -> str:
    """Every control on the page, for a failure that has to be diagnosed once."""
    try:
        rows = []
        for control in win.children():
            info = control.element_info
            try:
                shown = "visible" if control.is_visible() else "HIDDEN"
                if not control.is_enabled():
                    shown += ",disabled"
            except Exception:
                shown = "?"
            rows.append(f"  {control.window_text()!r} "
                        f"[class={getattr(info, 'class_name', '?')} "
                        f"type={getattr(info, 'control_type', '?')} {shown}]")
        return ("Controls in this window (HIDDEN ones belong to other pages of "
                "the same window):\n" + ("\n".join(rows) or "  (none)"))
    except Exception as exc:
        return f"(could not enumerate controls: {type(exc).__name__}: {exc})"


def click_button(win, labels: list[str], *, timeout: float = 30,
                 interval: float = 0.5) -> str:
    """Click the visible, enabled button matching any label. Returns its text.

    Retries rather than failing on the first miss: a page can exist before its
    controls are created, and a page can still be animating in.
    """
    title_re = "|".join(f"(?:{_accelerator_regex(label)})" for label in labels)
    deadline = time.time() + timeout
    last = "no matching button was visible and enabled"
    while True:
        matches = _matching_controls(win, title_re, ("Button",))
        if len(matches) > 1:
            # Two VISIBLE buttons with the same label would mean the product
            # changed shape. Say so rather than guess which to click.
            last = (f"{len(matches)} visible enabled controls match {labels}: "
                    + ", ".join(repr(c.window_text()) for c in matches))
        elif matches:
            control = matches[0]
            text = control.window_text()
            try:
                control.click_input()
                return text
            except Exception as exc:
                last = f"click failed on {text!r}: {type(exc).__name__}: {exc}"
        if time.time() >= deadline:
            break
        time.sleep(interval)
    raise WizardError(f"could not click any of {labels} within {timeout:.0f}s. "
                      f"Last: {last}.\n" + _describe_controls(win))


def _is_checked(control) -> bool:
    """Read a checkbox state across backends: uia toggles, win32 check-states."""
    for reader in ("get_toggle_state", "get_check_state"):
        try:
            return bool(getattr(control, reader)())
        except Exception:
            continue
    raise WizardError("the control does not report a check state; it is "
                      "probably not a checkbox")


def tick_checkbox(win, labels: list[str], *, timeout: float = 30,
                  interval: float = 0.5) -> str:
    """Tick a checkbox identified BY ITS LABEL, if not already ticked.

    Finding it by label rather than by position matters: on the win32 backend a
    checkbox and a push button are both class `Button`, so "the first unchecked
    Button-class child" would happily click Back.

    The licence checkbox is the only one on the default path with a real label.
    The five install-option checkboxes are declared Text=" " in the product and
    expose no accessible name at all, which is why this driver is defaults-only.
    """
    title_re = "|".join(f"(?:{_accelerator_regex(label)})" for label in labels)
    deadline = time.time() + timeout
    last = "no matching checkbox was visible and enabled"
    while True:
        matches = _matching_controls(win, title_re, ("Button", "CheckBox"))
        if len(matches) > 1:
            last = f"{len(matches)} visible controls match {labels}"
        elif matches:
            control = matches[0]
            try:
                if _is_checked(control):
                    return "already ticked"
                control.click_input()
                if _is_checked(control):
                    return "ticked"
                last = "clicked, but the control still reports unchecked"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
        if time.time() >= deadline:
            break
        time.sleep(interval)
    raise WizardError(
        f"could not tick the checkbox labelled {labels} within {timeout:.0f}s. "
        f"Last: {last}.\nNext stays disabled until the licence is accepted "
        "(CUBRID_EULA_OK), so the wizard cannot advance.\n"
        + _describe_controls(win))


# --------------------------------------------------------------------------- #
# The default install path
# --------------------------------------------------------------------------- #
@dataclass
class StepRecord:
    """One completed wizard step."""

    name: str
    window_title: str
    backend: str
    action: str
    seconds: float


@dataclass
class WizardResult:
    """Deliberately the same shape as silent.RunResult, so the install fixture
    can treat the two drivers as interchangeable."""

    action: str
    duration_seconds: float
    steps: list[StepRecord] = field(default_factory=list)
    timed_out: bool = False
    error: str | None = None
    returncode: int | None = None
    log_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out

    @property
    def reboot_required(self) -> bool:
        return False

    def describe(self) -> str:
        if self.timed_out:
            return f"{self.action} TIMED OUT after {self.duration_seconds:.0f}s"
        status = "ok" if self.ok else f"FAILED ({self.error})"
        return (f"{self.action} {status} in {self.duration_seconds:.0f}s over "
                f"{len(self.steps)} wizard steps")

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action,
                "duration_seconds": round(self.duration_seconds, 1),
                "ok": self.ok, "timed_out": self.timed_out, "error": self.error,
                "steps": [s.__dict__ for s in self.steps]}


def _assert_defaults_only(options: dict[str, Any]) -> None:
    """Refuse options this driver cannot actually honour.

    The driver advances on whatever the wizard offers. If the caller asked for
    non-default options and we clicked Next past them, the install would succeed
    and the comparison would fail with a misleading message about the product.
    """
    differing = {k: v for k, v in options.items()
                 if k in constants.INSTALL_OPTIONS
                 and str(constants.INSTALL_OPTIONS[k]) != str(v)}
    if differing:
        raise WizardError(
            f"the wizard driver drives DEFAULTS ONLY, but was asked for "
            f"{differing}. Driving the option checkboxes needs label-pairing by "
            'screen position -- the product declares them Text=" " and they '
            "expose no accessible name -- and that is not implemented.")


def install(package: config_mod.InstallerPackage, options: dict[str, Any],
            log_path: Path, *, timeout: int, step_timeout: float = 120,
            note: Callable[[str], None] | None = None) -> WizardResult:
    """Drive a default installation through the real wizard."""
    say = note or (lambda _text: None)
    preflight.require_elevation(preflight.WIZARD_REASON)
    _assert_defaults_only(options)

    strings = constants.ui_strings
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = WizardResult(action="wizard-install", duration_seconds=0.0,
                          log_path=log_path)
    process: subprocess.Popen | None = None
    started = time.monotonic()
    deadline = started + timeout

    def step(name: str, titles: list[str], action, *, wait: float | None = None) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"ran out of time before step {name!r}")
        step_started = time.monotonic()
        window = find_window(titles, timeout=wait or step_timeout)
        win, backend = _connect(window.handle)
        performed = action(win)
        elapsed = time.monotonic() - step_started
        result.steps.append(StepRecord(name, window.title, backend,
                                       str(performed), elapsed))
        say(f"    wizard   : {name:<18} {window.title!r} -> {performed} ({elapsed:.0f}s)")

    try:
        assert_no_setup_window(strings("bundle_title"))

        # Burn draws its own UI here, so there is no /passive -- but its log is
        # Disable="yes" in the bundle, so /log is still required to diagnose a
        # failure. Same as the silent driver.
        process = subprocess.Popen([str(package.path), "/log", str(log_path)])

        step("bundle-welcome", strings("bundle_title"),
             lambda w: click_button(w, strings("bundle_btn_install")))
        step("welcome", strings("welcome_title"),
             lambda w: click_button(w, strings("btn_next")))

        def _license(win):
            ticked = tick_checkbox(win, strings("license_accept"))
            return f"licence {ticked} + " + click_button(win, strings("btn_next"))
        step("license", strings("license_title"), _license)

        step("environment-check", strings("envcheck_title"),
             lambda w: click_button(w, strings("btn_next")))
        step("install-options", strings("options_title"),
             lambda w: click_button(w, strings("btn_next")))
        step("install-directory", strings("installdir_title"),
             lambda w: click_button(w, strings("btn_next")))
        step("verify-ready", strings("verifyready_title"),
             lambda w: click_button(w, strings("btn_install")))

        # The install itself. This is where the WSL import happens and it is by
        # far the longest step, so it gets the whole remaining budget.
        remaining = max(60.0, deadline - time.monotonic())
        step("finish", strings("finish_title"),
             lambda w: click_button(w, strings("btn_finish")), wait=remaining)

        # Burn shows its own success page afterwards. Closing it is what makes
        # the run repeatable -- a bundle window left open blocks the next launch.
        step("bundle-close", strings("bundle_title"),
             lambda w: click_button(w, strings("bundle_btn_close")), wait=step_timeout)

    except TimeoutError as exc:
        result.timed_out = True
        result.error = str(exc)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    if process is not None:
        # Recorded rather than waited on, deliberately: a bundle still running
        # after a failed run is itself a finding, and killing an installer
        # mid-operation is how a machine ends up half-installed.
        result.returncode = process.poll()
        if result.returncode is None and not result.ok:
            result.error = (f"{result.error}; the bundle process (pid "
                            f"{process.pid}) is still running")

    result.duration_seconds = time.monotonic() - started
    return result
