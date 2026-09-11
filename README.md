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
│   │                          #   options, tray identifiers, wizard UI strings
│   ├── state.py               # Machine-state collection
│   ├── cubrid_cli.py          # CUBRID's own CLI inside the distribution:
│   │                          #   service/server control, csql, createdb
│   ├── verify.py              # Expected vs actual verification
│   ├── distro.py              # WSL operations
│   ├── reset.py               # Machine cleanup
│   ├── preflight.py           # Session preconditions: Windows, elevation, account
│   └── config.py              # Configuration loading
├── tests/
│   ├── conftest.py            # Shared fixtures: machine states, run report
│   ├── environment_checks.py  # Not cases: is this machine ready to run them
│   ├── framework_checks.py    # Not cases: do the framework's tools give right answers
│   ├── INS/                   # Category 02, Installer Orchestration
│   │   └── test_install_*.py
│   └── OPS/                   # Category 03, CUBRID Operational
│       ├── conftest.py        #   machine binding + precondition fixtures
│       └── test_*.py
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
  machine would break every teammate's clone. The environment checks enforce this.
- **Keep the installer's shipped filename.** The version fields are read *out of*
  the name, and INS-001 checks the CUBRID reported inside the distribution
  against them. A renamed file is refused rather than silently tested.

Then run the checks on their own:

```powershell
.\run-tests.ps1 checks
```

They also run automatically at the start of every suite; running them alone is
the quick way to check your setup. They are read-only, need no installed
product, and take seconds. If they fail, nothing after them is meaningful.

---

## 5. Running Tests

```powershell
.\run-tests.ps1 checks
.\run-tests.ps1 silent
.\run-tests.ps1 ui
.\run-tests.ps1 all
```

Run a specific case, or a whole category:

```powershell
.\run-tests.ps1 -Case INS-001
.\run-tests.ps1 -Case OPS       # a bare category runs every case in it
```

Every suite installs once per **machine state** it needs, not once per case —
which is what keeps a full run in minutes rather than hours. To see what a given
selection actually costs before running it, `pytest --setup-plan -m silent`
lists each session fixture exactly once where it is set up.

List every case without executing anything — this, not a table in this file, is
the current inventory:

```powershell
.\run-tests.ps1 -CollectOnly          # list every test case
.\run-tests.ps1 checks -CollectOnly   # list the environment and framework checks
.\run-tests.ps1 silent -CollectOnly   # list the silent test cases
.\run-tests.ps1 ui -CollectOnly       # list the UI test cases
```

What each case asserts is in its own file's docstring, under `Verifies:`.

Other options: `-Installer <path>` for a one-off build.

The bundle's UI mode is **not** a runner option. Each driver pins its own, and
the choice is not cosmetic: `/passive` runs the installer's environment checks
while `/quiet` skips them, so a case that names one has to keep it. Cleanup
always uninstalls `/quiet`, because a `/passive` uninstall draws a window the
wizard driver cannot tell apart from the one it is about to open.

Every run, including a single `-Case` (but not `-CollectOnly`, which only
lists), starts with two check files, once each:

| File | Question | Fails when |
|---|---|---|
| `tests/environment_checks.py` | Is **this machine** ready to run the cases? | the installer path is wrong, WSL does not answer, a setting is missing |
| `tests/framework_checks.py` | Do the **framework's own tools** give right answers? | a bug in `src/cubridwsl/` would mis-read the product on every machine |

Both always run, so one run shows every setup problem, and a failure in either
stops the run before anything is installed: a broken setup fails in a second
rather than after a two-minute install. Neither is a test case -- their
names do not match pytest's `test_*.py`, so they are only ever run by path -- and
the run ends with separate counts:

```text
Environment checks : 6 passed, 0 failed, 0 skipped
Framework checks   : 5 passed, 0 failed, 0 skipped
Cases              : N passed, 0 failed, 0 skipped
```

Only the last line, and `junit.xml`, count workbook cases.

Test results are stored under `reports/<timestamp>/`:

| File | Contents |
|---|---|
| `run.json` | which bundle (path + SHA-256), which account, elevated or not, which mode, and what each install did |
| `junit.xml` | machine-readable results, workbook cases only |
| `environment-checks/`, `framework-checks/` | each check session's own `junit.xml` and `run.json` |
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

## 6. Adding a New Test Case

When adding a new scenario:

1. Use the workbook case ID in the test name, lower case with underscores
   (`test_ins_005_...`), so `-Case INS-005` finds it.
2. Open the file with the standard docstring — title line, one or two lines of
   what the case is about, then a `Verifies:` bullet list of the assertions, and
   nothing else:

   ```python
   """CUBRID Operational -- OPS-001, service stop/start cycle.

   Asserts the TRANSITION. INS-001 owns the post-install "already running" state.

   Verifies:
   - `cubrid service stop` confirms master, broker and manager stopped
   - ...
   """
   ```

   No case-ID history, no dates, no "absorbed from" tags — IDs get renumbered
   and a stale one sends the reader to the wrong workbook row. Constraints a
   reader must not break belong inline, next to the code they govern.
3. Reuse existing fixtures whenever possible.
4. Keep product-specific verification in the verification layer rather than
   duplicating it in tests.
5. Avoid hardcoded product values in test files — they belong in `constants.py`.
6. Keep each test independent and restore any machine state it changes. The
   install fixtures are shared for the whole session, so a test that stops the
   service hands the next test a stopped service.
7. Never `time.sleep()`. Use `state.wait_until(...)`.

Example:

```python
def test_ins_005_something(silent_install):
    problems = silent_install.comparison.problems("distro")
    assert not problems, problems
```

### Where things go

| You are adding | It goes in |
|---|---|
| A case against an existing installation | a file under the category's folder (`tests/INS/`, `tests/OPS/`) — no new fixture |
| A fact every installation should satisfy | a `Check` in `verify.CHECKS` — both drivers pick it up |
| Something new read from the machine | the matching `read_*` in `state.py` |
| A registry path, option name or UI string | `constants.py` |
| A new install-option combination | a copy of `constants.INSTALL_OPTIONS` plus one fixture in `conftest.py` |
| An action against the *installed* product (start, stop, connect, query, create) | a method on `cubrid_cli.CubridCli`, and a case under `tests/OPS/` marked `action` |
| A recorded product output format | a `parse_*` function in `state.py`; the cases exercise it against the real product -- no pasted sample |
| A framework function every case reads the product through | a check in `tests/framework_checks.py`, fed input whose right answer is known |

### Architecture Rule

> **Add test cases to the test layer. Add reusable product checks to the
> verification layer.**

This keeps the framework maintainable as the test suite grows.
