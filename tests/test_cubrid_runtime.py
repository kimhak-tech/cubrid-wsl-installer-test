"""CUBRID Operational -- what is true inside the distribution.

    OPS-001  Verify installed CUBRID version
    OPS-002  Verify the CUBRID environment is configured

Both share the silent install, so the two cases cost one install cycle between
them. DESTRUCTIVE through that fixture; needs an elevated shell.

Why these two matter: everything the installer writes on the Windows side can be
perfectly correct while CUBRID itself is unusable. That is not hypothetical --
it is the defect found on 2026-09-01, where the install reported success and
`cubrid_rel` was not on PATH because ~/.cubrid.sh had been baked into the image
with CRLF line endings.
"""
from __future__ import annotations

import pytest

from cubridwsl import constants, distro

pytestmark = [pytest.mark.silent, pytest.mark.destructive]


def test_ops_001_cubrid_reports_its_version_inside_the_distribution(
        silent_install, installer, note):
    """`cubrid_rel` runs, and reports the CUBRID the installer claims to ship.

    The cross-check against the artefact under test is the assertion nothing
    else makes: it proves the binary that was installed carries the CUBRID
    version its filename declares, rather than an image that has drifted from
    the installer shipping it.
    """
    cubrid = silent_install.state.cubrid
    assert not cubrid.errors, (
        "commands inside the distribution failed, so CUBRID is installed but "
        f"not usable: {cubrid.errors}")

    version = cubrid.cubrid_version
    assert version, (
        "`cubrid_rel` produced no output. This is the signature of a broken "
        "~/.cubrid.sh: if it carries CRLF line endings, $CUBRID/bin never "
        "reaches PATH and every product feature fails at once while the install "
        "still reports success.")
    note(f"  OPS-001    : cubrid_rel -> {version}")
    note(f"  OPS-001    : databases  -> {list(cubrid.databases)}  "
         f"service_running={cubrid.service_running}")

    declared = installer.cubrid_version            # e.g. "11.4", from the filename
    assert declared in version, (
        f"the installer declares CUBRID {declared} but the distribution reports "
        f"{version!r}. The image and the installer that ships it have diverged.")


def test_ops_002_cubrid_environment_persists_into_a_new_login_shell(
        silent_install, settings, note):
    """$CUBRID, $CUBRID_DATABASES and $PATH are set in a FRESH login shell.

    Two deliberate choices, and the case is worthless without either:

    * A **login** shell (`bash -lc`). The image writes ~/.cubrid.sh and has
      ~/.bash_profile source it, and .bash_profile is read by login shells. That
      is what "persists across new shell sessions, not just the install-time
      session" means in practice.
    * **No `env_setup` prefix.** Everywhere else the framework mirrors the
      product and prepends `. ~/.cubrid.sh`. Doing that here would source the
      file ourselves and answer a different question -- the environment would
      look correct even if nothing set it up for a real user session.

    The CR check is the specific defect this case exists to catch: a CRLF
    ~/.cubrid.sh makes every exported value end in a carriage return, so
    $CUBRID/bin and $CUBRID_DATABASES both point at directories that do not
    exist -- while the installer still reports success.
    """
    name = silent_install.state.registry.wsl_name
    assert name, "the registry did not record a WslName"

    result = distro.run(
        name,
        'echo "CUBRID=$CUBRID"; '
        'echo "CUBRID_DATABASES=$CUBRID_DATABASES"; '
        'echo "PATH=$PATH"',
        user=settings["distro"]["user"],
        login=True,                       # reads ~/.bash_profile
        env_setup="",                     # deliberately not sourced by us
        timeout=settings["timeouts"]["wsl_command_seconds"])

    assert result.ok, (f"a login shell inside {name!r} failed: "
                       f"rc={result.returncode} {result.stderr[:300]}")

    env = dict(line.split("=", 1) for line in result.stdout.splitlines()
               if "=" in line)
    for key in ("CUBRID", "CUBRID_DATABASES", "PATH"):
        note(f"  OPS-002    : {key}={env.get(key)!r}")

    assert "\r" not in result.stdout, (
        "an environment value carries a carriage return, which means "
        "~/.cubrid.sh was baked into the image with CRLF line endings. "
        "$CUBRID/bin and $CUBRID_DATABASES then both name directories that do "
        "not exist, and every CUBRID command fails while the install still "
        "reports success. Report this against the image build, not the test.")

    assert env.get("CUBRID") == constants.CUBRID_HOME, (
        f"$CUBRID is {env.get('CUBRID')!r} in a fresh login shell, expected "
        f"{constants.CUBRID_HOME!r}. If it is empty, ~/.bash_profile is not "
        "sourcing ~/.cubrid.sh and nothing in the product works from a user "
        "session.")
    assert env.get("CUBRID_DATABASES") == constants.CUBRID_DATABASES, (
        f"$CUBRID_DATABASES is {env.get('CUBRID_DATABASES')!r}, expected "
        f"{constants.CUBRID_DATABASES!r}")

    bin_dir = f"{constants.CUBRID_HOME}/bin"
    assert bin_dir in (env.get("PATH") or "").split(":"), (
        f"{bin_dir} is not on $PATH in a fresh login shell, so `cubrid`, "
        f"`cubrid_rel` and `csql` are not runnable by a user. $PATH was "
        f"{env.get('PATH')!r}")
