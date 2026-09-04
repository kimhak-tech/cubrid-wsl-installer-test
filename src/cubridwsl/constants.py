"""Facts about the product under test.

Every value here was read out of the CUBRID For WSL Installer source, and each
group names the file it came from so it can be re-checked when the product
changes. Keep product literals HERE and nowhere else: a registry path or a
window title inside a test file is a literal nobody will find when it changes.
"""
from __future__ import annotations

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
UNINSTALL_KEYS = (
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
)

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

# --------------------------------------------------------------------------- #
# Inside the distribution -- make_image/build_image.ps1 and the generated
# Dockerfile, which write ~/.cubrid.sh and have ~/.bash_profile source it.
#
# .bash_profile is read by LOGIN shells, which is why OPS-002 asks for one.
# --------------------------------------------------------------------------- #
CUBRID_HOME = "/home/cubrid/CUBRID"
CUBRID_DATABASES = "/home/cubrid/CUBRID/databases"

# The text the Tray itself matches to decide the service is up or down
# (src/cubrid_tray_app.cpp). Read the same output, agree with the product.
SERVICE_RUNNING_MARKER = "cubrid master is running"
SERVICE_STOPPED_MARKER = "cubrid master is not running"

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
    },
}

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
MSI_DIALOG_CLASS_PREFIX = "MsiDialog"

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
