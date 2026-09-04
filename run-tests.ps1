#Requires -Version 5.1
<#
    User-facing entry point: a thin wrapper over pytest. Keeping this interface
    stable means the runner underneath can change without retraining anyone.

        .\run-tests.ps1 environment   # read-only self-check (default)
        .\run-tests.ps1 silent        # INS-002, OPS-001, OPS-002   DESTRUCTIVE
        .\run-tests.ps1 ui            # INS-001                     DESTRUCTIVE
        .\run-tests.ps1 all           # everything

    One case by its ID:   .\run-tests.ps1 -Case INS-002
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('environment', 'silent', 'ui', 'all')]
    [string]$Suite = 'environment',

    # A manual test-case ID, e.g. -Case INS-002. Case IDs are part of the test
    # function names, so this becomes a plain pytest -k filter. It overrides
    # -Suite. The hyphen is translated to an underscore because pytest's -k
    # expression grammar does not accept one.
    [string]$Case,

    [string]$Installer,

    # /passive gives the MSI UILevel 4 and RUNS the environment checks (their
    # dialogs are merely suppressed); /quiet gives UILevel 2 and skips them.
    # Not cosmetic.
    [ValidateSet('passive', 'quiet')]
    [string]$Mode = 'passive',

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

    # Everything except 'environment' installs the product, and the MSI carries
    # a Privileged launch condition. Fail here with one clear line rather than
    # the same stack trace once per collected test.
    $needsAdmin = $CollectOnly -eq $false -and ($Case -or $Suite -ne 'environment')
    if ($needsAdmin -and -not $isAdmin) {
        throw ("This run installs and uninstalls CUBRID For WSL on this " +
               "machine, which requires an elevated shell. Re-run from an " +
               "Administrator PowerShell.")
    }

    # Not named $args: that is a PowerShell automatic variable.
    $pytestArgs = @('-m', 'pytest')
    if ($Case) {
        $pytestArgs += @('-k', $Case.Replace('-', '_').ToLower())
    } else {
        switch ($Suite) {
            'environment' { $pytestArgs += @('-m', 'environment') }
            'silent'      { $pytestArgs += @('-m', 'silent') }
            'ui'          { $pytestArgs += @('-m', 'ui') }
            'all'         { }
        }
    }
    if ($Installer)   { $pytestArgs += @('--installer', $Installer) }
    $pytestArgs += @('--install-mode', $Mode)
    if ($CollectOnly) { $pytestArgs += @('--collect-only', '-q') }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    # --basetemp keeps pytest's scratch inside this run's own directory.
    # Without it pytest shares %LOCALAPPDATA%\Temp\pytest-of-<user>, where files
    # created by an elevated run inherit administrator ACLs and a later ordinary
    # run dies during cleanup -- after every test has already passed.
    $pytestArgs += @('--junitxml', "reports/$stamp/junit.xml",
                     '--basetemp', "reports/$stamp/tmp", '-v')

    New-Item -ItemType Directory -Force -Path "reports/$stamp" | Out-Null
    Write-Host "python $($pytestArgs -join ' ')" -ForegroundColor DarkGray
    & $python @($prefix + $pytestArgs)
    exit $LASTEXITCODE
}
finally { Pop-Location }
