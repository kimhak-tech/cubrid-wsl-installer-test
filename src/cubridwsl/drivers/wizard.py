"""Wizard installer driver -- the second route to the same machine state.

DELIBERATELY MINIMAL. It launches the bundle, advances the wizard on its
defaults and completes. It does not set options, does not cancel and does not go
Back. The point of this driver is to prove that a wizard install and a silent
install reach a machine that passes the SAME assertions; anything beyond the
default path adds maintenance before that claim is even established.

Five choices worth understanding before changing anything here:

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

**Every wait watches for a dialog it cannot advance past.** The environment
check raises a modal warning per failed prerequisite and WiX raises a fatal
error dialog when the install fails. A driver that only waits for the next page
sits in front of those until its timeout -- up to the whole install budget on
the step where the WSL import happens -- and then reports that a page never
appeared, which is true and useless.

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


def find_window(titles: list[str], *, timeout: float = 120, interval: float = 0.5,
                accept: Callable[[Window], bool] | None = None,
                guard: Callable[[], None] | None = None) -> Window:
    """Wait for a visible top-level window whose title contains any of these.

    `accept` is a second test a candidate must pass. A title alone is not always
    enough to identify a page -- the completion dialog is the case that matters
    -- so the caller can insist on, say, an MSI-class window that really carries
    a Finish button.

    `guard` is called on every poll and may raise. That is what stops this from
    waiting out its whole timeout in front of a modal error dialog and then
    reporting the wrong thing: the page it was waiting for never appeared, but
    the reason is on screen and the guard reads it.
    """
    patterns = [re.compile(f".*{re.escape(t)}.*") for t in titles]
    deadline = time.time() + timeout
    seen: dict[str, str] = {}
    rejected: dict[str, str] = {}
    while True:
        if guard is not None:
            guard()
        for handle, title, class_name in _enumerate_windows():
            if not title:
                continue
            seen[title] = class_name
            if not any(rx.search(title) for rx in patterns):
                continue
            candidate = Window(handle, title, class_name)
            if accept is None or accept(candidate):
                return candidate
            rejected[title] = class_name
        if time.time() >= deadline:
            break
        time.sleep(interval)

    detail = ""
    if rejected:
        # The difference between "the page never came up" and "something that
        # looked like it came up and was not it" is most of the diagnosis.
        detail = ("\nWindows that matched the title but were rejected:\n"
                  + "\n".join(f"  {t!r} [{c}]" for t, c in sorted(rejected.items())))
    raise WizardError(
        f"no window matching {titles} appeared within {timeout:.0f}s.{detail}\n"
        "Visible top-level windows were:\n"
        + ("\n".join(f"  {t!r} [{c}]" for t, c in sorted(seen.items())) or "  (none)"))


def assert_no_setup_window(titles: list[str], *, timeout: float = 60.0,
                           interval: float = 1.0,
                           note: Callable[[str], None] | None = None) -> None:
    """Refuse to start while a setup window from an earlier run is still open.

    Discovery matches on title, so a leftover bundle window is indistinguishable
    from the one this run is about to launch -- the driver would attach to the
    corpse of the previous run and click its buttons. A failed wizard run leaves
    exactly that behind.

    It WAITS first, rather than refusing on the first look. The window this most
    often catches is not a human's: `reset.ensure_clean` runs an uninstall
    immediately before this, and Burn's process can return while its own window
    is still closing. Refusing instantly turned that race into a failed run --
    the suite tripping over its own cleanup. (The uninstall is now /quiet and
    draws no window at all, so this wait is the second line of defence rather
    than the first.)

    After the timeout it still REFUSES rather than closing anything: something
    the framework did not open may be mid-operation, and an installer killed
    mid-operation is how a machine ends up half-installed.
    """
    say = note or (lambda _text: None)
    patterns = [re.compile(f".*{re.escape(t)}.*") for t in titles]

    def _stale() -> list[tuple[int, str]]:
        return [(h, t) for h, t, _c in _enumerate_windows()
                if t and any(rx.search(t) for rx in patterns)]

    deadline = time.time() + timeout
    stale = _stale()
    if stale:
        say(f"    wizard   : a setup window is open ({stale[0][1]!r}); waiting "
            f"up to {timeout:.0f}s for it to close")
    while stale and time.time() < deadline:
        time.sleep(interval)
        stale = _stale()

    if stale:
        raise WizardError(
            "a CUBRID setup window is already open, so this run cannot tell it "
            f"apart from the one it is about to start (still there after "
            f"{timeout:.0f}s):\n"
            + "\n".join(f"  hwnd={h} {t!r}" for h, t in stale)
            + "\n\nClose it by hand and re-run.")


# --------------------------------------------------------------------------- #
# Dialogs that stop the wizard
#
# The installer's environment check raises a modal warning per failed
# prerequisite, and WiX raises CustomFatalErrorDlg when the install itself
# fails. Both are invisible to a driver that only waits for the next page: it
# waits out its whole timeout -- up to the full install budget on the step where
# the WSL import happens -- and then reports that a page never appeared, which
# is true and useless. The reason was on screen the entire time.
#
# So every wait polls for them. Fatal ones abort with the dialog's own text;
# the reboot advisory is acknowledged and the run continues, which is what the
# dev team's prototype does.
# --------------------------------------------------------------------------- #
def _dialog_text(handle: int) -> str:
    """The longest static text in a dialog -- in practice its message body."""
    try:
        win, _ = _connect(handle)
        texts = []
        for control in win.children():
            try:
                info = control.element_info
                if (getattr(info, "class_name", "") == "Static"
                        or getattr(info, "control_type", "") == "Text"):
                    texts.append((control.window_text() or "").strip())
            except Exception:
                continue
        return max(texts, key=len) if texts else ""
    except Exception:
        return ""


def check_for_blocking_dialog(say: Callable[[str], None] | None = None) -> None:
    """Raise if a dialog the wizard cannot advance past is on screen.

    The one advisory case -- WSL wants a reboot -- is acknowledged and the run
    continues; every other environment-check warning means the installer has
    refused, and clicking OK would only produce a failed install later.
    """
    say = say or (lambda _text: None)
    for handle, title, _class_name in _enumerate_windows():
        if not title:
            continue

        for name, titles in constants.WIZARD_WARNINGS.items():
            if not any(t in title for t in titles):
                continue
            if name == constants.ADVISORY_WARNING:
                try:
                    win, _ = _connect(handle)
                    click_button(win, list(constants.OK_BUTTONS), timeout=10)
                except WizardError as exc:
                    raise WizardError(
                        f"an advisory dialog {title!r} is on screen and could "
                        f"not be dismissed, so the wizard cannot continue: {exc}"
                    ) from exc
                say(f"    wizard   : acknowledged advisory dialog {title!r}")
                return
            raise WizardError(
                f"the installer's environment check refused to continue: "
                f"{title!r} ({name}).\n"
                f"{_dialog_text(handle)}\n"
                "This is the product telling you the machine does not meet a "
                "prerequisite. Fix the machine; do not click past it.")

        if any(t in title for t in constants.WIZARD_FAILURE_TITLES):
            raise WizardError(
                f"the installer reported failure: {title!r}\n"
                f"{_dialog_text(handle)}\n"
                "The bundle log named in the run directory carries the detail.")


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


def _label_regex(labels: list[str]) -> str:
    """One alternation matching any of these labels, accelerators optional."""
    return "|".join(f"(?:{_accelerator_regex(label)})" for label in labels)


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


def _matching_controls(win, title_re: str, kinds: tuple[str, ...], *,
                       require_enabled: bool = True) -> list:
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
            if not control.is_visible():
                continue
            if require_enabled and not control.is_enabled():
                continue
        except Exception:
            continue
        matches.append(control)
    return matches


def _describe_controls(win) -> str:
    """Every control on the page, for a failure that has to be diagnosed once.

    Carries the control ID and the row centre as well as the text, because the
    failure this appears in is almost always a PAIRING failure -- the option
    checkboxes have no accessible name (constants.OPTION_LABELS), so they are
    found by the label sharing their row. Diagnosing one means seeing which
    rows the controls actually sit on; and if the IDs turn out stable and
    distinct, matching on ID replaces the row pairing altogether. Both are read
    off this dump, so the failure answers the question instead of prompting a
    second run to ask it.
    """
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
            try:
                rect = control.rectangle()
                where = f"row={(rect.top + rect.bottom) // 2} x={rect.left}"
            except Exception:
                where = "row=?"
            try:
                identifier = f"id={control.control_id()}"
            except Exception:
                identifier = "id=?"
            rows.append(f"  {control.window_text()!r} "
                        f"[class={getattr(info, 'class_name', '?')} "
                        f"type={getattr(info, 'control_type', '?')} "
                        f"{identifier} {where} {shown}]")
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
    title_re = _label_regex(labels)
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
    # "Not visible and enabled" covers two completely different situations, and
    # the fix differs: a missing button means the wrong page, a disabled one
    # means the page will not let us leave yet (the licence checkbox, most
    # often). Say which.
    disabled = [c for c in _matching_controls(win, title_re, ("Button", "CheckBox"),
                                              require_enabled=False)]
    if disabled:
        last = ("the button exists but is disabled: "
                + ", ".join(repr(c.window_text()) for c in disabled))
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
    title_re = _label_regex(labels)
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


def _is_completion_dialog(window: Window) -> bool:
    """Whether this really is the MSI's completion page.

    Its title is not enough on its own -- the dev team's prototype hit the same
    ambiguity and validates the same two things. A window qualifies only if it
    is an MSI dialog class AND actually carries a Finish button, which also
    rules out accepting the page a moment before its controls exist.
    """
    if (window.class_name
            and not window.class_name.startswith(constants.MSI_DIALOG_CLASS_PREFIX)):
        return False
    try:
        win, _ = _connect(window.handle)
    except WizardError:
        return False
    title_re = _label_regex(constants.ui_strings("btn_finish"))
    return bool(_matching_controls(win, title_re, ("Button",),
                                   require_enabled=False))


def _controls(win) -> list:
    """Every direct child control, with the ones that raise on inspection
    dropped rather than aborting the search."""
    out = []
    for control in win.children():
        try:
            control.rectangle()
            out.append(control)
        except Exception:
            continue
    return out


def _class_of(control) -> str:
    return (getattr(control.element_info, "class_name", "") or "")


def _text_of(control) -> str:
    try:
        return (control.window_text() or "").strip()
    except Exception:
        return ""


def _find_labelled_control(win, labels: tuple[str, ...], kinds: tuple[str, ...],
                           *, side: str = "right"):
    """The control sitting on the same ROW as a label, on the given side.

    This is the answer to the product's unlabelled controls (see
    constants.OPTION_LABELS). A checkbox declared `Text=" "` has no accessible
    name, but the Text control carrying its caption shares its Y -- so find the
    LABEL by its text, then take the control beside it.

    `side` is "left" for the option checkboxes (X=25, label at X=40) and
    "right" for the WSL-name edit (label at X=25, field at X=75).

    Raises rather than guessing. A pairing that cannot be made confidently is
    the one case where a silent fallback is unacceptable: the driver would tick
    SOMETHING, the install would succeed, and the comparison would then report
    a product defect that is really a driver defect.
    """
    wanted = {label.casefold() for label in labels}
    kinds_wanted = {kind.casefold() for kind in kinds}
    label_control = next(
        (c for c in _controls(win)
         if _class_of(c) == "Static" and _text_of(c).casefold() in wanted), None)
    if label_control is None:
        raise WizardError(
            f"no label matching {labels} on this page. The option controls are "
            "found through their labels because the product gives them no "
            "accessible name; a renamed or re-localised label breaks that, and "
            "it must break loudly.\n" + _describe_controls(win))

    label_rect = label_control.rectangle()
    row = (label_rect.top + label_rect.bottom) / 2
    label_centre = (label_rect.left + label_rect.right) / 2

    candidates = []
    for control in _controls(win):
        if (control is label_control
                or _class_of(control).casefold() not in kinds_wanted):
            continue
        rect = control.rectangle()
        if abs((rect.top + rect.bottom) / 2 - row) > constants.OPTION_ROW_TOLERANCE_PX:
            continue
        # Compared by CENTRE, not by facing edges. The WSL-name label is
        # declared X=25 W=50 and its field X=75, so their edges are exactly
        # flush -- an edge test decides that pairing on one pixel of rounding
        # and drops the only candidate when it rounds the wrong way. Centres
        # are 167px apart on the same dialog, so the answer cannot depend on
        # rounding. The same holds for a checkbox left of its caption.
        centre = (rect.left + rect.right) / 2
        if side == "left" and centre > label_centre:
            continue
        if side == "right" and centre < label_centre:
            continue
        candidates.append((abs(centre - label_centre), control))

    if not candidates:
        raise WizardError(
            f"found the label {labels[0]!r} but no {kinds} control on its row "
            f"to the {side}. The dialog layout has changed.\n"
            + _describe_controls(win))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def option_checkbox(win, option: str):
    """The checkbox for one bundle variable, found through its label."""
    try:
        labels = constants.OPTION_LABELS[option]
    except KeyError:
        raise WizardError(
            f"{option!r} has no label in constants.OPTION_LABELS, so the wizard "
            "cannot find its checkbox. Options the wizard cannot drive must be "
            "refused, never silently skipped.") from None
    # A checkbox and a push button are both class "Button" on the win32 backend,
    # and the option boxes are the ones with EMPTY text -- which is also what
    # makes them unfindable by name in the first place.
    return _find_labelled_control(win, labels, ("Button",), side="left")


def set_option(win, option: str, want: bool, *, settle: float = 5,
               note: Callable[[str], None] | None = None) -> str:
    """Put one option checkbox into the wanted state, and confirm it took.

    Clicked with the REAL mouse (`click_input`), not with a posted message.
    `click()` posts WM_LBUTTONDOWN/UP straight to the control, and the MSI
    dialog does not toggle for it: an MSI CheckBox is not a self-toggling
    BS_AUTOCHECKBOX -- the state lives in the MSI property, and MSI's own dialog
    procedure is what flips it when it sees the click go through the message
    loop. So the posted click was read, the box stayed on, and the driver
    reported that it "did not take". It never had. This is the same input the
    licence checkbox already takes, which is why that one always worked.

    Then POLLED, not read once. Real input is asynchronous -- SendInput returns
    before the dialog has processed anything -- so an immediate read is a race
    the driver loses at random.

    The read-back itself is the point of this function. A click that lands on a
    disabled or obscured control changes nothing, and an unverified click is
    exactly how a scenario ends up asserting the defaults it thought it had
    changed.
    """
    say = note or (lambda _text: None)
    box = option_checkbox(win, option)
    before = _is_checked(box)
    if before == want:
        say(f"    wizard   : {option} already {'on' if want else 'off'}")
        return f"{option}={int(want)} (unchanged)"

    box.click_input()
    deadline = time.time() + settle
    after = before
    while True:
        after = _is_checked(box)
        if after == want or time.time() >= deadline:
            break
        time.sleep(0.1)

    if after != want:
        try:
            enabled = box.is_enabled()
        except Exception:
            enabled = "?"
        rect = box.rectangle()
        raise WizardError(
            f"clicked the {option} checkbox but after {settle:.0f}s it still "
            f"reads {'on' if after else 'off'}, not {'on' if want else 'off'}. "
            f"The click did not take. The control is at {rect} and reports "
            f"enabled={enabled}; if it is enabled and in view, the click is "
            "reaching the wrong window -- check that no other window took the "
            "foreground while the wizard was being driven.")
    say(f"    wizard   : {option} {'on' if before else 'off'} -> "
        f"{'on' if want else 'off'}")
    return f"{option}={int(want)}"


def set_text_field(win, labels: tuple[str, ...], value: str, *,
                   kinds: tuple[str, ...] = constants.TEXT_FIELD_CLASSES,
                   side: str = "right", settle: float = 5,
                   note: Callable[[str], None] | None = None) -> str:
    """Type a value into the field beside a label, and read it back.

    Typed with the keyboard rather than set with WM_SETTEXT. MSI reads a
    control's value through its own notifications, and a silently-set field can
    leave the underlying property untouched -- the install then succeeds with
    the DEFAULT value and every assertion about the custom one fails, blaming
    the product.

    The read-back is POLLED for the same reason the option checkboxes are:
    typing is real input, and SendInput returns before the dialog's message
    loop has caught up. Reading once turns a working driver into one that
    fails a few runs in ten.
    """
    say = note or (lambda _text: None)
    field = _find_labelled_control(win, labels, kinds, side=side)
    field.click_input()
    field.type_keys("^a{DEL}", set_foreground=False)
    field.type_keys(value, with_spaces=True, set_foreground=False)

    deadline = time.time() + settle
    while True:
        written = _text_of(field)
        if written == value or time.time() >= deadline:
            break
        time.sleep(0.1)

    if written != value:
        raise WizardError(
            f"typed {value!r} into the field beside {labels[0]!r} but after "
            f"{settle:.0f}s it reads {written!r}.")
    say(f"    wizard   : {labels[0]} <- {value!r}")
    return f"{labels[0]}={value!r}"


def _unsupported_options(options: dict[str, Any]) -> dict[str, Any]:
    """Options that differ from the defaults AND the wizard cannot drive.

    The driver used to refuse every non-default option outright, because the
    only way to reach a checkbox was positional indexing. Now that each one is
    found through its label, it refuses only what it genuinely cannot set --
    which today is nothing, but keeps the guarantee that a scenario is either
    driven or rejected, never quietly ignored.
    """
    return {key: value for key, value in options.items()
            if key in constants.INSTALL_OPTIONS
            and str(constants.INSTALL_OPTIONS[key]) != str(value)
            and key not in constants.OPTION_LABELS
            and key != "CUB_DEFAULT_WSL_NAME"}


def install(package: config_mod.InstallerPackage, options: dict[str, Any],
            log_path: Path, *, timeout: int, step_timeout: float = 120,
            install_dir: str | None = None,
            note: Callable[[str], None] | None = None) -> WizardResult:
    """Drive an installation through the real wizard.

    `options` are the bundle variables to SET on InstallOptionsDlg. Any that
    already match the shipping defaults are left alone -- the driver reads each
    checkbox before clicking it, so passing the defaults is a no-op rather than
    a double toggle.

    `install_dir` is wizard-only, and deliberately so: `ActionUpdateInstallFolder`
    overwrites INSTALLFOLDER whenever UILevel < 5, so a directory passed on a
    silent command line is discarded. The wizard is the only way to set it.

    START_TRAY_APP lives on the FINISH page, not the options page, so it is set
    after the install has already run -- which is where the product puts it.
    """
    say = note or (lambda _text: None)
    preflight.require_elevation(preflight.WIZARD_REASON)
    unsupported = _unsupported_options(options)
    if unsupported:
        raise WizardError(
            f"the wizard driver cannot set {unsupported}. Every option it can "
            "drive has an entry in constants.OPTION_LABELS; anything else is "
            "refused rather than silently left at its default, which would "
            "report a success the run did not achieve.")

    strings = constants.ui_strings
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = WizardResult(action="wizard-install", duration_seconds=0.0,
                          log_path=log_path)
    process: subprocess.Popen | None = None
    started = time.monotonic()
    deadline = started + timeout

    def guard() -> None:
        """Checked on every poll of every wait -- see check_for_blocking_dialog."""
        check_for_blocking_dialog(say)

    def step(name: str, titles: list[str], action, *, wait: float | None = None,
             accept=None) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"ran out of time before step {name!r}")
        step_started = time.monotonic()
        window = find_window(titles, timeout=wait or step_timeout,
                             accept=accept, guard=guard)
        win, backend = _connect(window.handle)
        performed = action(win)
        elapsed = time.monotonic() - step_started
        result.steps.append(StepRecord(name, window.title, backend,
                                       str(performed), elapsed))
        say(f"    wizard   : {name:<18} {window.title!r} -> {performed} ({elapsed:.0f}s)")

    try:
        assert_no_setup_window(strings("bundle_title"), note=say)

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
        def _options(win):
            """Set every option on InstallOptionsDlg, then advance.

            The name field first: it is the one control whose value another
            page derives from, and typing into it after ticking boxes would
            re-read a dialog that has already moved on.
            """
            performed = []
            wanted_name = str(options.get("CUB_DEFAULT_WSL_NAME", ""))
            if wanted_name and wanted_name != constants.INSTALL_OPTIONS[
                    "CUB_DEFAULT_WSL_NAME"]:
                performed.append(set_text_field(
                    win, constants.WSL_NAME_LABEL, wanted_name, note=say))
            for option in ("REG_TRAY_APP", "CREATE_SHORTCUT", "CREATE_DEMODB",
                           "IS_WSL2_MODE"):
                if option in options:
                    performed.append(set_option(
                        win, option, bool(int(options[option])), note=say))
            performed.append(click_button(win, strings("btn_next")))
            return " + ".join(performed)

        step("install-options", strings("options_title"), _options)

        def _directory(win):
            if install_dir is None:
                return click_button(win, strings("btn_next"))
            # The PathEdit is the only text field on this page, and its label
            # sits on the row ABOVE it rather than beside it -- so it is matched
            # by kind within the page, not by row.
            wanted = {kind.casefold() for kind in constants.TEXT_FIELD_CLASSES}
            field = next((c for c in _controls(win)
                          if _class_of(c).casefold() in wanted), None)
            if field is None:
                raise WizardError("no directory field on the installation "
                                  "directory page.\n" + _describe_controls(win))
            field.click_input()
            field.type_keys("^a{DEL}", set_foreground=False)
            field.type_keys(install_dir, with_spaces=True, set_foreground=False)
            written = _text_of(field)
            if written.rstrip("\\") != install_dir.rstrip("\\"):
                raise WizardError(f"typed {install_dir!r} into the directory "
                                  f"field but it reads {written!r}")
            say(f"    wizard   : install directory <- {install_dir!r}")
            return (f"dir={install_dir!r} + "
                    + click_button(win, strings("btn_next")))

        step("install-directory", strings("installdir_title"), _directory)
        step("verify-ready", strings("verifyready_title"),
             lambda w: click_button(w, strings("btn_install")))

        # The install itself. This is where the WSL import happens and it is by
        # far the longest step, so it gets the whole remaining budget.
        remaining = max(60.0, deadline - time.monotonic())
        step("finish", strings("finish_title"),
             lambda w: click_button(w, strings("btn_finish")), wait=remaining,
             accept=_is_completion_dialog)

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
