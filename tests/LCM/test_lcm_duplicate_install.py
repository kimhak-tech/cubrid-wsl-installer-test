"""Lifecycle Management -- LCM-003 and LCM-004, installing over an installation.

Same steps and the same survival checks; only the route differs: LCM-003
double-clicks Bundle B (`wizard.install_cubrid_wsl`), LCM-004 runs it unattended
(`silent.install_cubrid_wsl`).

Verifies:
- launching a DIFFERENT BUILD over an existing installation is refused, at both
  UI levels
- LCM-003 additionally: the failure page says a version is already installed,
  not merely that setup failed
- LCM-004 additionally: the unattended run draws no UI at all
- the existing installation survives untouched and CUBRID keeps running

Bundle B comes from `installer.alternate_path`. "Different" is narrower than it
sounds: Burn gates on `WixBundleInstalled` and regenerates the BundleId every
build, so even a rebuild from identical source is refused -- only the
byte-identical .exe gets the maintenance page, which is LCM-002.

DESTRUCTIVE and elevated; LCM-003 also drives the real mouse and keyboard.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants
from cubridwsl.drivers import silent, wizard
from cubridwsl.windows import apps, registry, tray
from cubridwsl.wsl import cubrid, distro

pytestmark = [pytest.mark.destructive]


# =============================================================================
# LCM-003: A different bundle launched over an existing installation
# =============================================================================
# With Bundle A installed, launch Bundle B through the UI, and confirm the
# installer blocks it, tells the user why, and leaves Bundle A's installation
# alone.

@pytest.mark.ui
def test_lcm_003_a_different_bundle_is_blocked(suite_installation,
                                               alternate_installer,
                                               settings, run_dir, note):
    wsl_name = suite_installation.wsl_name

    # ----------------------------------------------------------------- #
    # 1. Baseline -- what "unchanged" is measured against
    # ----------------------------------------------------------------- #
    # Read as VALUES, and read HERE rather than in the fixture, so they
    # describe the machine as THIS case found it. The suite installs once, so
    # LCM-004 measures against what LCM-003 left behind.
    installed_registry = registry.values()
    installed_entry = apps.read()
    note(f"  LCM-003-bundle A   : {suite_installation.package.path.name} "
         f"-> distro={wsl_name!r}")
    note(f"  LCM-003-bundle B   : {alternate_installer.describe()}")

    # ----------------------------------------------------------------- #
    # 2. Action -- launch Bundle B through the UI
    # ----------------------------------------------------------------- #
    # No switches: the double-click the workbook describes. Burn's gate fires
    # only on an INSTALL action, which a bare launch performs.
    result = wizard.install_cubrid_wsl(alternate_installer, settings,
                                       run_dir / "lcm-003-bundle-b.log", note=note)
    for step in result.steps:
        note(f"  LCM-003-step       : {step.name:<18} [{step.backend}] "
             f"{step.action} ({step.seconds:.0f}s)")
    note(f"  LCM-003-result     : {result.describe()}")

    if result.timed_out:
        pytest.fail(f"launching Bundle B timed out after {result.duration_seconds:.0f}s, "
                    "so it never reached a page that waits for the user -- see "
                    "lcm-003-bundle-b.log")

    # ----------------------------------------------------------------- #
    # 3. Verification
    # ----------------------------------------------------------------- #
    # What the page is expected to say. Both are lists -- every language the
    # bundle can be running in, since it follows the system locale.
    setup_failed_header = constants.ui_strings("bundle_failure_header")
    already_installed_message = constants.ui_strings("already_installed_message")

    page = result.detail.get("page")
    header = result.detail.get("header", "")
    message = result.detail.get("message", "")
    note(f"  LCM-003-page       : {page!r}, headed {header!r}")
    note(f"  LCM-003-message    : {message!r}")

    # 1. The install FAILED -- the bundle is showing the page headed "Setup
    # Failed". Each wrong page needs a different fix: 'maintenance' means the
    # two configured bundles are the same build, 'install' means the existing
    # installation was not detected, 'success' means it installed over itself.
    assert page == "failure", (
        f"expected the {setup_failed_header} page; Bundle B answered with its "
        f"{page!r} page instead, headed {header!r}")

    # 2. And the page says WHY. The header alone does not tell the user that a
    # version is already installed, nor that they must uninstall it first.
    assert message, (
        f"the failure page carried no text containing {already_installed_message}")

    # The existing installation is UNTOUCHED.
    assert registry.values() == installed_registry, (
        "the product's registry key changed while a refused bundle ran over it:\n"
        f"      before: {installed_registry}\n"
        f"      after : {registry.values()}")

    assert apps.read() == installed_entry, (
        "the Apps & Features entry changed while a refused bundle ran over it:\n"
        f"      before: {installed_entry.describe()}\n"
        f"      after : {apps.read().describe()}")

    assert distro.exists(wsl_name), (
        f"the WSL distribution {wsl_name!r} is gone after a refused install")

    assert tray.is_running(), "the Tray is no longer running after a refused install"

    # The one thing none of the above can answer: the machine can match field
    # for field while the product inside the distribution has stopped working.
    assert cubrid.is_ready(wsl_name, settings), (
        "CUBRID is not running inside the distribution after the refused "
        f"install. Status: {cubrid.service_status(wsl_name, settings)}")

    # No clean-up here: `suite_installation` owns this machine and removes it
    # after the last case in this module.


# =============================================================================
# LCM-004: A different bundle run unattended over an existing installation
# =============================================================================
# With Bundle A installed, run Bundle B silently, and confirm the installer
# rejects it without drawing any UI and without touching Bundle A's
# installation.

@pytest.mark.silent
def test_lcm_004_a_different_bundle_is_rejected_silently(suite_installation,
                                                         alternate_installer,
                                                         settings, run_dir, note):
    wsl_name = suite_installation.wsl_name

    # ----------------------------------------------------------------- #
    # 1. Baseline -- what "unchanged" is measured against
    # ----------------------------------------------------------------- #
    installed_registry = registry.values()
    installed_entry = apps.read()
    note(f"  LCM-004-bundle A   : {suite_installation.package.path.name} "
         f"-> distro={wsl_name!r}")
    note(f"  LCM-004-bundle B   : {alternate_installer.describe()}")

    # ----------------------------------------------------------------- #
    # 2. Action -- run Bundle B unattended
    # ----------------------------------------------------------------- #
    # /quiet with no property overrides: a duplicate install is refused before
    # any property is read, so passing some would only obscure which run failed.
    result = silent.install_cubrid_wsl(alternate_installer, settings,
                                       run_dir / "lcm-004-bundle-b.log", note=note)
    note(f"  LCM-004-result     : {result.describe()}")
    note(f"  LCM-004-command    : {result.command}")

    # A refusal is decided before any package runs, so a run that takes the whole
    # install budget is not being refused -- it is doing something else.
    if result.timed_out:
        pytest.fail(f"running Bundle B timed out after {result.duration_seconds:.0f}s, "
                    "so it was not being refused -- see lcm-004-bundle-b.log")

    # ----------------------------------------------------------------- #
    # 3. Verification
    # ----------------------------------------------------------------- #
    # Recorded as EVIDENCE, not asserted: whether Burn writes the localized
    # message under /quiet, and to which log, has never been observed on a real
    # run. Tighten this into an assertion once it has been.
    refusal = silent.text_in_logs(
        result.logs, constants.ui_strings("already_installed_message"))
    note(f"  LCM-004-log        : {refusal!r}")
    note(f"  LCM-004-uilevel    : bundle recorded "
         f"{silent.ui_level_from_log(result.log_path)}")

    # NOT the banned exit-code check: that rule is about refusing to TRUST a
    # success. The claim here runs the other way -- a bundle that refused to
    # install cannot also be reporting that it installed.
    assert not result.ok, (
        f"Bundle B reported SUCCESS ({result.describe()}) for an install over a "
        "product that is already there")

    # Read AFTER the run: a /quiet bundle should never have drawn a window at any
    # point. `wizard` is safe to import here -- this reader is ctypes only.
    windows_open = wizard.setup_windows_open(constants.ui_strings("bundle_title"))
    assert not windows_open, (
        "an unattended run put a setup window on screen: "
        f"{[title for _handle, title in windows_open]}")

    # The existing installation is UNTOUCHED.
    assert registry.values() == installed_registry, (
        "the product's registry key changed while a refused bundle ran over it:\n"
        f"      before: {installed_registry}\n"
        f"      after : {registry.values()}")

    assert apps.read() == installed_entry, (
        "the Apps & Features entry changed while a refused bundle ran over it:\n"
        f"      before: {installed_entry.describe()}\n"
        f"      after : {apps.read().describe()}")

    assert distro.exists(wsl_name), (
        f"the WSL distribution {wsl_name!r} is gone after a refused install")

    assert tray.is_running(), "the Tray is no longer running after a refused install"

    assert cubrid.is_ready(wsl_name, settings), (
        "CUBRID is not running inside the distribution after the refused "
        f"install. Status: {cubrid.service_status(wsl_name, settings)}")

    # No clean-up here: `suite_installation` owns this machine and removes it
    # after the last case in this module.
