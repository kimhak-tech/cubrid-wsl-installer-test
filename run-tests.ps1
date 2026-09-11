#Requires -Version 5.1
<#
    User-facing entry point: a thin wrapper over pytest. Keeping this interface
    stable means the runner underneath can change without retraining anyone.

        .\run-tests.ps1 checks        # the two check files only, read-only (default)
        .\run-tests.ps1 silent        # every case with no UI dependency  DESTRUCTIVE
        .\run-tests.ps1 ui            # every case driven through the wizard  DESTRUCTIVE
        .\run-tests.ps1 all           # both  DESTRUCTIVE

    Every run starts with tests/environment_checks.py (is this machine ready?)
    and tests/framework_checks.py (do the framework's tools give right
    answers?), once each, as their own pytest sessions -- for every suite and
    every -Case; -CollectOnly only lists. Both always run, and a failure in
    either stops the run before anything is installed. They are not test cases,
    so the case session's pass count and reports/<stamp>/junit.xml hold
    workbook cases only.

    Suites are described by what they SELECT, never by a list of case IDs: a
    list here goes stale the day a case is added and nothing fails when it does.
    -CollectOnly is the inventory: with no suite named it lists every case, and
    with one it lists what that suite would run.

    One case by its ID:   .\run-tests.ps1 -Case INS-001
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('checks', 'silent', 'ui', 'all')]
    [string]$Suite = 'checks',

    # A workbook case ID (-Case INS-001) or a whole category (-Case INS). Case
    # IDs are part of the test function names, so this becomes a pytest -k
    # filter. It overrides -Suite. The hyphen is translated to an underscore
    # because pytest's -k expression grammar does not accept one.
    [string]$Case,

    [string]$Installer,

    # List what WOULD run, and stop. The only risk-free way to check a filter
    # against a suite that installs and uninstalls the product.
    [switch]$CollectOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $root
