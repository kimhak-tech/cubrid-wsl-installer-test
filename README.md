# CUBRID WSL Installer Test Automation

QA automation framework for the **CUBRID For WSL Installer**.

**Jira:** TOOLS-4939 · TOOLS-4932

## 1. Purpose

The framework supports two automation approaches:

- **Silent / Unattended** — fast installation, uninstallation, and system-state verification.
- **pywinauto** — testing the actual installer wizard and UI interactions.

Both approaches use the same verification layer:

```text
Test Case
   │
   ├── Silent Driver
   └── UI Driver
          │
          ▼
     Machine State
          │
          ▼
      Verification
          │
          ▼
       PASS / FAIL
```

**Key principle:** drivers perform actions; the verification layer determines
whether the resulting system state is correct. A driver never asserts, and a
test never installs for itself — it asks for a fixture, so many tests share one
installation.

---

## 2. Technology

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| Test runner | pytest |
| Silent automation | Installer CLI + `subprocess` |
| UI automation | pywinauto |
| Verification | Python / Windows APIs / WSL |
| Reporting | JUnit XML + JSON + logs |
| Entry point | PowerShell |

---

## 3. Project Structure

```text
cubrid-wsl-installer-test/
├── run-tests.ps1              # Test entry point
├── pyproject.toml             # Dependencies and pytest configuration
├── config/
│   ├── settings.toml          # Framework defaults
│   └── settings.local.toml    # Local machine settings (gitignored)
├── src/cubridwsl/
│   ├── drivers/
│   │   ├── silent.py          # Silent/unattended installer
│   │   └── wizard.py          # Installer wizard
│   ├── constants.py           # All product values: registry paths, install
│   │                          #   options, wizard UI strings
│   ├── state.py               # Machine-state collection
│   ├── verify.py              # Expected vs actual verification
│   ├── distro.py              # WSL operations
│   ├── reset.py               # Machine cleanup
│   ├── preflight.py           # Environment checks
│   └── config.py              # Configuration loading
├── tests/
│   ├── conftest.py             # Shared fixtures
│   ├── test_environment.py     # Framework checks
│   ├── test_install_silent.py  # Silent installation cases
│   ├── test_install_wizard.py  # UI installation cases
│   └── test_cubrid_runtime.py  # CUBRID runtime cases
└── reports/                   # Test results (gitignored)
```

---

## 4. Setup

### Prerequisites

- Windows 10/11
- WSL 2
- Python 3.10+ — from python.org or the Python Install Manager, **not** the
  Microsoft Store, whose stub breaks automation
- CUBRID For WSL installer bundle
- Administrator PowerShell for installation tests
- pywinauto for UI tests

### Install

```powershell
git clone <repository-url>
cd cubrid-wsl-installer-test
python -m pip install -e .
```

For UI automation:

```powershell
python -m pip install -e ".[ui]"
```

Configure the installer path in:

```text
config/settings.local.toml
```

Example:

```toml
[installer]
path = "C:/CUBRID/build/CUBRID-11.4-For-WSL-1.0.0-0003-win64.exe"
```

Two things to know about that path:

- **Put it in `settings.local.toml`, not `settings.toml`.** The first is
  gitignored; the second is committed, so a path that exists only on your
  machine would break every teammate's clone. The environment check enforces this.
- **Keep the installer's shipped filename.** The version fields are read *out of*
  the name, and OPS-001 checks the CUBRID reported inside the distribution
  against them. A renamed file is refused rather than silently tested.

Run the environment check before running installation tests:

```powershell
.\run-tests.ps1 environment
```

It is read-only, needs no installed product, and takes seconds. If it fails,
nothing after it is meaningful.

---

## 5. Running Tests

```powershell
.\run-tests.ps1 environment
.\run-tests.ps1 silent
.\run-tests.ps1 ui
.\run-tests.ps1 all
```

Run a specific case:

```powershell
.\run-tests.ps1 -Case INS-002
```

List tests without executing:

```powershell
.\run-tests.ps1 -CollectOnly
```

Other options: `-Installer <path>` for a one-off build, and
`-Mode passive|quiet` to choose the bundle's UI mode — not cosmetic, since
`/passive` runs the installer's environment checks and `/quiet` skips them.

The environment checks always run first, whichever suite you ask for, so a
broken setup fails in a second rather than after a five-minute install.

Test results are stored under `reports/<timestamp>/`:

| File | Contents |
|---|---|
| `run.json` | which bundle (path + SHA-256), which account, elevated or not, which mode, and what each install did |
| `junit.xml` | machine-readable results |
| `install-*.log` | the installer's own logs, plus the MSI's |
| `state-*.json` | the machine as the verification layer saw it |
| `comparison-*.json` | every check, expected vs actual |

> **Warning:** Installation tests modify the Windows/WSL environment and require
> an elevated PowerShell. The UI tests also control the real mouse and keyboard.
>
> Two safety rules are enforced in code: `wsl --shutdown` is never issued (it is
> machine-global and would stop Docker Desktop too), and no distribution is ever
> touched implicitly — every call names its target, and `safety.protected_distros`
> guards the rest. If the machine cannot be returned to a clean state, the
> framework stops and prints the exact commands instead of deleting anything.

---

## 6. Current Automated Scenarios

| Case | Method | Purpose |
|---|---|---|
| INS-001 | UI | Verify installation through the real setup wizard |
| INS-002 | Silent | Verify the installed WSL distribution is WSL 2 |
| OPS-001 | Silent | Verify CUBRID runtime and version |
| OPS-002 | Silent | Verify CUBRID environment configuration |

These are **sample scenarios** used to validate the framework.
The remaining scenarios from the test-scenario workbook will be added
progressively by the QA team.

---

## 7. Adding a New Test Case

When adding a new scenario:

1. Use the workbook case ID in the test name, lower case with underscores
   (`test_ins_016_...`), so `-Case INS-016` finds it.
2. Reuse existing fixtures whenever possible.
3. Keep product-specific verification in the verification layer rather than
   duplicating it in tests.
4. Avoid hardcoded product values in test files — they belong in `constants.py`.
5. Keep each test independent and restore any machine state it changes. The
   install fixtures are shared for the whole session, so a test that stops the
   service hands the next test a stopped service.
6. Never `time.sleep()`. Use `state.wait_until(...)`.

Example:

```python
def test_ins_016_desktop_shortcuts(silent_install):
    problems = silent_install.comparison.problems("shortcuts")
    assert not problems, problems
```

### Where things go

| You are adding | It goes in |
|---|---|
| A case against an existing installation | a `tests/test_*.py` file — no new fixture |
| A fact every installation should satisfy | a `Check` in `verify.CHECKS` — both drivers pick it up |
| Something new read from the machine | the matching `read_*` in `state.py` |
| A registry path, option name or UI string | `constants.py` |
| A new install-option combination | a copy of `constants.INSTALL_OPTIONS` plus one fixture in `conftest.py` |

### Architecture Rule

> **Add test cases to the test layer. Add reusable product checks to the
> verification layer.**

This keeps the framework maintainable as the test suite grows.
