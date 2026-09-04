# cubrid-wsl-installer-test

QA automation for the **CUBRID For WSL Installer** (Jira TOOLS-4939, spec TOOLS-4932).

## 1 · Purpose

This is a **starter framework**, not a finished suite. It implements four
representative test cases end to end and provides the infrastructure those four
need — nothing more. The rest of the matrix in
`../documents/CUBRID_WSL_Installer_Test_Scenarios.xlsx` is for the QA team to add
on top of it.

Its one architectural idea is worth stating up front:

```
  drivers/    exercise the installer (silent CLI, wizard UI) -- they never assert
  state.py    read what is actually true on the machine     -- it never interprets
  verify.py   decide whether that state is what the options imply
  reset.py    return the machine to a known clean starting point
```

Both drivers assert through **one** verification layer. That is what makes "a
wizard install reaches the same state as a silent install" a tested fact rather
than a claim: adding a check to `verify.CHECKS` strengthens both tracks at once,
and neither can quietly drift from the other.

Two rules follow from the product's own behaviour and are not negotiable:

* **Never assert on an installer exit code alone.** `ActionUninstallWsl` is
  declared `Return="ignore"`, so the uninstaller reports success even when it
  fails to remove the distribution. Assert the machine.
* **A test never installs for itself.** It asks for a fixture. One install
  serves every test that shares it.

## 2 · Technology

| | |
|---|---|
| Language | Python 3.10+ |
| Test runner | pytest |
| Silent track | `subprocess` against the installer's own unattended CLI |
| UI track | pywinauto (window discovery via `ctypes`, which is far faster) |
| Verification | `winreg`, `wsl.exe`, the filesystem — all native Python |
| Reporting | JUnit XML + logs + JSON state snapshots under `reports/<timestamp>/` |
| PowerShell | only `run-tests.ps1`, the user-facing entry point |

## 3 · Structure

```text
cubrid-wsl-installer-test/
├── run-tests.ps1              # entry point: a thin wrapper over pytest
├── pyproject.toml             # dependencies, pytest config, markers
├── config/
│   ├── settings.toml          # committed defaults
│   └── settings.local.toml    # YOUR machine's installer path (gitignored)
├── src/cubridwsl/
│   ├── constants.py           # every product literal: registry paths, Run
│   │                          #   values, install options, wizard UI strings
│   ├── config.py              # settings loading, installer resolution
│   ├── preflight.py           # elevation and platform gates. Refuses; never repairs
│   ├── distro.py              # THE only wsl.exe call site
│   ├── state.py               # THE verification layer's eyes: one machine snapshot
│   ├── verify.py              # options -> expected state -> the difference
│   ├── reset.py               # uninstall, then prove the machine is clean
│   └── drivers/
│       ├── silent.py          # unattended CLI
│       └── wizard.py          # pywinauto, default path only
├── tests/
│   ├── conftest.py            # fixtures, including the two install fixtures
│   ├── test_environment.py    # framework self-check; read-only
│   ├── test_install_silent.py # INS-002
│   ├── test_install_wizard.py # INS-001
│   └── test_cubrid_runtime.py # OPS-001, OPS-002
└── reports/                   # gitignored run output
```

## 4 · Setup

### Prerequisites

| You need | For | Required? |
|---|---|---|
| Windows 10/11 with WSL 2 | everything — `wsl --status` must succeed | always |
| Python 3.10+ (python.org or the Python Install Manager, **not** the Microsoft Store) | the framework | always |
| An installer bundle `CUBRID-*-For-WSL-*-win64.exe` | anything that installs | always |
| An **Administrator** PowerShell | every suite except `environment` | mostly |
| pywinauto (`pip install -e .[ui]`) | the wizard suite | `ui` only |

### 1. Install Python

```powershell
winget install 9NQ7512CXL7T -e --accept-package-agreements
py install 3.12
```

Accept the prompt to add `%LocalAppData%\Python\bin` to PATH and open a new
shell. If `python` still fails, turn off the Store aliases under
**Settings → Apps → Advanced app settings → App execution aliases**.

### 2. Install the framework

```powershell
git clone https://github.com/kimhak-tech/cubrid-wsl-installer-test.git
cd cubrid-wsl-installer-test
python -m pip install -e .          # add .[ui] for the wizard suite
```

### 3. Name the installer bundle — *the step people miss*

The framework never downloads anything and never searches a build directory.
Create **`config/settings.local.toml`** (gitignored) and name the exact file:

```toml
[installer]
path = "D:/CUBRID/CUBRID-WSL/builds/CUBRID-11.4-For-WSL-1.0.0-0003-win64.exe"
```

Two things that catch people out:

* **The filename must keep its shipped form**, `CUBRID-<cubrid version>-For-WSL-<installer version>-<build>-win64.exe`. The version fields are read *out of* the name, and OPS-001 checks the CUBRID reported inside the distribution against them. A renamed file is refused rather than silently tested.
* **The filename is not an identity.** The build number is a commit count, so two different binaries can share a name. Every run records the bundle's SHA-256; that is what identifies the build a result came from.

For a single run against another build: `.\run-tests.ps1 -Installer <path>`.

### 4. Prove the setup before trusting any result

```powershell
.\run-tests.ps1 environment
```

Read-only, needs no installed product, takes seconds. If this fails, nothing
after it is meaningful.

