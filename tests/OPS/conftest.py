"""Fixtures used only by the CUBRID Operational scenarios."""
from __future__ import annotations

from typing import Iterator

import pytest

from cubridwsl.provisioning import InstalledProduct, provide_installation

# ADDING A CASE: declare its markers in the test MODULE, not here. `pytestmark`
# in a conftest is SILENTLY INERT -- pytest honours it at module and class
# level only -- so markers placed here would leave `-m silent` missing the case
# and the `action` sort doing nothing, with no error to say so.


@pytest.fixture(scope="module")
def suite_installation(installer, settings, run_dir,
                       note) -> Iterator[InstalledProduct]:
    """One installation for every case in test_ops_cubrid_operational.py.

        uninstall  ->  install  ->  OPS-001 .. OPS-004  ->  uninstall

    The cases share it, so each one BEGINS by putting the machine into the
    state it needs -- service running, its database or table absent -- rather
    than trusting the case before it to have cleaned up. A case that fails
    part-way cannot then hand its mess to the next one.

    Module scope because that module IS the group, so the installation and
    the cases have one lifetime and the teardown lands after OPS-004 rather
    than at the end of the run.

    Deliberately absent from INSTALL_FIXTURE_ORDER: OPS-004 replaces the CUBRID
    engine and nothing undoes it, so this installation must never be one
    another category reads.
    """
    yield from provide_installation(installer, settings, run_dir, note, "ops")
