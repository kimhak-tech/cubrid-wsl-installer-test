"""Fixtures used only by the Lifecycle Management scenarios."""
from __future__ import annotations

from typing import Iterator

import pytest

from cubridwsl.provisioning import InstalledProduct, provide_installation


@pytest.fixture(scope="module")
def suite_installation(installer, settings, run_dir,
                       note) -> Iterator[InstalledProduct]:
    """One installation for every case in this module.

        uninstall  ->  install  ->  LCM-003  ->  LCM-004  ->  uninstall

    Neither case consumes it -- each asserts the installation is untouched by
    the bundle it refuses -- which is what makes it shareable. LCM-001 and
    LCM-002 deliberately do not use this: removing the product is their
    subject, so each installs for itself.

    Module scope because this module IS the group, so the installation and the
    suite have one lifetime and the teardown lands after the last case rather
    than at the end of the run.

    Deliberately absent from INSTALL_FIXTURE_ORDER: LCM must keep scoring -1 in
    `_group_key` so it still runs ahead of every case that provisions a shared
    machine.
    """
    yield from provide_installation(installer, settings, run_dir, note, "lcm")
