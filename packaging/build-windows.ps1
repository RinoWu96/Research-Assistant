$ErrorActionPreference = 'Stop'

$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$build = Join-Path $project 'build'
$dist = Join-Path $project 'dist'
$release = Join-Path $project 'release'
$python = Join-Path $project '.venv-build\Scripts\python.exe'
$spec = Join-Path $PSScriptRoot 'research-assistant.spec'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv-build with official CPython 3.12 and install requirements-build.txt first.'
}

$runtime = & $python -c "import platform; import PySide6; print(platform.python_version()); print(PySide6.__version__)"
if ($runtime[0] -notlike '3.12.*' -or $runtime[1] -ne '6.8.3') {
    throw "Unsupported build runtime: Python $($runtime[0]), PySide6 $($runtime[1])"
}

foreach ($path in @($build, $dist, $release)) {
    $resolvedParent = (Resolve-Path (Split-Path $path -Parent)).Path
    if ($resolvedParent -ne $project) { throw "Refusing to clean path outside project: $path" }
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

& $python -m PyInstaller --noconfirm --clean --workpath $build --distpath $dist $spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$bundle = Join-Path $dist 'ResearchAssistant-0.7.1-windows-x64'
Copy-Item -LiteralPath (Join-Path $project 'packaging\PORTABLE-README.txt') -Destination $bundle

New-Item -ItemType Directory -Path $release | Out-Null
$archive = Join-Path $release 'ResearchAssistant-0.7.1-windows-x64.zip'
Compress-Archive -LiteralPath $bundle -DestinationPath $archive -CompressionLevel Optimal
Get-FileHash -Algorithm SHA256 -LiteralPath $archive | Format-List
