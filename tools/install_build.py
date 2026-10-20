"""Install one build of CUBRID For WSL, and run no test case.

For preparing a machine -- manual testing, debugging -- with the same install
the shared test groups make: `provisioning.install_fresh`, so "installed" means
the same thing here as in a case. Called by install-build.ps1, which runs the
environment checks first.

Not a test case: nothing here is collected by pytest, nothing is counted, and
it judges nothing beyond "is the product installed". INS-002 is the case that
asserts what a default install should look like.

    python tools/install_build.py <report_dir> --installer=<path>

`--installer` is required: installer.path is not used here, because this
replaces whatever is installed, so the build is always named explicitly.

Writes into <report_dir>: notes.txt, install.json (which build, SHA-256, under
which account) and the two installer logs, build-before.log and
build-install.log.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from cubridwsl import config as config_mod
from cubridwsl import preflight, provisioning

LOG_PREFIX = "build"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install one CUBRID For WSL build unattended; run no test.")
    parser.add_argument("report_dir", type=Path,
                        help="Where the notes, install.json and logs go.")
    parser.add_argument("--installer", required=True,
                        help="Path to the installer bundle to install.")
    args = parser.parse_args(argv)

    report_dir: Path = args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    notes_path = report_dir / "notes.txt"

    def note(text: str) -> None:
        print(text, flush=True)
        with notes_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    facts: dict[str, Any] = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        **preflight.describe(),
        "result": "not started",
    }

    def record(result: str) -> None:
        facts["result"] = result
        facts["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_json(report_dir / "install.json", facts)

    try:
        settings = config_mod.load_settings()
        package = config_mod.resolve_installer(settings, args.installer)
    except config_mod.ConfigError as exc:
        note(f"REFUSED: {exc}")
        record(f"refused: {exc}")
        return 1

    note(f"  bundle     : {package.describe()}")
    facts["installer"] = package.as_dict()
    # Written BEFORE installing too, so an install that dies part-way still
    # leaves behind which build it was.
    _write_json(report_dir / "install.json", facts)

    try:
        product = provisioning.install_fresh(package, settings, report_dir,
                                             note, LOG_PREFIX)
    except (AssertionError, RuntimeError) as exc:
        # AssertionError: nothing is registered after the install.
        # RuntimeError: preflight's refusals and silent.InstallerError.
        reason = str(exc) or type(exc).__name__
        note(f"FAILED: {reason}")
        note(f"  logs       : {report_dir / f'{LOG_PREFIX}-install.log'}")
        record(f"failed: {reason}")
        return 1

    facts["wsl_name"] = product.wsl_name
    note(f"  installed  : distro {product.wsl_name!r}")
    record("installed")
    return 0


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
