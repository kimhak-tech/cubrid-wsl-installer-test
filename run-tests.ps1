#Requires -Version 5.1
<#
    User-facing entry point: a thin wrapper over pytest. Keeping this interface
    stable means the runner underneath can change without retraining anyone.

        .\run-tests.ps1 environment   # read-only self-check (default)
        .\run-tests.ps1 silent        # INS-002, 1 install           DESTRUCTIVE
        .\run-tests.ps1 ui            # INS-001 + INS-004, 2 installs DESTRUCTIVE
        .\run-tests.ps1 all           # everything, 3 installs        DESTRUCTIVE

    One case by its ID:   .\run-tests.ps1 -Case INS-001
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('environment', 'silent', 'ui', 'all')]
    [string]$Suite = 'environment',

    # A manual test-case ID, e.g. -Case INS-001. Case IDs are part of the test
    # function names, so this becomes a plain pytest -k filter. It overrides
    # -Suite. The hyphen is translated to an underscore because pytest's -k
    # expression grammar does not accept one.
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
    $code = $LASTEXITCODE

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
