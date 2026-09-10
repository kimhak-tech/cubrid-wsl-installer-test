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
# did rather than the framework inferring it from the command line. INS-002
# requires "no UI is displayed at any point"; this is the evidence.
#
# Observed: 4 on the INS-001 wizard run, 3 on a /passive uninstall.
# --------------------------------------------------------------------------- #
BURN_UI_LEVEL_VARIABLE = "WixBundleUILevel"
BURN_UI_LEVEL_NONE = 2          # /quiet   -- no UI at all
BURN_UI_LEVEL_PASSIVE = 3       # /passive -- a progress bar, no prompts
BURN_UI_LEVEL_FULL = 4          # the wizard

# --------------------------------------------------------------------------- #
# Install options -- the bal:Overridable variables in wix_src/bundle.wxs,
# forwarded to the MSI as properties. These are the shipping defaults.
#
# TO ADD A SCENARIO: copy this dict and override the keys you want, then pass it
# to the install fixture. See the README, "Adding a new test scenario".
# --------------------------------------------------------------------------- #
INSTALL_OPTIONS = {
    "REG_TRAY_APP": 1,          # register the Tray for auto-start at logon
    "CREATE_SHORTCUT": 1,       # two desktop shortcuts
    "CREATE_DEMODB": 1,         # create demodb inside the distribution
    "IS_WSL2_MODE": 1,          # import the distribution at WSL 2
    "START_TRAY_APP": 1,        # launch the Tray at the end of the install
    "CUB_DEFAULT_WSL_NAME": "CUBRID-FOR-WSL",
}

# The two a CASE has to name: INS-004 overrides the mode and reads it back out
# of the bundle's log, INS-006 overrides the name and reports which name was
# refused. A product literal in a test file is a bug, so they are named here.
# The other four are only ever dict keys, which is why they are not.
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

# The components INS-001 requires to be RUNNING after a default install.
#
# `master` is added to what the workbook lists because it is the ONE component
# the product itself guarantees: cubrid_starter.cpp polls `IsMasterRunning()`
# and returns as soon as it is up.
#
# `server` is PARSED but NOT REQUIRED, and this is a KNOWN COVERAGE GAP against
# the workbook rather than a judgement that it does not matter. INS-001 lists
# "server, broker and manager RUNNING"; the check was implemented, it worked,
# and it was then switched off deliberately.
#
# What it found, on build 11.4-1.0.0-0003 with everything else healthy:
#
#     @ cubrid server status
#     @ cubrid pl status          <- the section is EMPTY
#
# That section lists STARTED DATABASES. demodb exists in databases.txt, but
# nothing starts it: CUBRID's stock cubrid.conf leaves `server=` commented out,
# so `cubrid service start` brings up the master, the broker and the manager
# and no database. Whether that is a defect (the image should set
# `server=demodb`) or correct (starting a database is OPS-001's job) is open
# with development.
#
# Restoring the assertion is adding "server" back to this tuple. It is still
# read every run and printed in the notes, so the evidence keeps arriving.
SERVICE_COMPONENTS_EXPECTED_RUNNING = ("master", "broker", "manager")

# The subset the install fixture WAITS for, which is not the same list.
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

# `cubrid service start` and `cubrid service stop` report per component the same
# way `status` does -- one "@ cubrid <component> <verb>" section each, whose body
# carries the verdict:
#
#     @ cubrid master stop
#     ++ cubrid master stop: success
#
# OPS-001 requires the stop and start ACTIONS to be confirmed, not merely a zero
# exit code, so the command's own output is parsed (state.parse_service_command)
# rather than trusted. A section with neither marker reads None -- unknown, not
# failed. The server section is legitimately EMPTY when no database is started,
# which is the normal post-install state.
SERVICE_COMMAND_SUCCESS_MARKER = ": success"
SERVICE_COMMAND_FAILURE_MARKER = ": fail"

# --------------------------------------------------------------------------- #
# Category 03, CUBRID Operational
#
# Nothing here comes from the installer source: these are facts about CUBRID and
# about the demodb sample the image ships, so they are recorded rather than
# derived, exactly like SERVICE_EXPECTED_BROKERS.
# --------------------------------------------------------------------------- #
DEMODB_NAME = "demodb"

# The tables the shipped demodb sample carries -- CUBRID's "olympic" dataset.
#
# Asserted as a SUBSET of what `db_class` reports, never as equality: an added
# table is not a defect, a missing one is. The observed list is printed in the
# run notes on every run, passing or failing, so a wrong entry here is one
# constant edit and never a silent gap.
#
# Confirmed against a real machine: `db_class` reported exactly this set.
DEMODB_TABLES = ("athlete", "code", "event", "game", "history", "nation",
                 "olympic", "participant", "record", "stadium")

# The one table OPS-002 reads row-for-row. Small, so `SELECT *` is cheap.
DEMODB_KNOWN_TABLE = "code"

# CUBRID's built-in administrator. demodb ships with no password for it, which
# is why none is passed -- and why a password prompt would hang, so csql is
# always given a user explicitly.
CSQL_DBA = "dba"

# OPS-002's scratch table. Named so a leftover from a failed run is obviously
# this suite's and not a tester's.
OPS_SCRATCH_TABLE = "qa_ops_scratch"

# OPS-003 creates this database and deletes it again. `en_US` is the locale the
# workbook's step names.
OPS_TESTDB_NAME = "testdb"
OPS_TESTDB_LOCALE = "en_US"

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

# The system catalog view listing every class, and the flag separating the
# product's own catalog classes from a user's tables.
CATALOG_CLASS_VIEW = "db_class"
CATALOG_USER_CLASS_PREDICATE = "is_system_class = 'NO'"

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
OPTION_LABELS = {
    # bundle variable  ->  the label text, every language it ships in
    "REG_TRAY_APP": ("Register Tray application to Windows Startup",
                     "자동 실행 목록에 Tray 애플리케이션 등록"),
    "CREATE_SHORTCUT": ("Create Desktop Shortcut", "바탕화면 바로가기 생성"),
    "CREATE_DEMODB": ('Create a sample "demodb" database',
                      '샘플 "demodb" 데이터베이스 생성'),
    "IS_WSL2_MODE": ("Install in WSL2 mode", "WSL2 모드로 설치"),
    # On CustomFinishDlg, NOT InstallOptionsDlg -- the wizard offers this one
    # only at the end, so a scenario that changes it must act after the install.
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
# This cost a run: a class filter that misses is indistinguishable from a
# control that is not there, so the driver reported "the dialog layout has
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
# only be found by class and title, never by enumerating visible windows.
# --------------------------------------------------------------------------- #
TRAY_MUTEX = r"Global\CUBRID_WSL_Tray_App_Mutex"
TRAY_WINDOW_CLASS = "CUBRIDTrayApp"
TRAY_WINDOW_TITLE = "CUBRID Service Tray"

# The Tray executable, as installed. Named here because reset stops it before
# uninstalling -- see reset.stop_tray().
TRAY_EXE = "cubrid_tray_app.exe"

# Buttons on the environment-check warning dialogs.
OK_BUTTONS = ("OK", "확인")


def ui_strings(name: str) -> list[str]:
    """Every language's version of one wizard string."""
    values = [table[name] for table in WIZARD_STRINGS.values() if name in table]
    if not values:
        raise KeyError(f"no wizard UI string named {name!r} in constants.py")
    return values
