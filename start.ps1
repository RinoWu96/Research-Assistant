$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
Start-Process -FilePath $pythonPath -ArgumentList 'main.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