try {
    # Probe for a working interpreter rather than pattern-matching its path:
    # the Microsoft Store STUB and the Python Install Manager's real launcher
    # both live under WindowsApps, so the path tells you nothing.
    $python = $null; $prefix = @()
    foreach ($cand in @(
        @{ Exe = 'python'; Pre = @() },
        @{ Exe = 'py';     Pre = @('-3') }
    )) {
        $cmd = Get-Command $cand.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $probe = & $cmd.Source @($cand.Pre + @('-c', 'import sys; print(sys.executable)')) 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) {
            $python = $cmd.Source; $prefix = $cand.Pre
            Write-Host "python: $($probe.Trim())" -ForegroundColor DarkGray
            break
        }
    }
    if (-not $python) {
        throw @"
No working Python found.

    winget install 9NQ7512CXL7T -e --accept-package-agreements
    py install 3.12

Accept the prompt to add %LocalAppData%\Python\bin to PATH, then open a new
shell. If 'python' still fails, turn off the Store aliases under
Settings > Apps > Advanced app settings > App execution aliases.
"@
    }

    $isAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    # Everything except 'checks' installs the product, and the MSI carries a
    # Privileged launch condition. Fail here with one clear line rather than
    # the same stack trace once per collected test.
    $needsAdmin = $CollectOnly -eq $false -and ($Case -or $Suite -ne 'checks')
    if ($needsAdmin -and -not $isAdmin) {
        throw ("This run installs and uninstalls CUBRID For WSL on this " +
               "machine, which requires an elevated shell. Re-run from an " +
               "Administrator PowerShell.")
    }

    $checkFiles = @('tests/environment_checks.py', 'tests/framework_checks.py')

    # What the CASE session selects. The check files never match pytest's
    # test_*.py pattern, so no selection below can pull them into it.
    if ($Case) {
        # Anchored as `test_<id>_`, never the bare ID. `-k` is a plain SUBSTRING
        # match over the whole test name, so a bare `ops` also selects every
        # test whose name merely CONTAINS it, and `-Case INS` could reach
        # `test_ops_004_install_a_new_cubrid_version...`, which replaces the
        # CUBRID engine inside the shared machine. A selection that silently
        # does an engine swap is the worst shape a filter bug can take. Every
        # case function is named `test_<category>_<number>_...`, so anchoring on
        # that prefix makes INS mean INS and INS-001 mean one case.
        $filter = $Case.Replace('-', '_').ToLower()
        if (-not $filter.StartsWith('test_')) { $filter = "test_$filter" }
        if (-not $filter.EndsWith('_'))       { $filter = "${filter}_" }
        $selection = @('-k', $filter)
    } else {
        switch ($Suite) {
            'checks' {
                # `checks` is the default so that a bare run is read-only. A
                # bare -CollectOnly is a request for the case INVENTORY, which
                # the default must not turn into a list of the checks. Typing
                # `checks` yourself still lists them.
                if ($CollectOnly -and -not $PSBoundParameters.ContainsKey('Suite')) {
                    $selection = @()
                } else {
                    $selection = $checkFiles
                }
            }
            'silent' { $selection = @('-m', 'silent') }
            'ui'     { $selection = @('-m', 'ui') }
            'all'    { $selection = @() }
        }
    }

    $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
    $runDir = "reports/$stamp"

    # One pytest session, reporting into $ReportDir.
    #
    # Each session gets its own directory because conftest writes run.json
    # beside the --junitxml path, and INS-002 takes a LATER run's baseline from
    # reports/<stamp>/run.json and junit.xml. Those two files must be the CASE
    # session's; a check session writing there would replace them.
    #
    # --basetemp keeps pytest's scratch inside this run's own directory.
    # Without it pytest shares %LOCALAPPDATA%\Temp\pytest-of-<user>, where files
    # created by an elevated run inherit administrator ACLs and a later ordinary
    # run dies during cleanup -- after every test has already passed.
    #
    # Returns nothing: called as a statement, pytest writes straight to the
    # console, and the caller reads $LASTEXITCODE. Assigning the call to a
    # variable would capture pytest's output instead of showing it.
    function Invoke-Pytest([string[]]$Selection, [string]$ReportDir,
                           [string[]]$Extra = @()) {
        # Not named $args: that is a PowerShell automatic variable.
        $pytestArgs = @('-m', 'pytest') + $Selection + $Extra
        if ($Installer) { $pytestArgs += @('--installer', $Installer) }
        $pytestArgs += @('--junitxml', "$ReportDir/junit.xml",
                         '--basetemp', "$ReportDir/tmp", '-v')
        New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
        Write-Host "python $($pytestArgs -join ' ')" -ForegroundColor DarkGray
        & $python @($prefix + $pytestArgs)
    }

    # One line of counts from a session's JUnit XML.
    function Get-Counts([string]$ReportDir) {
        $path = Join-Path $ReportDir 'junit.xml'
        if (-not (Test-Path $path)) { return 'no report written' }
        $suite = ([xml](Get-Content -Raw -Encoding UTF8 $path)).SelectSingleNode('//testsuite')
        $failed = [int]$suite.failures + [int]$suite.errors
        $skipped = [int]$suite.skipped
        $passed = [int]$suite.tests - $failed - $skipped
        return "$passed passed, $failed failed, $skipped skipped"
    }

    $summary = @()
    function Write-Summary {
        Write-Host ""
        foreach ($line in $summary) { Write-Host $line }
    }

    if ($CollectOnly) {
        Invoke-Pytest $selection $runDir @('--collect-only', '-q')
        exit $LASTEXITCODE
    }

    $checks = @(
        @{ Name = 'Environment checks'; File = $checkFiles[0]
           Dir  = "$runDir/environment-checks" },
        @{ Name = 'Framework checks';   File = $checkFiles[1]
           Dir  = "$runDir/framework-checks" }
    )
    # Both run before either can stop the run. They are independent -- one reads
    # the machine, the other feeds the framework known input -- so one run
    # reports every setup problem instead of one per attempt.
    $failedChecks = @(); $checkCode = 0
    foreach ($check in $checks) {
        Invoke-Pytest @($check.File) $check.Dir
        $code = $LASTEXITCODE
        $summary += '{0,-18} : {1}' -f $check.Name, (Get-Counts $check.Dir)
        if ($code -ne 0) {
            $failedChecks += $check.Name
            if ($checkCode -eq 0) { $checkCode = $code }
        }
    }
    if ($failedChecks) {
        Write-Summary
        Write-Host ""
        Write-Host ("$($failedChecks -join ' and ') failed, so nothing was " +
                    "installed and no case ran. Fix the failures above and " +
                    "re-run.") -ForegroundColor Red
        exit $checkCode
    }

    if ($Suite -eq 'checks' -and -not $Case) {
        Write-Summary
        exit 0
    }

    Invoke-Pytest $selection $runDir
    $code = $LASTEXITCODE
    $summary += '{0,-18} : {1}' -f 'Cases', (Get-Counts $runDir)
    Write-Summary

    # pytest exits 5 when nothing was collected. After a -Case filter that means
    # the ID does not exist, which otherwise reads as "the test is missing".
    if ($code -eq 5 -and $Case) {
        Write-Host ""
        Write-Host "No test carries case ID '$Case'." -ForegroundColor Yellow
        Write-Host ("Case IDs are part of the test names. List what exists with:" +
                    "`n    .\run-tests.ps1 -CollectOnly") -ForegroundColor Yellow
    }
    exit $code
}
finally { Pop-Location }
