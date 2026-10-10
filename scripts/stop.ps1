$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $projectRoot '.local\server.pid'
if (Test-Path -LiteralPath $pidFile) {
    $serverProcessId = [int](Get-Content -LiteralPath $pidFile -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
    $expectedScript = Join-Path $projectRoot 'backend\run.py'
    if ($process -and $process.CommandLine.Contains($expectedScript)) {
        # Windows venv python may use a redirector parent plus a real Python child.
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $serverProcessId"
        foreach ($child in $children) {
            if ($child.CommandLine -and $child.CommandLine.Contains($expectedScript)) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
        }
        Stop-Process -Id $serverProcessId -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile
}
Write-Host 'MedRAG stopped.'
