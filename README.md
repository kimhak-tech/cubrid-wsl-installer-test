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
installation. The exception is a case that **removes** the product: it cannot
share a machine with anything, so it installs and uninstalls in its own test
body (see `tests/LCM/`).

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
│   ├── windows/               # What the product left on WINDOWS, by surface
│   │   ├── registry.py        #   what the PRODUCT wrote about itself
│   │   ├── apps.py            #   the Apps & Features entry, and its
│   │   │                      #     Uninstall button
│   │   ├── tray.py            #   the Tray, as a process and as a file
│   │   ├── shortcuts.py       #   the desktop shortcuts the installer created
│   │   └── files.py           #   install directories and leftovers
│   ├── wsl/                   # What the product left INSIDE WSL
│   │   ├── distro.py          #   the distribution itself
│   │   └── cubrid.py          #   CUBRID running in it: service/server
│   │                          #     control, csql, createdb, engine install
│   ├── constants.py           # All product values: registry paths, install
│   │                          #   options, tray identifiers, wizard UI strings
│   ├── provisioning.py        # One installation shared by a category's cases
│   │                          #   and removed when they finish; a category's
│   │                          #   conftest picks the scope
│   ├── preflight.py           # Session preconditions: Windows, elevation, account
│   └── config.py              # Configuration loading
├── tests/
│   ├── conftest.py            # Shared fixtures: machine states, run report
│   ├── environment_checks.py  # Not cases: is this machine ready to run them
│   ├── framework_checks.py    # Not cases: do the framework's tools give right answers
│   ├── INS/                   # Category 02, Installer Orchestration
│   │   └── test_install_*.py
│   ├── OPS/                   # Category 03, CUBRID Operational
│   │   ├── conftest.py        #   one shared installation for the module below
│   │   └── test_ops_cubrid_operational.py
│   ├── TRA/                   # Category 04, CUBRID Control Tray
│   │   ├── conftest.py        #   the Tray driver and a CUBRID CLI bound to
│   │   │                      #     the machine `silent_install` left
│   │   └── test_tray.py
│   └── LCM/                   # Category 05, Lifecycle Management
│       ├── conftest.py        #   one shared installation, for the two cases
│       │                      #     below that do NOT consume it
│       ├── test_lcm_uninstall.py
│       └── test_lcm_duplicate_install.py
└── reports/                   # Test results (gitignored)
```

`windows/` and `wsl/` hold one module per surface of the machine, each
answering its own questions and performing the actions that surface has. A
function belongs in the module that owns its surface; do not add a shared
catch-all module to reuse it.

There are two ways a case gets a machine, and the difference is what the case
does to it. A **machine-state fixture** in `tests/conftest.py` establishes a
named state that the verification layer is then asked about, and holds it for
the session. `provisioning.py` is for a group of cases that needs nothing more
than a working installation to act against and wants it gone afterwards: a
category's own conftest declares the fixture at the scope its cases need and
delegates the body with `yield from`. Sharing either way is only sound for cases
that do not CONSUME the installation — LCM-001 and LCM-002 remove the product,
so they install for themselves inside the test body and take no such fixture.

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
.\run-tests.ps1 tray
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
.\run-tests.ps1 tray -CollectOnly     # list the Control Tray test cases
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
3. Write the body as numbered steps — `# 1. Set up`, `# 2. Action`,
   `# 3. Verification`, ... — each calling one function from `windows/` or
   `wsl/`, with a plain `assert` straight after the action it checks.
4. Act first, then verify what came back: run the command, keep its result,
   and assert on that result — `result.ok`, `result.value`,
   `result.rows_selected` — rather than calling a helper that answers yes or no.
5. A case that shares an installation starts by putting the machine into the
   state it needs (service running, no leftover database or table), rather
   than trusting the case before it to have cleaned up.
6. Declare a value used by one case inside that case. Only product values
   shared across cases — registry paths, option names, UI strings — go in
   `constants.py`.
7. Never `time.sleep()` in a test. An action waits until its change has landed
   (as `cubrid.service_start` does), so the assertion on the next line is not a
   race.

Example:

```python
def test_ops_005_something(suite_installation, settings, note):
    wsl_name = suite_installation.wsl_name
    demodb = constants.DEMODB_NAME

    # ----------------------------------------------------------------- #
    # 1. Set up -- demodb's server running
    # ----------------------------------------------------------------- #
    if not cubrid.is_server_started(wsl_name, demodb, settings):
        cubrid.server_start(wsl_name, demodb, settings)

    # ----------------------------------------------------------------- #
    # 2. Action -- query it
    # ----------------------------------------------------------------- #
    result = cubrid.csql(wsl_name, "SELECT 1", demodb, settings)
    note(f"  OPS-005-csql       : {result.describe()}")

    # ----------------------------------------------------------------- #
    # 3. Verification
    # ----------------------------------------------------------------- #
    assert result.ok and result.rows_selected == 1, (
        f"csql could not query {demodb}: {result.describe()}")
```

### Where things go

| You are adding | It goes in |
|---|---|
| A case | a file under the category's folder (`tests/INS/`, `tests/OPS/`, `tests/TRA/`, `tests/LCM/`), with its markers declared in the test module — `pytestmark` in a conftest is silently ignored |
| A new install-option combination | a copy of `constants.INSTALL_OPTIONS` plus one fixture in `tests/conftest.py` |
| A group of cases that only needs a working installation to act against | a `suite_installation` fixture in the category's conftest, delegating to `provisioning.provide_installation` — module scope, so it lasts exactly as long as the one file that uses it. It installs before the first case and uninstalls after the last |
| A case that REMOVES the product | the test body itself, set up with `silent.install_cubrid_wsl` — and **no machine-state fixture**, which is what keeps it ahead of every case that provisions one |
| A case that runs a SECOND bundle over an installation | the test body, reading the values it claims are unchanged before the action and comparing them after |
| A second bundle to test against | `installer.alternate_path` in `settings.local.toml` — any build other than the one under test. It must differ: the BundleId is regenerated on every build, so a rebuild from identical source is a different bundle, while the SAME file gets the maintenance page instead |
| Something read from Windows | the module that owns that surface — `windows/registry.py`, `windows/apps.py`, `windows/tray.py`, `windows/shortcuts.py`, `windows/files.py` — one question per function, answered as a plain value |
| Something read or done inside WSL | `wsl/distro.py` for the distribution itself; `wsl/cubrid.py` for CUBRID in it — service and server control, csql, createdb, engine install |
| A recorded product output format | a `parse_*` function in the `windows/` or `wsl/` module that reads it; the cases exercise it against the real product — no pasted sample |
| A product value shared across cases | `constants.py` |
| A framework function every case reads the product through | a check in `tests/framework_checks.py`, fed input whose right answer is known |

### Architecture Rule

> **Test cases contain intent: steps, actions and assertions. Reusable
> questions and actions against the machine go in `windows/` and `wsl/`, one
> module per surface.**

This keeps the framework maintainable as the test suite grows.
