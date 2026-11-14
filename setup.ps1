param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $Hint"
    }
}

Write-Host "[1/3] Checking prerequisites..." -ForegroundColor Cyan
Require-Command "python" "Install Python 3.11+ and enable 'Add Python to PATH'."
Require-Command "node" "Install Node.js 20+; it is used to validate the extension scripts."

$pythonVersion = (& python --version 2>&1).ToString()
if ($pythonVersion -notmatch "Python 3\.(1[1-9]|[2-9][0-9])") {
    throw "Python 3.11 or newer is required. Found: $pythonVersion"
}

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..." -ForegroundColor DarkCyan
    python -m venv venv
}

$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
Write-Host "Installing backend dependencies..." -ForegroundColor DarkCyan
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r backend\requirements.txt -c backend\constraints.txt

if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Created .env from .env.example" -ForegroundColor DarkCyan
}

Write-Host "[2/3] Opening .env in Notepad..." -ForegroundColor Cyan
Write-Host "Choose LLM_PROVIDER, set LLM_MODEL_NAME, and paste the matching API key." -ForegroundColor Yellow
Start-Process notepad.exe -ArgumentList (Join-Path $Root ".env") -Wait

Write-Host "[3/3] Starting backend and opening Chrome extension setup..." -ForegroundColor Cyan
if (-not $SkipBrowser) {
    Start-Process "chrome.exe" -ArgumentList "chrome://extensions/" -ErrorAction SilentlyContinue
}

Write-Host "Load unpacked -> select: $Root\extension" -ForegroundColor Yellow
Write-Host "Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Press Ctrl+C in this window to stop the backend." -ForegroundColor DarkGray

& $VenvPython -m backend.main
