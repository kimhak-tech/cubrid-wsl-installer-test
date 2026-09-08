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
│   ├── verify.py              # Expected vs actual verification
│   ├── distro.py              # WSL operations
│   ├── reset.py               # Machine cleanup
│   ├── preflight.py           # Environment checks
│   └── config.py              # Configuration loading
├── tests/
│   ├── conftest.py                       # Shared fixtures
│   ├── test_environment.py               # Framework self-checks
│   ├── test_install_wizard.py            # INS-001
│   ├── test_install_silent.py            # INS-002
│   └── test_install_wizard_all_custom.py # INS-004
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
  the name, and INS-001 checks the CUBRID reported inside the distribution
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
.\run-tests.ps1 -Case INS-001
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

| Case | Method | Machine state | Purpose |
|---|---|---|---|
| INS-001 | UI | Installed:default | Full wizard flow on defaults, **and the complete post-install state** |
| INS-002 | Silent | Installed:default (silent) | The same install through `/quiet`, **diffed against INS-001's machine** |
| INS-004 | UI | Installed:allCustom | Every option off its default through the wizard — five effects on five artifacts |

**Three cases, one assertion set.** INS-001 owns it, absorbing nine other rows
in the 2026-09-07 consolidation. INS-002 is the same install through the
unattended CLI — same defaults, same assertions, different driver. INS-004 is
the same assertion set again with the options inverted, which costs nothing
extra because every expectation is *derived* from the options the machine was
installed with: untick a box and the expectation moves with it.

INS-004 is wizard-only, and not by preference. The bundle forwards six
properties to the MSI and `INSTALLFOLDER` is not among them, and
`ActionUpdateInstallFolder` overwrites the folder under `UILevel < 5` anyway —
so no silent command line can express an install directory. A silent twin would
have to drop one of the five option effects.

### Why INS-002 is a diff, not a second success check

Re-listing INS-001's assertions against a silent install would recreate exactly
the duplication the matrix was cleaned up to remove. So INS-002 asserts three
things and no more:

1. the bundle exited acceptably, and **no UI was displayed** — read from the
   bundle's own log (`WixBundleUILevel = 2`), which is Burn recording what it
   *did*, not the command line restating what we asked for;
2. INS-001's post-install set holds, through the **same** `comparison` object —
   so a fact added to `verify.CHECKS` strengthens both tracks in one edit;
3. `state.diff()` — the resulting machine is **field-for-field equivalent** to
   the one the wizard produced. Any divergence is a product defect or an
   undocumented default, and this catches the class of defect nobody thought to
   write a check for.

Two fields are excluded from the diff, and only two: `cubrid.service_status_raw`
(contains process IDs) and `cubrid.errors`. An environment self-check asserts
both paths actually exist in the report — ignoring a field that isn't there
looks identical to ignoring one that is.

**Run order is load-bearing.** INS-001 must run first: INS-002 diffs against the
machine it leaves, and INS-001's own assertions read the live disk before the
silent install replaces it. `conftest.INSTALL_FIXTURE_ORDER` enforces it, sorting
on the *last* install fixture a test requests — INS-002 asks for both.

**Scope limit, from the workbook:** `/quiet` sets MSI UILevel 2, so
`ActionEnvironmentCheck` never runs. A green INS-002 is *no* evidence that the
prerequisite gate works — ENV-008 and ENV-009 own that.

### What INS-001 asserts

**One workbook case, one test function, one result** — the workbook has a single
Status cell for INS-001, so five test results would make "did INS-001 pass?" a
question with five answers. The test collects every problem and asserts once at
the end, so nothing is hidden behind a first failure; each hidden failure would
otherwise cost another ~90-second install to find.

The workbook's Expected Result has two sections, and so does the test:

**INSTALL COMPLETION** — the wizard reached and advanced every page
(`bundle-welcome` → `finish`), and all default components are present: WSL2
mode, demodb, Tray auto-start *and* immediate launch, both shortcuts.
Proceeding past the environment gate is the assertion absorbed from ENV-001.

**POST-INSTALL STATE — observed, no action performed.** Most of it is expressed
as ordinary `Check`s in `verify.CHECKS`, so it is asserted by the same
comparison every future case will use:

