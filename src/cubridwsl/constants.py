"""Facts about the product under test.

Every value here was read out of the CUBRID For WSL Installer source, and each
group names the file it came from so it can be re-checked when the product
changes. Keep product literals HERE and nowhere else: a registry path or a
window title inside a test file is a literal nobody will find when it changes.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Registry -- CMakeLists.txt (CUB_REGISTRY_KEY_PATH) and src/cubrid_installer.cpp
#
# HKCU, not HKLM: the MSI is InstallScope="perUser" even though the bundle sets
# ForcePerMachine. Whichever account a run executes as decides which hive it
# reads, which is why preflight records the account.
# --------------------------------------------------------------------------- #
PRODUCT_KEY = r"Software\CUBRID\CUBRID_FOR_WSL"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Created only when REG_TRAY_APP=1.
RUN_VALUE_TRAY = "CUBRID_WSL_TrayApp"
# Created UNCONDITIONALLY (ActionRegisterStarterApp carries no condition), so an
# assertion of "no CUBRID entries under Run" fails against a correct build.
RUN_VALUE_STARTER = "CUBRID_WSL_Starter"

# --------------------------------------------------------------------------- #
# Apps & Features -- wix_src/bundle.wxs
#
# The MSI is installed with ARPSYSTEMCOMPONENT=1 and is hidden from Apps &
# Features; only the Burn bundle appears. The bundle's ProductCode is generated
# per build, so the entry is matched on DisplayName rather than a GUID.
# --------------------------------------------------------------------------- #
ARP_DISPLAY_NAME = "CUBRID For WSL"
# bundle.wxs Manufacturer. The MSI declares the same string, but the MSI's entry
# is hidden, so this is only ever compared against the BUNDLE's.
ARP_PUBLISHER = "CUBRID"
UNINSTALL_KEYS = (
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
)

# --------------------------------------------------------------------------- #
# The bundle's own record of how it was displayed -- Burn writes this line into
# the /log file on every run:
#
#     i410: Variable: WixBundleUILevel = 4
#
# It is the BOOTSTRAPPER_DISPLAY enum, and it is the product stating what it
# did rather than the framework inferring it from the command line. LCM-004
# records it as evidence that an unattended run drew nothing:
#
#     2 = /quiet (no UI)   3 = /passive (progress bar only)   4 = the wizard
#
# Observed: 4 on the INS-001 wizard run, 3 on a /passive uninstall.
# --------------------------------------------------------------------------- #
BURN_UI_LEVEL_VARIABLE = "WixBundleUILevel"

# --------------------------------------------------------------------------- #
# Install options -- the bal:Overridable variables in wix_src/bundle.wxs,
# forwarded to the MSI as properties. These are the shipping defaults.
#
# TO ADD A SCENARIO: pass only the keys you change to the install verb, and
# `{**INSTALL_OPTIONS, **changed}` to `check_post_install`. See the README,
# "Adding a New Test Case".
# --------------------------------------------------------------------------- #
INSTALL_OPTIONS = {
    "REG_TRAY_APP": 1,          # register the Tray for auto-start at logon
    "CREATE_SHORTCUT": 1,       # two desktop shortcuts
    "CREATE_DEMODB": 1,         # create demodb inside the distribution
    "IS_WSL2_MODE": 1,          # import the distribution at WSL 2
    "START_TRAY_APP": 1,        # launch the Tray at the end of the install
    "CUB_DEFAULT_WSL_NAME": "CUBRID-FOR-WSL",
}

# The two a CASE has to name: INS-004 overrides the mode, INS-006 overrides
# the name and reports which name the bundle refused. A product literal in a
# test file is a bug, so they are named here. The other four are only ever
# dict keys, which is why they are not.
OPTION_WSL2_MODE = "IS_WSL2_MODE"
OPTION_WSL_NAME = "CUB_DEFAULT_WSL_NAME"

# --------------------------------------------------------------------------- #
# Inside the distribution -- make_image/build_image.ps1 and the generated
# Dockerfile, which write ~/.cubrid.sh and have ~/.bash_profile source it.
#
# .bash_profile is read by LOGIN shells, which is why INS-001 asks for one --
# persistence across sessions is the point of the check.
# --------------------------------------------------------------------------- #
CUBRID_HOME = "/home/cubrid/CUBRID"
CUBRID_DATABASES = "/home/cubrid/CUBRID/databases"

# `cubrid service status` prints one section per component, each introduced by a
# header line "@ cubrid <component> status". INS-001 requires the components to
# be asserted INDIVIDUALLY -- "assert the components individually, not just a
# zero exit code" -- and the product's own code is no help there: both the Tray
# and the Starter decide the whole service is up from the master line alone.
#
# Observed on build 11.4-1.0.0-0003, immediately after a default wizard install:
#
#     @ cubrid master status
#     ++ cubrid master is running.
#     @ cubrid server status
#     @ cubrid pl status
#     @ cubrid broker status
#     ++ cubrid broker is not running.
#     @ cubrid gateway status
#     ++ cubrid gateway is not running.
#     @ cubrid manager server status
#     ++ cubrid manager server is not running.
#
# `pl` and `gateway` appear in this build and are NOT parsed: neither is named
# by INS-001, and neither is in CUBRID's default `service=` line, so both being
# down is the configured behaviour rather than a finding.
#
# The manager's header reads "cubrid manager server status", so component names
# are matched as a PREFIX rather than as whole words.
SERVICE_COMPONENTS = ("master", "server", "broker", "manager")
SERVICE_SECTION_PREFIX = "@ cubrid "

# The components an install is WAITED for (`silent.wait_until_ready`), and a
# service start or stop confirmed by (`wsl.cubrid`).
#
# The installer starts CUBRID asynchronously -- ActionStartCubridService is
# Return="asyncNoWait" and cubrid_starter.cpp nohup's `cubrid service start`
# -- so master, broker and manager appear some seconds after the bundle exits
# and must be waited for or they read as failures.
#
# `server` is NOT waited for. That section lists STARTED DATABASES, and nothing
# starts one asynchronously: either cubrid.conf names it in `server=` and it
# comes up with the rest, or it never appears. Waiting would spend the whole
# settle budget -- five minutes of every run -- to learn nothing.
SERVICE_COMPONENTS_AWAITED = ("master", "broker", "manager")

# The brokers a default install is expected to be running, observed in
# `cubrid service status` on build 11.4-1.0.0-0003:
#
#     @ cubrid broker status
#       NAME              PID  PORT   AS  JQ ...
#     =============================================
#     * query_editor       97 30000    5   0 ...
#     * broker1           116 33000    5   0 ...
#
# They come from CUBRID's own cubrid_broker.conf INSIDE the image, not from
# this product's source -- which is why they are recorded from a real run
# rather than derived. A broker service that is "up" with no brokers running
# serves nothing, so the names are checked, not just the section.
SERVICE_EXPECTED_BROKERS = ("query_editor", "broker1")

# Within one section: "is not running" is checked FIRST, so a section carrying
# both lines resolves to not-running. Two components print no "is running" line
# at all -- the server lists "Server <db> (rel ...)" per started database, and a
# running broker is shown as a table whose header carries NAME and PID. An
# EMPTY server section means no database is started: a definite answer, not an
# unreadable one.
SERVICE_NOT_RUNNING_MARKER = "is not running"
SERVICE_IS_RUNNING_MARKER = "is running"
SERVICE_SERVER_RUNNING_PREFIX = "server "

# --------------------------------------------------------------------------- #
# Category 03, CUBRID Operational
#
# Nothing here comes from the installer source: these are facts about CUBRID and
# about the demodb sample the image ships, so they are recorded rather than
# derived, exactly like SERVICE_EXPECTED_BROKERS.
# --------------------------------------------------------------------------- #
DEMODB_NAME = "demodb"

# CUBRID's built-in administrator. demodb ships with no password for it, which
# is why none is passed -- and why a password prompt would hang, so csql is
# always given a user explicitly.
CSQL_DBA = "dba"

# CUBRID's Linux engine installer (`CUBRID-<version>-Linux.x86_64.sh`) is a
# self-extracting archive that PROMPTS, and there is no documented unattended
# flag, so every prompt is fed this answer.
#
# "y", NOT "yes". The `[yN]` / `[Yn]` prompts match on the FIRST CHARACTER, so
# "y" is affirmative whichever way the default is capitalised and "yes" is
# rejected outright ("License not accepted. Exiting ...").
#
# With the default `upgrade.installer_args` the installer asks nothing and this
# is only a backstop. OPS-004 writes the full transcript to
# ops_004_installer.json, which is where an unanticipated prompt shows up.
ENGINE_INSTALLER_ANSWER = "y"

# The version inside a CUBRID engine package name, e.g.
#   CUBRID-11.4.5.1866-e9c17f7-Linux.x86_64.sh  ->  11.4.5.1866-e9c17f7
#
# OPS-004 asserts that string appears in `cubrid_rel`, which prints it verbatim
# in its parentheses:
#   CUBRID 11.4.5 (11.4.5.1866-e9c17f7) (64bit release build for Linux) ...
#
# Reading it out of the FILENAME is what makes "the installed version matches
# what was installed" a single fact with a single source -- the same reason
# config.INSTALLER_RE reads the bundle's version out of its name rather than
# having it configured twice. A name that carries no version (`11.4-latest`)
# does not match, and OPS-004 says so rather than asserting nothing.
ENGINE_PACKAGE_RE = re.compile(
    r"^CUBRID-(?P<version>[0-9][0-9.]*(?:-[0-9a-f]{4,})?)-Linux",
    re.IGNORECASE)

# --------------------------------------------------------------------------- #
# Wizard UI strings -- wix_src/strings/*.wxl
#
# The wizard driver's ONLY literals. The MSI is multi-language through
# transforms, so the language on screen is not simply the system locale: the
# driver matches every language at once rather than detecting one. A `&` is a
# keyboard accelerator; the driver matches it as optional because Windows does
# not report it on every path.
# --------------------------------------------------------------------------- #
WIZARD_STRINGS = {
    "en": {
        "bundle_title": "CUBRID For WSL Setup",
        "bundle_btn_install": "&Install",
        "bundle_btn_close": "&Close",
        "welcome_title": "Welcome to CUBRID WSL Installer",
        "license_title": "License Agreement",
        "license_accept": "I accept the terms in the License Agreement",
        "envcheck_title": "CUBRID WSL - Environment Check",
        "options_title": "Installation Options",
        "installdir_title": "Installation Directory",
        "verifyready_title": "Ready to Install",
        "finish_title": "Installation Complete",
        "btn_next": "Next",
        "btn_install": "Install",
        "btn_finish": "Finish",
        "btn_cancel": "Cancel",
        # CustomCancelDlg, spawned by the Cancel button on every wizard page.
        "cancel_title": "Cancel Installation",
        "btn_yes": "Yes",
        # Burn's MAINTENANCE page, shown when the bundle is launched on a
        # machine that already carries the product. The bundle is
        # DisableModify=yes / DisableRemove=no, and BundleTheme.xml's Modify
        # page draws exactly two buttons -- Uninstall and Close -- so reaching
        # this page at all IS the duplicate-install detection in the UI path.
        "modify_header": "Modify Setup",
        "bundle_btn_uninstall": "&Uninstall",
        # Burn's own Success and Failure pages, which both end an uninstall and
        # both carry a `&Close`. Their headers are the only thing that tells
        # them apart, and they are UNNAMED theme controls -- so WixStdBA cannot
        # substitute its per-action variants ("Uninstall Complete", "Uninstall
        # Failed"), and these two literals hold whatever the bundle was asked
        # to do. Matching the per-action strings instead would find nothing.
        "bundle_success_header": "Successful",
        "bundle_failure_header": "Setup Failed",
        # A FRAGMENT of FailureAlreadyInstalled, not the whole string. The
        # product's message is two sentences joined by a literal CRLF
        # (`&#13;&#10;` in the .wxl), and how a Hypertext control reports an
        # embedded line break is not something to bet a case on. This half
        # carries the meaning the workbook asks to see -- that upgrade and
        # duplicate installation are not supported -- in one unbroken line.
        "already_installed_message": "Upgrade/duplicate installation is not supported",
    },
    "ko": {
        "bundle_title": "CUBRID For WSL 설치",
        "bundle_btn_install": "설치(&I)",
        "bundle_btn_close": "닫기(&C)",
        "welcome_title": "CUBRID WSL 설치 프로그램 시작",
        "license_title": "사용권 계약",
        "license_accept": "사용권 계약에 동의합니다",
        "envcheck_title": "CUBRID WSL - 환경 점검",
        "options_title": "설치 옵션",
        "installdir_title": "설치 경로",
        "verifyready_title": "설치 준비 확인",
        "finish_title": "완료",
        "btn_next": "다음",
        "btn_install": "설치",
        "btn_finish": "완료",
        "btn_cancel": "취소",
        "cancel_title": "설치 취소",
        "btn_yes": "예",
        "modify_header": "제거 안내",
        "bundle_btn_uninstall": "제거(&U)",
        # Character-for-character the Korean `finish_title` above, and
        # deliberately written out again: that one is the MSI's CustomFinishDlg
        # title from strings_ko-kr.wxl, this one is Burn's SuccessHeader from
        # BundleTheme_ko-kr.wxl. Two product strings that happen to coincide in
        # Korean and not in English -- not one string repeated.
        "bundle_success_header": "완료",
        "bundle_failure_header": "설치 실패",
        "already_installed_message": "업그레이드/중복 설치를 지원하지 않습니다",
    },
}

# --------------------------------------------------------------------------- #
# The option controls -- wix_src/cubrid_wsl_ui.wxs
#
# Every checkbox is declared with `Text=" "`, so it exposes NO accessible name
# and cannot be found by title. Its human-readable label is a SEPARATE sibling
# Text control at the SAME Y:
#
#   <Control Id="RegisterTrayCheck" X="25" Y="80" Property="REG_TRAY_APP" Text=" " />
#   <Control Id="RegisterTrayText"  X="40" Y="80" Text="!(loc....RegisterTrayCheckbox)" />
#
# The dev team's prototype coped by sorting the empty-text buttons by position
# and taking indices 0-3, which silently toggles the WRONG option the day the
# dialog is reordered -- and a test that toggles the wrong option still reports
# a pass. So the driver pairs each box with the LABEL beside it instead: same
# row, immediately to its left. Reordering the dialog then moves a label and its
# box together and nothing breaks; renaming a label fails loudly.
#
# This is the framework working around a product defect. The missing accessible
# names should be reported against TOOLS-4932 -- with them, all of this becomes
# a lookup by name.
# --------------------------------------------------------------------------- #
# THIS DICT IS THE DRIVER'S CONTRACT, NOT A GLOSSARY. `wizard.install` sets
# every option in it and refuses any non-default option that is NOT in it, so an
# entry here is a promise that the driver drives that option on
# InstallOptionsDlg. A label added for reference alone turns that promise into a
# silent no-op: the driver accepts the option, never clicks anything, and the
# resulting comparison reports a PRODUCT defect that is really a driver gap.
OPTION_LABELS = {
    # bundle variable  ->  the label text, every language it ships in
    "REG_TRAY_APP": ("Register Tray application to Windows Startup",
                     "자동 실행 목록에 Tray 애플리케이션 등록"),
    "CREATE_SHORTCUT": ("Create Desktop Shortcut", "바탕화면 바로가기 생성"),
    "CREATE_DEMODB": ('Create a sample "demodb" database',
                      '샘플 "demodb" 데이터베이스 생성'),
    "IS_WSL2_MODE": ("Install in WSL2 mode", "WSL2 모드로 설치"),
}

# START_TRAY_APP is on CustomFinishDlg, not InstallOptionsDlg: the wizard offers
# it only after the install has already run. It is kept OUT of OPTION_LABELS
# above so `_unsupported_options` refuses a scenario that asks for it, which is
# the honest answer until a driver step exists for the finish page. Its label is
# recorded here so that step is a lookup rather than a re-derivation.
FINISH_PAGE_OPTION_LABELS = {
    "START_TRAY_APP": ("Start Tray application after installation",
                       "설치 완료 후 Tray 애플리케이션 실행"),
}

# The WSL-name field, identified by the label beside it. The install-directory
# field needs no label: it is the only text field on its own page, so the driver
# matches it by kind (see wizard.install's _directory step).
WSL_NAME_LABEL = ("WSL Name:", "WSL 이름:")

# MSI's Edit and PathEdit controls are NOT window class "Edit". Observed on
# build 0003: the WSL-name field answers as "RichEdit20W" -- MSI hosts its text
# controls in the rich edit control, and which flavour replies depends on the
# riched DLL loaded, not on the .wxs. So the whole family is matched.
#
# Keep the whole family listed. A class filter that misses is indistinguishable
# from a control that is not there, so the driver reports "the dialog layout has
# changed" about a field sitting in plain sight. Matched case-insensitively --
# RichEdit 4.1 answers RICHEDIT50W in capitals.
TEXT_FIELD_CLASSES = ("Edit", "RichEdit20W", "RichEdit20A", "RichEdit50W")

# A checkbox and its label share a Y in dialog units. On screen they share a
# row, so pairing allows this much drift between their vertical centres --
# generous enough for DPI scaling and font metrics, far tighter than the 15
# dialog units between adjacent options.
OPTION_ROW_TOLERANCE_PX = 12

# Dialogs the wizard can stop on, from ActionEnvironmentCheck's own message
# tables. Every one of them is MODAL: the driver cannot advance past it, and a
# driver that only waits for the next page hangs on it until its timeout and
# then blames the missing page.
#
# All are fatal except `reboot_required`, which is advisory -- the dev team's
# prototype clicks OK on that one and carries on, and so does the driver.
WIZARD_WARNINGS = {
    "virtualization_disabled": ("Virtualization Technology Disabled",
                                "가상화 기술 비활성화"),
    "windows_version":         ("Windows Version Not Met", "Windows 버전 문제"),
    "administrator_rights":    ("Administrator Rights Not Met", "관리자 권한 문제"),
    "wsl_not_installed":       ("WSL Installation Not Met", "WSL 설치 미설치"),
    "reboot_required":         ("Reboot Required For WSL",
                                "WSL 위한 재부팅이 필요할 수 있습니다."),
}
ADVISORY_WARNING = "reboot_required"

# WiX's CustomFatalErrorDlg and Burn's own failure page. Detecting these is what
# turns a 15-minute timeout into a five-second failure carrying the real reason.
WIZARD_FAILURE_TITLES = ("Installation Failed", "설치 실패",
                         "Uninstall Failed", "제거 실패")

# The MSI dialogs' window class. The wizard's completion page shares its title
# with other windows on screen, so the class is what distinguishes it.
#
# CustomUserExit -- the page a CANCELLED install ends on -- is declared
# Title="[ProductName] Setup", and ProductName is "CUBRID For WSL", so its title
# is character-for-character the bundle window's. There is deliberately no
# separate string for it: a second copy of one literal is a copy that can drift.
# It is told apart the same way the completion page is, by class plus a Finish
# button, which is why both go through _is_completion_dialog.
MSI_DIALOG_CLASS_PREFIX = "MsiDialog"

# --------------------------------------------------------------------------- #
# The Tray application -- src/cubrid_tray_app.cpp
#
# The mutex is the cheapest reliable "is the Tray running" probe and needs no UI
# automation: the process creates it at startup (CreateMutexA) and Windows
# destroys it when the process exits, so its existence IS the answer. The window
# is a second, independent signal -- and it is deliberately HIDDEN, so it can
# only be found by class, never by enumerating visible windows.
# --------------------------------------------------------------------------- #
TRAY_MUTEX = r"Global\CUBRID_WSL_Tray_App_Mutex"
TRAY_WINDOW_CLASS = "CUBRIDTrayApp"

# The Tray executable, as installed. Named here because a clean-up uninstall
# stops it first -- see windows.tray.stop().
TRAY_EXE = "cubrid_tray_app.exe"

# The Tray's desktop shortcut, when the registry does not name it
# (`TrayAppLinkFile`). The distribution's shortcut is `<WslName>.lnk`.
TRAY_SHORTCUT_FILE = "cubrid_tray_app.lnk"

# The popup menu, its About dialog and the guide Guide opens. The menu labels are
# what the Tray appends at ShowContextMenu(); the driver matches on them because
# no persistent HMENU exists to index by command ID from outside the process.
TRAY_ABOUT_TITLE = "About CUBRID Service Tray"
TRAY_MENU_ITEMS = ("About", "CUBRID Start", "CUBRID Stop", "Guide", "Exit")
# TrayApp::MENU_EXIT. The one command sent WITHOUT opening the menu: a clean-up
# stop posts it so the Tray removes its own icon (`Shutdown()` -> NIM_DELETE).
TRAY_EXIT_COMMAND = 1005
# CUB_GUIDE_FILE in CMakeLists.txt, installed beside the Tray.
TRAY_GUIDE_FILE = "cubrid_guide.html"

# The notification-area tooltip, which is the only place the Tray REPORTS the
# service state it polled. Readable through UI Automation only.
TRAY_TIP_RUNNING = "CUBRID Service - Running"
TRAY_TIP_STOPPED = "CUBRID Service - Stopped"
TRAY_TIP_ERROR = "CUBRID Service - Error"

# Buttons on the environment-check warning dialogs.
OK_BUTTONS = ("OK", "확인")


def ui_strings(name: str) -> list[str]:
    """Every language's version of one wizard string."""
    values = [table[name] for table in WIZARD_STRINGS.values() if name in table]
    if not values:
        raise KeyError(f"no wizard UI string named {name!r} in constants.py")
    return values
