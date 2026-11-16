param([switch]$SkipBrowser)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program $($Arguments -join ' ')" }
}

try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.11+ from https://www.python.org/downloads/windows/ and enable Add Python to PATH, then rerun this command.'
    }
    Invoke-Checked python @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)')
    $VenvPython = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
    if (-not (Test-Path $VenvPython)) {
        Invoke-Checked python @('-m', 'venv', 'venv')
    }
    Invoke-Checked $VenvPython @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)')
    $Fingerprint = & $VenvPython -c "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('backend/requirements.txt').read_bytes() + Path('backend/constraints.txt').read_bytes()).hexdigest())"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read dependency files.' }
    $Stamp = Join-Path $PSScriptRoot 'venv\.webagent-dependencies'
    if (-not (Test-Path $Stamp) -or (Get-Content $Stamp -Raw).Trim() -ne $Fingerprint) {
        Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-r', 'backend/requirements.txt', '-c', 'backend/constraints.txt')
        Set-Content -LiteralPath $Stamp -Value $Fingerprint
    }
    $LaunchArguments = @('-m', 'backend.launcher')
    if ($SkipBrowser) { $LaunchArguments += '--skip-browser' }
    Invoke-Checked $VenvPython $LaunchArguments
} catch {
    Write-Host "Setup failed: $_" -ForegroundColor Red
    exit 1
}
