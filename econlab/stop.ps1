$ErrorActionPreference = 'Stop'
$record = Join-Path $PSScriptRoot '.local\server.json'
if (-not (Test-Path -LiteralPath $record)) { Write-Host 'No recorded EconLab process.'; exit 0 }
$saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$serverProcess = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
if ($serverProcess) {
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($saved.pid)"
    # PowerShell 7 may deserialize ISO timestamps into DateTime already;
    # parsing that value again as a culture-formatted string loses its timezone.
    $recordedStartUtc = if ($saved.started -is [datetime]) {
        $saved.started.ToUniversalTime()
    } else {
        [DateTimeOffset]::Parse([string]$saved.started, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
    }
    $startMatches = [Math]::Abs(($serverProcess.StartTime.ToUniversalTime() - $recordedStartUtc).TotalSeconds) -lt 2
    if ($startMatches -and $details.CommandLine -match 'uvicorn\s+app:app' -and $saved.root -eq $PSScriptRoot) {
        # Stop only the recorded, identity-checked app process and its own workers.
        & taskkill.exe /PID $serverProcess.Id /T /F | Out-Null
        Write-Host 'EconLab stopped.'
    } else { throw 'Process identity has changed; no process was stopped.' }
}
Remove-Item -LiteralPath $record
exit 0
