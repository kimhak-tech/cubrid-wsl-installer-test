"""One installation, shared by a group of cases and removed when they finish.

The counterpart to `conftest._provision`, and deliberately not the same thing.
`_provision` establishes a NAMED MACHINE STATE that the verification layer is
then asked about -- it snapshots the machine, builds an `Installation` and
holds it for the rest of the session. This is for a group of cases that needs
nothing more than a working installation to act against, and that wants it gone
again afterwards.

It is a generator rather than a fixture because the SCOPE is what differs
between groups, and a fixture's scope is fixed by its decorator. Each category's
conftest declares its own fixture at the width its cases need and delegates the
body here with `yield from`, which passes on both the value and the resumption:

    # tests/<CATEGORY>/conftest.py
    @pytest.fixture(scope="module")        # or "package", for a group of files
    def suite_installation(installer, settings, run_dir, note):
        yield from provide_installation(installer, settings, run_dir, note,
                                        "<label>")

Sharing one installation is only sound for cases that do not CONSUME it. A case
whose subject is the removal of the product must install for itself, or the
case that follows inherits a machine with nothing on it.

No pytest import: this package is the framework the tests call, not a plugin,
and the gate below is a plain assert -- which is what `_provision` uses for the
same job. pytest reports an AssertionError raised in a fixture as an error
against every case that requested it, carrying the message.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from . import config as config_mod
from . import preflight
from .drivers import silent
from .windows import registry

Note = Callable[[str], None]


@dataclass(frozen=True)
class InstalledProduct:
    """A CUBRID For WSL installation, and which bundle produced it.

    Both fields exist so that a case never has to ask for the `installer`
    fixture alongside this one. A case that read the bundle from one place and
    the machine from another could report a filename that is not what is
    installed.
    """

    package: config_mod.InstallerPackage
    wsl_name: str


def provide_installation(installer: config_mod.InstallerPackage,
                         settings: dict[str, Any], run_dir: Path, note: Note,
                         label: str) -> Iterator[InstalledProduct]:
    """Install, hand the installation to a group of cases, then remove it.

    `label` names the group in the run notes and prefixes its three logs, so
    two groups provisioning in one session do not overwrite each other's.

    The opening uninstall makes the install's precondition true rather than
    assumed, and costs nothing when it is already true -- `uninstall_cubrid_wsl`
    returns without running the bundle when nothing is registered. It is also
    the only recovery from a run that was killed before its teardown could run.
    """
    preflight.require_windows()
    preflight.require_elevation()

    note(f"== provisioning one installation for the {label} cases ==")
    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / f"{label}-shared-before.log", note=note)
    silent.install_cubrid_wsl(installer, settings,
                              run_dir / f"{label}-shared-install.log", note=note)

    # Assert the MACHINE, never the bundle's exit code -- `install_cubrid_wsl`
    # reports a failed install rather than raising, so nothing above this line
    # has established that anything was installed at all.
    assert registry.exists(), (
        f"CUBRID for WSL is not installed after the {label} set-up step -- see "
        f"{label}-shared-install.log")

    yield InstalledProduct(package=installer, wsl_name=registry.wsl_name())

    silent.uninstall_cubrid_wsl(installer, settings,
                                run_dir / f"{label}-shared-after.log", note=note)
