# Shared by run-tests.ps1 and install-build.ps1. Dot-source it:
#
#     . "$root\tools\common.ps1"
#
# One copy, so the two entry points cannot disagree about which Python runs the
# framework or whether the shell is elevated.

# A working interpreter, as @{ Exe = <path>; Prefix = <args before the script> }.
#
# Probe for a working interpreter rather than pattern-matching its path:
# the Microsoft Store STUB and the Python Install Manager's real launcher
# both live under WindowsApps, so the path tells you nothing.
function Find-Python {
    foreach ($cand in @(
        @{ Exe = 'python'; Pre = @() },
        @{ Exe = 'py';     Pre = @('-3') }
    )) {
        $cmd = Get-Command $cand.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $probe = & $cmd.Source @($cand.Pre + @('-c', 'import sys; print(sys.executable)')) 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) {
            Write-Host "python: $($probe.Trim())" -ForegroundColor DarkGray
            return @{ Exe = $cmd.Source; Prefix = $cand.Pre }
        }
    }
    throw @"
No working Python found.

    winget install 9NQ7512CXL7T -e --accept-package-agreements
    py install 3.12

Accept the prompt to add %LocalAppData%\Python\bin to PATH, then open a new
shell. If 'python' still fails, turn off the Store aliases under
Settings > Apps > Advanced app settings > App execution aliases.
"@
}

function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
