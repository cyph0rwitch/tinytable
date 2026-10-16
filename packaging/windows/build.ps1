# Build dist\TinyTable-Setup-<version>.exe. Run on Windows from any directory:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 0.1.0
# Requires Python 3.12+ (py launcher or python on PATH), Node 22, and Inno Setup 6.
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"

function Invoke-Checked {
    & $args[0] $args[1..($args.Count - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $args" }
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $Root
try {
    Push-Location frontend
    Invoke-Checked npm ci
    Invoke-Checked npm run build
    Pop-Location

    if (-not (Test-Path .build-venv)) { Invoke-Checked python -m venv .build-venv }
    $Python = Join-Path $Root ".build-venv\Scripts\python.exe"
    Invoke-Checked $Python -m pip install --upgrade -r backend\requirements.txt "pyinstaller>=6.10,<7"
    Invoke-Checked $Python -m PyInstaller --noconfirm --clean packaging\windows\tinytable.spec

    $Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) {
        $Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
    Invoke-Checked $Iscc "/DAppVersion=$Version" packaging\windows\installer.iss
    Write-Host "Built dist\TinyTable-Setup-$Version.exe"
}
finally {
    Pop-Location
}
