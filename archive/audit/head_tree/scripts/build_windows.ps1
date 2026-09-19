$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $VenvPython)) {
    python -m venv .venv
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
& $VenvPython -m pip install -r requirements-packaging.txt
& $VenvPython -m PyInstaller --clean --noconfirm DataConverterTool.spec

$AppDir = Join-Path $ProjectRoot "dist\DataConverterTool"
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "exports") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "logs") | Out-Null

Write-Host ""
Write-Host "Build finished."
Write-Host "Executable:"
Write-Host (Join-Path $AppDir "DataConverterTool.exe")
Write-Host ""
Write-Host "Copy the whole dist\DataConverterTool folder to another Windows computer."
