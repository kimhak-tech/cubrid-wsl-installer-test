"""Configuration loading and installer resolution.

Nothing else in the framework reads TOML or looks at the build directory.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:                                    # Python 3.11+
    import tomllib
except ModuleNotFoundError:             # Python 3.10
    import tomli as tomllib             # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

# CUBRID-<cubrid version>-For-WSL-<installer version>-<build>-win64.exe
INSTALLER_RE = re.compile(
    r"^CUBRID-(?P<cubrid_version>[0-9][0-9.]*)-For-WSL-"
    r"(?P<installer_version>[0-9][0-9.]*)-(?P<build>[0-9]+)-win64\.exe$",
    re.IGNORECASE,
)


class ConfigError(RuntimeError):
    """Configuration is missing, malformed, or cannot be satisfied."""


def _deep_merge(base: dict, over: dict) -> dict:
    """Merge `over` onto `base`, recursing into tables instead of replacing them."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """settings.toml, with settings.local.toml deep-merged over it."""
    config_dir = config_dir or CONFIG_DIR
    base_path = config_dir / "settings.toml"
    if not base_path.is_file():
        raise ConfigError(f"missing {base_path}")
    with base_path.open("rb") as fh:
        settings = tomllib.load(fh)

    local_path = config_dir / "settings.local.toml"
    if local_path.is_file():
        with local_path.open("rb") as fh:
            settings = _deep_merge(settings, tomllib.load(fh))
        settings.setdefault("_meta", {})["local_overrides"] = str(local_path)
    return settings


# The keys under [installer] that name a file on THIS machine. Both must stay
# empty in the tracked settings.toml; the environment checks enforce it by
# reading this tuple, so a third bundle added later is covered without an edit
# there.
MACHINE_SPECIFIC_INSTALLER_KEYS = ("path", "alternate_path")


def committed_installer_path(key: str = "path",
                             config_dir: Path | None = None) -> str:
    """`installer.<key>` as the COMMITTED settings.toml carries it.

    Exists so the environment checks can catch a machine-specific path edited
    into the tracked file instead of settings.local.toml. That mistake works
    perfectly on the machine that makes it and hands everyone else a path that
    does not exist -- the worst shape a setup error can take.
    """
    config_dir = config_dir or CONFIG_DIR
    with (config_dir / "settings.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return str(data.get("installer", {}).get(key) or "").strip()


@dataclass
class InstallerPackage:
    """A resolved installer bundle.

    The filename is NOT a content identity: `build` is `git rev-list --count`,
    so two different binaries can share a name -- two such files already exist
    in this workspace. Report `sha256` alongside the version, always.

    The three version fields are empty for a bundle resolved by
    `resolve_alternate_installer`, whose filename is not required to parse. Read
    them only from the bundle under test.
    """

    path: Path
    size: int
    cubrid_version: str = ""
    installer_version: str = ""
    build: str = ""
    _sha256: str | None = field(default=None, repr=False)

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256()
            with self.path.open("rb") as fh:      # hundreds of MB; read in chunks
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._sha256 = digest.hexdigest()
        return self._sha256

    def describe(self) -> str:
        # The version clause is dropped rather than printed empty for a bundle
        # whose name did not parse -- "CUBRID  installer  build" reads like
        # three fields the product failed to report, when in fact nothing ever
        # asked for them. See resolve_alternate_installer.
        versions = (f"CUBRID {self.cubrid_version}  installer "
                    f"{self.installer_version} build {self.build}  "
                    if self.cubrid_version else "")
        return (f"{self.path.name}  {versions}"
                f"{self.size:,} bytes  sha256={self.sha256[:16]}...")


def resolve_installer(settings: dict[str, Any],
                      override: str | Path | None = None) -> InstallerPackage:
    """The bundle to test: `installer.path`, or --installer for a single run.

    There is deliberately no search of a build directory. Naming the exact file
    is the tester's job, and it is what makes a result quotable.
    """
    configured = str(settings.get("installer", {}).get("path") or "").strip()
    chosen = override or configured
    source = "the --installer option" if override else "installer.path"

    if not chosen:
        raise ConfigError(
            "no installer configured. Name the exact bundle to test in "
            "config/settings.local.toml:\n\n"
            "  [installer]\n"
            '  path = "D:/path/to/CUBRID-11.4-For-WSL-1.0.0-0003-win64.exe"')

    path = Path(chosen).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"the installer named by {source} is not on disk:\n  {path}")

    match = INSTALLER_RE.match(path.name)
    if match is None:
        raise ConfigError(
            f"the installer named by {source} does not match the shipped "
            f"filename pattern:\n  {path.name}\n"
            "Expected CUBRID-<cubrid version>-For-WSL-<installer version>-"
            "<build>-win64.exe.\n"
            "The version fields are read OUT of the name -- INS-001 checks the "
            "CUBRID reported inside the distribution against them -- so a "
            "renamed file is refused rather than silently tested.")

    return InstallerPackage(path=path, size=path.stat().st_size, **match.groupdict())


def resolve_alternate_installer(settings: dict[str, Any]) -> InstallerPackage:
    """Bundle B: a DIFFERENT BUILD of the installer, for the duplicate-install cases.

    What makes a bundle "different" here is narrower than it looks, and it is
    the whole reason this resolver exists. Burn's already-installed gate is
    `WixBundleInstalled` -- "is THIS bundle registered" -- and the BundleId is
    regenerated on every build. `Bundle Version` is hardcoded 1.0.0 and the
    UpgradeCode is fixed, so the version fields say nothing about it:

        the byte-identical .exe that installed the product  -> maintenance page
        ANY other build, same source and version included   -> refused

    Confirmed by hand on 2026-09-14 with two builds that share a filename.

    Two differences from `resolve_installer`, both following from that:

    * **The filename is not required to parse.** A second build kept for this
      purpose is named by hand -- `...-win64-old.exe` is the one on the
      reference machine -- and refusing it would refuse the only kind of file
      this is ever pointed at. Nothing reads a version off Bundle B; what
      identifies it is the SHA-256, which is recorded like Bundle A's.
    * **There is no --installer override.** The bundle under test is chosen per
      run; its counterpart is a property of the machine's build collection.
    """
    configured = str(settings.get("installer", {}).get("alternate_path") or "").strip()
    if not configured:
        raise ConfigError(
            "no second bundle configured, so the duplicate-install cases have "
            "nothing to launch. Name a DIFFERENT BUILD of the installer in "
            "config/settings.local.toml -- any other build will do, including "
            "one of the same version:\n\n"
            "  [installer]\n"
            '  alternate_path = "D:/path/to/CUBRID-11.4-For-WSL-1.0.0-0003-'
            'win64-old.exe"')

    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(
            f"the bundle named by installer.alternate_path is not on disk:\n  {path}")

    match = INSTALLER_RE.match(path.name)
    return InstallerPackage(path=path, size=path.stat().st_size,
                            **(match.groupdict() if match else {}))
