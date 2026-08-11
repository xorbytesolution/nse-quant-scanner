param(
    [string] $ShimDir = "$env:APPDATA\npm"
)

$ErrorActionPreference = "Stop"

$target = Join-Path $PSScriptRoot "sqlite-open.cmd"
if (-not (Test-Path $target)) {
    throw "sqlite-open.cmd not found next to this installer."
}

if (-not (Test-Path $ShimDir)) {
    New-Item -ItemType Directory -Path $ShimDir -Force | Out-Null
}

$target = (Resolve-Path $target).Path
$commands = @("sdb", "sqlite-open")

foreach ($command in $commands) {
    $shimPath = Join-Path $ShimDir "$command.cmd"
    $content = @"
@echo off
"$target" %*
"@
    Set-Content -Path $shimPath -Value $content -Encoding ASCII
}

Write-Host "Installed command shims in: $ShimDir"
Write-Host "Commands available from any folder:"
Write-Host "  sdb <database>"
Write-Host "  sqlite-open <database>"
