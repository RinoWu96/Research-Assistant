$ErrorActionPreference = 'Stop'

# Incremental rebuild for code-only changes: reuse PyInstaller's cached analysis
# in build\, build into a staging folder, then replace only the three .exe files
# (which embed the application's Python code) in the existing portable bundle.
# Falls back to telling you to run build-windows.ps1 when dependencies changed.

$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$build = Join-Path $project 'build'
$staging = Join-Path $build 'incremental-dist'
$python = Join-Path $project '.venv-build\Scripts\python.exe'
$spec = Join-Path $PSScriptRoot 'research-assistant.spec'
$version = (Select-String -LiteralPath (Join-Path $project 'app\version.py') -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
if (-not $version) { throw 'Cannot read __version__ from app\version.py.' }
$name = "ResearchAssistant-$version-windows-x64"
$bundle = Join-Path (Join-Path $project 'dist') $name
$executables = @('ResearchAssistant.exe', 'ResearchAssistantCore.exe', 'ResearchAssistantMCP.exe')

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv-build with official CPython 3.12 and install requirements-build.txt first.'
}
if (-not (Test-Path -LiteralPath $bundle)) {
    throw "No existing bundle at $bundle. Run build-windows.ps1 for the first build."
}
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($bundle) }
if ($running) {
    throw "Close Research Assistant first (running: $(($running | ForEach-Object Name) -join ', '))."
}

# PyInstaller logs to stderr; under 'Stop' Windows PowerShell would treat that as a failure.
$ErrorActionPreference = 'Continue'
& $python -m PyInstaller --noconfirm --workpath $build --distpath $staging $spec 2>&1 | Out-Host
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
$built = Join-Path $staging $name

# System runtime DLLs are collected from whatever PATH the build ran under and
# differ between shells/Windows updates; only the existing bundle's copies are kept.
$systemRuntime = '^\\(api-ms-win-.*|ucrtbase|msvcp140.*|vcruntime140.*|libcrypto-.*|libssl-.*)\.dll$'
function Get-Manifest([string]$root) {
    $internal = (Resolve-Path -LiteralPath (Join-Path $root '_internal')).Path
    Get-ChildItem -LiteralPath $internal -Recurse -File |
        ForEach-Object { $_.FullName.Substring($internal.Length), $_.Length -join '|' } |
        Where-Object { ($_ -split '\|')[0] -notmatch $systemRuntime } | Sort-Object
}
$difference = Compare-Object (Get-Manifest $bundle) (Get-Manifest $built)
if ($difference) {
    $difference | Select-Object -First 10 | Format-Table -AutoSize | Out-String | Write-Host
    throw 'Dependencies in _internal changed; run build-windows.ps1 for a full build.'
}

foreach ($exe in $executables) {
    Copy-Item -LiteralPath (Join-Path $built $exe) -Destination (Join-Path $bundle $exe) -Force
}
Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "Updated $($executables -join ', ') in $bundle"
