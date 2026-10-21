#Requires -Version 5.1
<#
    Install one build of CUBRID For WSL, and run no test case.

        .\install-build.ps1 -Installer <path\to\bundle.exe>

    -Installer is REQUIRED. installer.path in settings.local.toml is not used
    here: this uninstalls whatever is on the machine, so the build replacing it
    is always named on the command line.

    DESTRUCTIVE: whatever CUBRID For WSL is on the machine is uninstalled
    first, then the build is installed unattended (/quiet) and waited for
    until it is usable -- the same install the shared test groups make. The
    build is LEFT installed, for manual testing or debugging.

    Not a test run: the environment checks run first, as before every suite,
    and nothing after them is a case. Results go to reports/<stamp>-install/:
    notes.txt, install.json (which build, its SHA-256, under which account),
    and the installer logs.

    Needs an elevated shell.
#>
[CmdletBinding()]
param(
    # The bundle to install. Required -- see above.
    [string]$Installer
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $root
try {
    # Checked by hand, not [Parameter(Mandatory)]: that would PROMPT for the
    # value, and a script that waits for input looks hung.
    if (-not $Installer) {
        throw ("Name the build to install: " +
               ".\install-build.ps1 -Installer <path\to\bundle.exe>. " +
               "Nothing was uninstalled or installed.")
    }

    . "$root\tools\common.ps1"
    $found = Find-Python
    $python = $found.Exe; $prefix = $found.Prefix

    # The MSI carries a Privileged launch condition. Fail here with one clear
    # line rather than after the environment checks.
    if (-not (Test-IsAdmin)) {
        throw ("This installs and uninstalls CUBRID For WSL on this machine, " +
               "which requires an elevated shell. Re-run from an " +
               "Administrator PowerShell.")
    }

    # ONE token, --installer=<path>: see Invoke-Pytest in run-tests.ps1.
    $installerArg = @("--installer=$Installer")

    $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
    $runDir = "reports/$stamp-install"
    $checkDir = "$runDir/environment-checks"
    New-Item -ItemType Directory -Force -Path $checkDir | Out-Null

    $checkArgs = @('-m', 'pytest', 'tests/environment_checks.py') + $installerArg +
                 @('--junitxml', "$checkDir/junit.xml",
                   '--basetemp', "$checkDir/tmp", '-v')
    Write-Host "python $($checkArgs -join ' ')" -ForegroundColor DarkGray
    & $python @($prefix + $checkArgs)
    $checkCode = $LASTEXITCODE
    if ($checkCode -ne 0) {
        Write-Host ""
        Write-Host ("Environment checks failed, so nothing was uninstalled or " +
                    "installed. Fix the failures above and re-run.") -ForegroundColor Red
        exit $checkCode
    }

    $toolArgs = @('tools/install_build.py', $runDir) + $installerArg
    Write-Host ""
    Write-Host "python $($toolArgs -join ' ')" -ForegroundColor DarkGray
    & $python @($prefix + $toolArgs)
    $code = $LASTEXITCODE

    Write-Host ""
    Write-Host ('{0,-18} : {1}' -f 'Environment checks', 'passed')
    Write-Host ('{0,-18} : {1}' -f 'Install', $(if ($code -eq 0) { 'installed' } else { 'FAILED' }))
    Write-Host ('{0,-18} : {1}' -f 'Report', (Join-Path $root $runDir))
    exit $code
}
finally { Pop-Location }
