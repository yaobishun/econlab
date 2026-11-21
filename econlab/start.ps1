param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$stateRoot = Join-Path $appRoot '.local'
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
$url = "http://127.0.0.1:$Port"
try {
    $existing = Invoke-RestMethod "$url/api/bootstrap" -TimeoutSec 2
    if ($existing.data_path -eq $stateRoot) {
        if (-not $NoBrowser) { Start-Process $url }
        Write-Host "EconLab is already running at $url"
        exit 0
    }
} catch {}
$pythonPath = Join-Path $appRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python 3.11+ is required. Install Python, then run this launcher again.' }
    $pythonPath = $pythonCommand.Source
}
& $pythonPath -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(x) for x in ('fastapi','uvicorn','multipart','fitz','numpy','scipy','httpx')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Preparing an isolated Python environment for EconLab...'
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot '.venv\Scripts\python.exe'))) {
        & $pythonPath -m venv (Join-Path $appRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment.' }
    }
    $pythonPath = Join-Path $appRoot '.venv\Scripts\python.exe'
    & $pythonPath -m pip install -r (Join-Path $appRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. See the message above.' }
}
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateRoot 'server.log') -RedirectStandardError (Join-Path $stateRoot 'server-error.log') -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    if ($serverProcess.HasExited) { throw "EconLab stopped. Read $stateRoot\server-error.log" }
    try {
        $response = Invoke-RestMethod "$url/api/bootstrap" -TimeoutSec 1
        if ($response.data_path -eq $stateRoot) { $ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw "Startup timed out. Read $stateRoot\server-error.log" }
@{pid=$serverProcess.Id;port=$Port;root=$appRoot;started=$serverProcess.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateRoot 'server.json') -Encoding UTF8
if (-not $NoBrowser) { Start-Process $url }
Write-Host "EconLab is ready: $url"
Write-Host "Data: $stateRoot"