## 5 · Running

```powershell
.\run-tests.ps1 environment    # framework self-check, read-only (default)
.\run-tests.ps1 silent         # INS-002, OPS-001, OPS-002   -- one install
.\run-tests.ps1 ui             # INS-001                     -- one install
.\run-tests.ps1 all            # everything                  -- two installs

.\run-tests.ps1 -Case INS-002        # one case
.\run-tests.ps1 -CollectOnly         # list what would run, and stop
.\run-tests.ps1 silent -Mode quiet   # /quiet instead of /passive
```

Equivalent direct invocations, for development:

```powershell
pytest -m environment
pytest -m silent
pytest -k ins_002
```

Each run writes `reports/<timestamp>/`: JUnit XML, the bundle and MSI logs, the
machine state as JSON, and the verification result.

> ### ⚠ These are real system-level tests
>
> Everything except `environment` **uninstalls whatever CUBRID For WSL is on
> this machine and installs the configured bundle.** It needs an elevated
> PowerShell, because the MSI carries a `Privileged` launch condition — and
> because the UAC prompt is drawn on the secure desktop, where no automation
> library can reach it.
>
> The `ui` suite additionally **drives the real mouse and keyboard**. Do not use
> the machine while it runs.
>
> Two safety rules are enforced in code rather than left to reviewers:
> `wsl --shutdown` is never issued (it is machine-global and would kill Docker
> Desktop too), and the default distribution is never used implicitly — every
> call names its target, and `safety.protected_distros` guards the rest.
>
> If the machine cannot be brought back to a clean state, `reset.py` **refuses
> and prints the exact commands** instead of deleting anything itself.

## 6 · Automated scenarios

| Case | Track | What it proves |
|---|---|---|
| **INS-001** | UI | A default install clicked through the real wizard reaches the same machine state as a silent one — asserted through the shared verification layer, over every check area. |
| **INS-002** | Silent | `IS_WSL2_MODE=1` produced a **version 2** distribution. Read straight from `wsl -l -v`, because the product imports without `--version` and ignores `--set-default-version`'s exit code, so a silently-WSL1 install is a real product risk. |
| **OPS-001** | Silent | `cubrid_rel` runs inside the distribution, and the version it reports contains the CUBRID version the installer's filename declares. |
| **OPS-002** | Silent | `$CUBRID`, `$CUBRID_DATABASES` and `$PATH` are correct in a **fresh login shell** — the check that catches a CRLF `~/.cubrid.sh`, which breaks every CUBRID command while the install still reports success. |

Everything else in the xlsx is deliberately **not** implemented.

## 7 · Adding a new test scenario

**a. A new case against a state that already exists** — this is most of them.
Write a function; ask for the fixture; assert. No new fixture, no new install:
session-scoped fixtures mean ten cases against one install still cost one
install.

```python
# tests/test_install_silent.py
def test_ins_016_desktop_shortcuts_exist(silent_install, note):
    problem = silent_install.comparison.problems("shortcuts")
    assert not problem, problem
```

Conventions, all of them load-bearing:

* **The case ID goes in the function name**, lower case with underscores
  (`test_ins_016_...`), so `-Case INS-016` and `pytest -k ins_016` find it. One
  case ID must have exactly one owner, or a tag filter silently means two things.
* **No product literals in a test file.** Registry paths, Run value names,
  window titles and menu IDs live in `constants.py`. A literal in a test is a
  literal nobody will find when the product changes.
* **Never `time.sleep()`.** Use `state.wait_until(predicate, settings, ...)`.
* **Say why the assertion failed.** `assert x == 2, "..."` — a failure message
  should let someone triage without reproducing the run.

**b. A new install-option combination.** Copy `constants.INSTALL_OPTIONS`,
override what you need, and add one fixture in `tests/conftest.py` that calls
`_provision` with it — the silent and wizard fixtures are two four-line examples.
Note that the wizard driver refuses non-default options on purpose: the five
option checkboxes are declared `Text=" "` in the product and expose no
accessible name, so driving them needs label-pairing by screen position, which
is not implemented.

**c. A new fact that every install should satisfy.** Add a `Check` to
`verify.CHECKS`, giving it an `area`. Both tracks pick it up immediately.

**d. Something read from the machine that `state.py` does not report yet.** Add
it to the relevant `read_*` function and its dataclass. Record errors per field
rather than raising: one unresponsive command must not mask the assertion a test
actually cared about.

### Known gaps a future case will need

* **The Tray is not covered.** Driving it needs no UI library — `PostMessage(WM_COMMAND, id)` to the `CUBRIDTrayApp` window works from `ctypes` — but *reading* its Start/Stop grey state does, because the menu exists only while it is open. Its tooltip is the readable status surface.
* **Shortcut *targets* are not resolved**, only existence. INS-016 needs the target, which means COM through PowerShell.
* **Prerequisite/environment gating (the ENV group) is structurally UI-only.** `ActionEnvironmentCheck` is sequenced in `InstallUISequence`, which silent installs skip entirely.
* **Custom install path is wizard-only.** Silent mode overwrites `INSTALLFOLDER` from the WSL name.

The previous framework — with a product-constants generator, tray control, MSI
log parsing and shortcut target resolution — is kept beside this one as
`cubrid-wsl-installer-test-old`. Lift from it; do not import it.
