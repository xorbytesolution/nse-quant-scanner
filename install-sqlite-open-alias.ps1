param(
    [string] $ProfilePath = $PROFILE
)

$ErrorActionPreference = "Stop"

$toolPath = Join-Path $PSScriptRoot "sqlite-open.cmd"
if (-not (Test-Path $toolPath)) {
    throw "sqlite-open.cmd not found next to this installer."
}

$profileDir = Split-Path -Parent $ProfilePath
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}

$start = "# >>> sqlite-open shortcut >>>"
$end = "# <<< sqlite-open shortcut <<<"
$escapedToolPath = $toolPath.Replace("'", "''")
$block = @"
$start
function sqlite-open {
    & '$escapedToolPath' @args
}
Set-Alias sdb sqlite-open
$end
"@

$content = ""
if (Test-Path $ProfilePath) {
    $content = Get-Content -Raw -Path $ProfilePath
    $pattern = "(?s)\Q$start\E.*?\Q$end\E\r?\n?"
    $content = [regex]::Replace($content, $pattern, "")
}

Set-Content -Path $ProfilePath -Value ($content.TrimEnd() + "`r`n`r`n" + $block + "`r`n")
Write-Host "Installed PowerShell commands:"
Write-Host "  sqlite-open <database>"
Write-Host "  sdb <database>"
Write-Host ""
Write-Host "Profile: $ProfilePath"
Write-Host "Restart PowerShell, or run: . '$ProfilePath'"
