"""CUBRID Operational -- OPS-004, installing another CUBRID engine.

Installs an engine build over the one the image ships, inside the distribution.
CUBRID is NOT stopped first: the engine is replaced underneath a running
service, which is part of what this case exercises.

Verifies:
- the engine package downloads inside the distribution
- its installer runs to completion over the default installation
- `cubrid_rel` reports the version that package's filename carries
"""
from __future__ import annotations

import pytest

from cubridwsl import constants

# `destructive` because it replaces the engine inside the shared machine;
# `action` plus the case number is what orders it after every other OPS case.
pytestmark = [pytest.mark.silent, pytest.mark.destructive, pytest.mark.action]

CASE = "OPS-004"


def test_ops_004_install_a_new_cubrid_version_over_the_default(
        cubrid, settings, ops_evidence, ops_result, note, dump_json):
    """Fetch an engine package, install it, and check the version it reports."""
    problems: list[str] = []
    skipped: list[str] = []
    url = str(settings["upgrade"]["url"]).strip()
    budget = int(settings["timeouts"]["upgrade_seconds"])

    # ------------------------------------------------------------------ #
    # 1. Fetch
    # ------------------------------------------------------------------ #
    fetched, filename = cubrid.download(url, timeout=budget)
    ops_evidence(CASE, "wget", fetched)
    if not fetched.ok:
        # wget runs INSIDE the distribution, so this is the Windows host's
        # network. A machine that cannot reach the download host cannot run
        # this case, which is not the same as the product failing.
        problems.append(
            f"could not download the engine package from {url} "
            f"(rc={fetched.returncode}): "
            f"{fetched.stderr[:400] or fetched.stdout[:400]}. wget runs inside "
            "the distribution, so this is the Windows host's network. Point "
            "`upgrade.url` in config/settings.local.toml at a build this "
            "machine can reach.")
        skipped.extend(["the install", "the version check"])
        ops_result(CASE, problems, skipped)
        return

    # The version to expect, read out of the package's own name -- so the URL is
    # the only thing to change and the two can never disagree.
    match = constants.ENGINE_PACKAGE_RE.search(filename)
    expected = match.group("version") if match else None
    note(f"  {CASE}-package    : {filename} -> version {expected!r}")

    # ------------------------------------------------------------------ #
    # 2. Install it over the default
    # ------------------------------------------------------------------ #
    installed = cubrid.install_engine(
        filename, args=str(settings["upgrade"].get("installer_args") or ""),
        timeout=budget)
    ops_evidence(CASE, "install", installed)
    # The whole transcript, not the 400-character summary -- an interactive
    # installer answered blind is diagnosed from what it actually asked.
    dump_json("ops_004_installer", installed.as_dict())
    if not installed.ok:
        problems.append(
            f"the engine installer failed (rc={installed.returncode}). Its full "
            "transcript is in ops_004_installer.json; the LAST lines are the "
            "ones worth reading. It is run with "
            f"`{settings['upgrade'].get('installer_args')}` and every prompt "
            f"answered {constants.ENGINE_INSTALLER_ANSWER!r}.\n"
            f"{installed.stdout[-1500:]}")
        skipped.append("the version check")
        ops_result(CASE, problems, skipped)
        return

    # ------------------------------------------------------------------ #
    # 3. The installed engine is the one that was installed
    # ------------------------------------------------------------------ #
    reported = ops_evidence(CASE, "cubrid_rel", cubrid.run("cubrid_rel"))
    version = (reported.stdout or "").strip() or None
    note(f"  {CASE}-version    : cubrid_rel -> {version!r}")

    if not version:
        problems.append(
            "`cubrid_rel` produced no output after the install. That is the "
            "signature of a broken ~/.cubrid.sh -- if $CUBRID/bin is not on "
            "PATH then nothing in the distribution runs, while the installer "
            "still reported success.")
    elif expected is None:
        # Recorded as not evaluated, never as a pass: an assertion that could
        # not be made must not look like one that was.
        skipped.append(
            f"the version check -- {filename!r} carries no version to check "
            f"against, so `cubrid_rel` reporting {version!r} proves nothing "
            "about what was installed. Point `upgrade.url` at a named build "
            "rather than a `-latest-` one")
    elif expected not in version:
        where = ops_evidence(CASE, "engine-locations", cubrid.engine_locations())
        problems.append(
            f"the package installed was {filename!r}, so `cubrid_rel` should "
            f"report {expected!r} -- it reports {version!r}. Either the package "
            "was not what its name says, or it did not land over $CUBRID: an "
            "installer that extracts into a VERSIONED subdirectory leaves PATH "
            "pointing at the old engine, which reads exactly like this. CUBRID "
            f"trees in the guest home:\n{where.stdout}\n"
            "The installer transcript is in ops_004_installer.json.")
    else:
        note(f"  {CASE}-version    : matches the package -- {expected!r}")

    # No downgrade exists, so this is stated rather than undone. The case is
    # ordered last on this installation for that reason.
    note(f"  {CASE}-NOT RESTORED    : the engine in this distribution has been "
         "replaced and nothing puts the original back, so OPS-004 is specified "
         "to run last on this installation and the next install fixture cleans "
         "before it installs.")

    ops_result(CASE, problems, skipped)
