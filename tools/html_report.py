"""Render one run's results as a single HTML page.

    python tools/html_report.py reports/<stamp>

Writes reports/<stamp>/report.html from the files the run already left there:
junit.xml for the cases, run.json for the builds and the machine, and
environment-checks/junit.xml only when no case ran. The page is a VIEW of
those files, never a second record of the result -- it can be regenerated for
any past run, and it cannot disagree with junit.xml.

Standard library only, so it runs wherever pytest ran.
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Same shape as `_CASE_ID` in tests/conftest.py: test_ops_002_... -> ("ops", "002").
_CASE_ID = re.compile(r"test_([a-z]+)_(\d+)_")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Case:
    name: str
    seconds: float
    status: str
    message: str = ""
    details: str = ""
    logs: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        """The workbook's category code: INS, OPS, LCM, TRA."""
        match = _CASE_ID.match(self.name)
        return match.group(1).upper() if match else ""


def _read_cases(junit: Path) -> tuple[list[Case], float]:
    """Every <testcase> in run order, and the session's wall-clock duration."""
    root = ET.parse(junit).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    cases = []
    for element in root.iter("testcase"):
        # A teardown error is reported on the same <testcase> as its call, so a
        # case that passed and then failed to clean up counts as failed -- as it
        # does in run-tests.ps1's own counts.
        problem = element.find("failure")
        if problem is None:
            problem = element.find("error")
        skipped = element.find("skipped")
        if problem is not None:
            status, node = FAIL, problem
        elif skipped is not None:
            status, node = SKIP, skipped
        else:
            status, node = PASS, None
        cases.append(Case(
            name=element.get("name", ""),
            seconds=float(element.get("time") or 0),
            status=status,
            message=(node.get("message", "") if node is not None else ""),
            details=((node.text or "").strip() if node is not None else ""),
        ))
    total = float(suite.get("time") or 0) if suite is not None else 0.0
    return cases, total


def _case_logs(run_dir: Path, name: str) -> list[str]:
    """The logs a case wrote itself, else its category's shared-install logs.

    INS cases name their logs `ins-001-*.log`; a category that shares one
    installation (`provisioning.provide_installation`) names them
    `<category>-shared-*.log`, and those are the only logs its cases have.
    """
    match = _CASE_ID.match(name)
    if not match:
        return []
    category, number = match.groups()
    own = sorted(run_dir.glob(f"{category}-{number}-*.log"))
    if own:
        return [p.name for p in own]
    return [p.name for p in sorted(run_dir.glob(f"{category}-shared-*.log"))]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _hms(seconds: float) -> tuple[int, int, int]:
    h, rest = divmod(round(seconds), 3600)
    return (h, *divmod(rest, 60))


def _clock(seconds: float) -> str:
    """Per-case duration as mm:ss (h:mm:ss past an hour)."""
    h, m, s = _hms(seconds)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _span(seconds: float) -> str:
    """Whole-run duration, e.g. 38m 37s."""
    h, m, s = _hms(seconds)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def _build_name(entry: object) -> str:
    """The bundle's filename from a run.json installer entry, "" if absent."""
    if isinstance(entry, dict) and entry.get("path"):
        return Path(str(entry["path"]).replace("\\", "/")).name
    return ""


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
_CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --text: #1d2330; --muted: #5f6b7a;
  --line: #dde2e8; --head: #eef1f5;
  --pass: #1a7f45; --pass-bg: #e3f4ea; --fail: #b42318; --fail-bg: #fde8e6;
  --skip: #6b7280; --skip-bg: #eceef1;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171c; --card: #1d2128; --text: #e6e9ee; --muted: #9aa4b1;
    --line: #2d333d; --head: #242a33;
    --pass: #5fd08e; --pass-bg: #1b3326; --fail: #ff8a80; --fail-bg: #3a1f1d;
    --skip: #b0b7c1; --skip-bg: #2a2f37;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 12px; }
h2 { font-size: 16px; margin: 28px 0 10px; }
.card { background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 20px; }
.facts { display: grid; grid-template-columns: max-content max-content 1fr;
  gap: 4px 10px; }
.facts dt, .facts .sep { color: var(--muted); }
.facts dd { margin: 0; }
.facts dd:not(.sep) { font-family: Consolas, "Cascadia Mono", monospace;
  overflow-wrap: anywhere; }
.tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
  margin-top: 20px; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.tile .label { color: var(--muted); font-size: 12px; }
