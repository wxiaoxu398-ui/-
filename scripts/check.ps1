param([switch] $NoPause, [switch] $NoGuide)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$guide = Join-Path $projectRoot 'docs\07-启动修复与评审检查.md'
$checkExitCode = 1

try {
    $pythonPath = $null
    foreach ($relative in @('runtime\python\python.exe', 'runtime\python.exe', '.venv\Scripts\python.exe')) {
        $candidate = Join-Path $projectRoot $relative
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            & $candidate -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' *> $null
            if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
        } catch {}
    }
    if (-not $pythonPath) {
        throw '找不到可运行的 Python 3.11–3.13。请运行 prepare-medrag.cmd 准备环境，或重新解压包含 runtime 的完整评审运行包。'
    }
    & $pythonPath (Join-Path $PSScriptRoot 'check_environment.py')
    $checkExitCode = $LASTEXITCODE
} catch {
    Write-Host '环境检查未通过。' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
}

if ($checkExitCode -ne 0) {
    Write-Host ''
    Write-Host ('处理说明：' + $guide) -ForegroundColor Yellow
    if (-not $NoGuide -and (Test-Path -LiteralPath $guide)) {
        try {
            Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\notepad.exe') -ArgumentList @('"' + $guide + '"')
        } catch { Write-Host '无法自动打开说明，请到 docs 目录查看 07-启动修复与评审检查.md。' }
    }
}
if (-not $NoPause) { Read-Host '按 Enter 关闭检查窗口' | Out-Null }
exit $checkExitCode