| Check |
|---|
| `service.master` / `.broker` / `.manager` — individually, after waiting |
| `service.broker.query_editor` / `.broker1` — the brokers, by name |
| `environment.CUBRID` / `.CUBRID_DATABASES` / `.PATH_has_cubrid_bin`, read in a fresh **login** shell |
| `environment.no_carriage_return` — the CRLF `~/.cubrid.sh` defect |
| `cubrid.demodb` — registered in `databases.txt` |
| `distro.present` / `.version` — `wsl -l -v` name and VERSION=2 |
| `arp.present` / `.publisher` |
| `startup.tray_app` — and `startup.starter` separately, never as "no CUBRID entries" |
| `shortcuts.{distro,tray}_link` |

Which deleted row each check came from is recorded in the workbook, not here.
The INS sheet was renumbered on 2026-09-08 and those old numbers now belong to
different cases, so a provenance tag in the code would resolve to the wrong row.

Two assertions stay in the test file because `compare(options, state)` has only
those two inputs and these compare the machine against the **bundle under
test**: `cubrid_rel` versus the version in the installer's filename (OPS-001),
and the Apps & Features `DisplayVersion` versus the same.

### Open product defect, and the coverage gap it caused (2026-09-08)

**The tray desktop shortcut names no target.** `CubridCustomActions.cpp` builds
it as `installDir + "\\" + trayAppFile`, and `InstallDir` is stored in the
registry **with a trailing backslash** — so the path becomes
`…\CUBRID-FOR-WSL\\cubrid_tray_app.exe` with a doubled separator, and Windows
saves the link with no LinkInfo `LocalBasePath`. The WSL shortcut is unaffected:
it is built from `wslPath`, which has no trailing separator, and resolves
correctly on every run.

The product fix is one line: strip the trailing separator, or use a path join.

**Until it lands, the target and icon assertions are removed.** INS-001 asserts
only that both `.lnk` files exist. That is a *reduction* from what the workbook
asks (INS-011, absorbing the deleted INS-016: *"a target that exists on disk
with the correct icon — not merely present by filename"*), taken deliberately
on 2026-09-08 because the feature is not implemented correctly yet.

The three checks per shortcut are written out in a comment in `verify.py`, and
both links are still **parsed and printed in every run** — so the evidence keeps
arriving and restoring the assertions is three lines. INS-001 prints the gap on
every run, passing or failing, so it cannot quietly become permanent.

### Two known coverage gaps against the workbook

Both were implemented, both worked, and both were removed on 2026-09-08 because
the product does not satisfy them yet. Neither is a decision that the assertion
does not matter — the data is still read and printed on **every run**, so the
evidence keeps arriving and restoring each is a small, named edit.

**1. The CUBRID `server` component.** INS-001 lists *"server, broker and manager
RUNNING"*. On a healthy install the `@ cubrid server status` section is
**empty**:

```
@ cubrid master status
++ cubrid master is running.
@ cubrid server status
@ cubrid pl status          ← empty: no database server started
```

That section lists *started databases*. `demodb` exists in `databases.txt`, but
nothing starts it — CUBRID's stock `cubrid.conf` leaves `server=` commented out,
so `cubrid service start` brings up the master, broker and manager and no
database. **Open with development:** should the image set `server=demodb`, or is
starting a database OPS-003's job? Restoring the check is adding `"server"` back
to `SERVICE_COMPONENTS_EXPECTED_RUNNING`.

**2. Desktop shortcut targets and icons.** See the product defect above.
Restoring is three `Check`s, written out in a comment in `verify.py`.

`master` is asserted although the workbook does not list it: it is the one
component the product itself guarantees, since `cubrid_starter.cpp` polls
`IsMasterRunning()` and returns as soon as it is up.

One assertion is **not made**, and is reported as a `GAP` line in the run notes
on every run, passing or failing: Windows optional features report Enabled
(INS-014). Reading them needs PowerShell, and the assertion is vacuous on a
machine where WSL is already enabled — it would pass without the installer
having done anything. It is surfaced rather than dropped, because an assertion
nobody can see missing is indistinguishable from one that passed.

### One install fixture per machine state, and no more

Each of `wizard_install`, `silent_install` and `wizard_all_custom_install`
establishes one machine state for one case, and each costs about ninety seconds
plus the uninstall before it. A fixture with no test behind it is that time
spent on nothing, so a new one is added only when a case needs a state none of
these produces. `silent.py` is also what `reset` uninstalls with, on every run
of every suite.

---

## 7. Adding a New Test Case

When adding a new scenario:

1. Use the workbook case ID in the test name, lower case with underscores
   (`test_ins_003_...`), so `-Case INS-003` finds it.
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
def test_ins_003_wsl1_mode(wsl1_install):
    problems = wsl1_install.comparison.problems("distro")
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