.tile .value { font-size: 24px; font-weight: 600; }
.tile.pass .value { color: var(--pass); }
.tile.fail .value { color: var(--fail); }
.tile.skip .value { color: var(--skip); }
.table-wrap { overflow-x: auto; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
th { background: var(--head); font-weight: 600; }
tr:last-child td { border-bottom: none; }
td.name { font-family: Consolas, "Cascadia Mono", monospace; font-size: 13px; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; min-width: 44px; text-align: center;
  padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.badge.PASS { color: var(--pass); background: var(--pass-bg); }
.badge.FAIL { color: var(--fail); background: var(--fail-bg); }
.badge.SKIP { color: var(--skip); background: var(--skip-bg); }
tr.FAIL td { background: var(--fail-bg); }
a { color: inherit; }
.note { color: var(--muted); font-size: 12px; margin-top: 8px; }
.problem { background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--fail); border-radius: 8px; padding: 12px 16px;
  margin-bottom: 12px; }
.problem.SKIP { border-left-color: var(--skip); }
.problem .title { display: flex; justify-content: space-between; gap: 12px;
  font-family: Consolas, "Cascadia Mono", monospace; font-weight: 600; }
.problem .message { margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.problem pre { margin: 8px 0 0; padding: 10px; background: var(--head);
  border-radius: 6px; overflow-x: auto; font-size: 12px; }
.problem .logs { color: var(--muted); font-size: 13px; }
.empty { background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px; }
@media (max-width: 640px) {
  .tiles { grid-template-columns: repeat(2, 1fr); }
}
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _header(facts: dict, cases: list[Case], seconds: float) -> str:
    rows = [
        ("Base build", _build_name(facts.get("installer")) or "—"),
        ("Alternative build", _build_name(facts.get("alternate_installer"))
         or "— (not used in this run)"),
        ("Environment", facts.get("platform") or "—"),
        ("Test Date", str(facts.get("started", ""))[:16] or "—"),
    ]
    rows_html = "".join(
        f'<dt>{_e(label)}</dt><dd class="sep">:</dd><dd>{_e(value)}</dd>'
        for label, value in rows)
    passed = sum(c.status == PASS for c in cases)
    failed = sum(c.status == FAIL for c in cases)
    skipped = sum(c.status == SKIP for c in cases)
    tiles = [
        ("", "Test Duration", _span(seconds) if cases else "—"),
        ("", "Total", len(cases)),
        ("pass", "✓ Passed", passed),
        ("fail", "✕ Failed", failed),
        ("skip", "⏭ Skipped", skipped),
    ]
    tile_html = "".join(
        f'<div class="tile {cls}"><div class="label">{_e(label)}</div>'
        f'<div class="value">{_e(value)}</div></div>'
        for cls, label, value in tiles)
    return f"""
<section class="card">
  <h1>CUBRID WSL Installer</h1>
  <dl class="facts">{rows_html}</dl>
  <div class="tiles">{tile_html}</div>
</section>"""


def _anchor(case: Case) -> str:
    """The id of a case's Failures & Skips block, which its table row links to."""
    return "case-" + re.sub(r"[^a-z0-9_]", "", case.name.lower())


def _results_table(cases: list[Case]) -> str:
    table_rows = []
    for case in cases:
        # Only a failed or skipped case links: to its block under Failures & Skips.
        name = _e(case.name)
        if case.status != PASS:
            name = f'<a href="#{_anchor(case)}">{name}</a>'
        table_rows.append(
            f'<tr class="{case.status}"><td>{_e(case.category)}</td>'
            f'<td class="name">{name}</td>'
            f'<td><span class="badge {case.status}">{case.status}</span></td>'
            f'<td class="num">{_clock(case.seconds)}</td></tr>')
    return f"""
<h2>Overall Test Case Result</h2>
<div class="table-wrap"><table>
  <thead><tr><th>Category</th><th>Test Case</th><th>Status</th><th>Duration</th></tr></thead>
  <tbody>{"".join(table_rows)}</tbody>
</table></div>
<p class="note">A case's duration includes the installs and clean-ups it
triggered. A category that shares one installation counts it in the first case
that uses it.</p>"""


def _problems(cases: list[Case]) -> str:
    blocks = []
    for case in cases:
        if case.status == PASS:
            continue
        mark = "✕" if case.status == FAIL else "⏭"
        message = _e(case.message or "(no message recorded)")
        details = (f"<details><summary>Full output</summary>"
                   f"<pre>{_e(case.details)}</pre></details>"
                   if case.details and case.details != case.message else "")
        links = " · ".join(f'<a href="{_e(log)}">{_e(log)}</a>' for log in case.logs)
        logs = f'<div class="logs">Logs: {links}</div>' if links else ""
        blocks.append(f"""
<div class="problem {case.status}" id="{_anchor(case)}">
  <div class="title"><span>{mark} {_e(case.name)}</span>
    <span>{_clock(case.seconds)}</span></div>
  <div class="message">{message}</div>
  {details}
  {logs}
</div>""")
    if not blocks:
        return ""
    return "<h2>Failures &amp; Skips</h2>" + "".join(blocks)


def _no_cases(run_dir: Path) -> str:
    """Why the case table is empty: the checks stopped the run, or nothing matched."""
    checks = run_dir / "environment-checks" / "junit.xml"
    if checks.exists():
        failed = [c for c in _read_cases(checks)[0] if c.status == FAIL]
        if failed:
            items = "".join(
                f"<li><code>{_e(c.name)}</code><br>{_e(c.message)}</li>"
                for c in failed)
            return (f'<h2>No cases ran</h2><div class="empty">The environment '
                    f"checks failed, so nothing was installed.<ul>{items}</ul></div>")
    return ('<h2>No cases ran</h2><div class="empty">No test case was selected '
            "or the case session wrote no results.</div>")


def render(run_dir: Path) -> Path:
    facts = _read_json(run_dir / "run.json") or _read_json(
        run_dir / "environment-checks" / "run.json")
    junit = run_dir / "junit.xml"
    cases, seconds = _read_cases(junit) if junit.exists() else ([], 0.0)
    for case in cases:
        case.logs = _case_logs(run_dir, case.name)

    body = _header(facts, cases, seconds)
    body += (_results_table(cases) + _problems(cases)) if cases else _no_cases(run_dir)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CUBRID WSL Test Report {_e(run_dir.name)}</title>
<style>{_CSS}</style>
</head>
<body><main>{body}
</main></body>
</html>
"""
    out = run_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python tools/html_report.py reports/<stamp>", file=sys.stderr)
        return 2
    run_dir = Path(argv[1])
    if not run_dir.is_dir():
        print(f"not a report directory: {run_dir}", file=sys.stderr)
        return 2
    print(render(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
