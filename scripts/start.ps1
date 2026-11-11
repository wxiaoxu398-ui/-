param(
    [switch] $NoBrowser, [switch] $NoPause, [switch] $Prepare,
    [switch] $DownloadDemoBooks, [switch] $DownloadChineseBooks,
    [ValidateRange(15, 600)] [int] $StartupTimeoutSec = 180
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$localDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDir -Force | Out-Null
$url = 'http://127.0.0.1:8000/'
$backendDir = Join-Path $projectRoot 'backend'
$expectedScript = Join-Path $backendDir 'run.py'
$lock = $null
$locked = $false
$transcribing = $false
$exitCode = 0

function Get-Health {
    try { return Invoke-RestMethod -Uri ($url + 'api/health') -TimeoutSec 2 } catch { return $null }
}
function Test-OurService($Health) {
    if (-not $Health -or $Health.status -ne 'ok') { return $false }
    if ($Health.service -eq 'medrag' -and $Health.project_id -eq $projectId) { return $true }
    # Accept an already-running older version only when its process belongs here.
    $listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $listener.OwningProcess)
        if ($owner -and $owner.CommandLine -and $owner.CommandLine.Contains($expectedScript)) { return $true }
    }
    return $false
}
function Open-Page {
    if (-not $NoBrowser) {
        try { Start-Process $url } catch { Write-Host '浏览器未能自动打开，请复制下方地址到 Chrome 或 Edge。' -ForegroundColor Yellow }
    }
    Write-Host ('网页已就绪：' + $url) -ForegroundColor Green
}

try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $projectId = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.Replace('\', '/').ToLowerInvariant())))).Replace('-', '').ToLowerInvariant().Substring(0, 16) } finally { $sha.Dispose() }
    $lock = New-Object Threading.Mutex($false, ('Local\MedRAG-' + $projectId))
    try { $locked = $lock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) {
        Write-Host '另一个启动窗口正在准备服务，请等待该窗口完成。'
        exit 0
    }
    Start-Transcript -Path (Join-Path $localDir 'launcher.log') -Append | Out-Null
    $transcribing = $true
    Write-Host 'MedRAG 正在启动，请等待网页自动打开。'
    $health = Get-Health
    if (Test-OurService $health) {
        if ($Prepare -or $DownloadDemoBooks -or $DownloadChineseBooks) { throw '服务正在运行。请先双击 stop-medrag.cmd 停止，再执行准备或下载。日常使用直接启动即可。' }
        Open-Page
    } else {
        if ($health -or (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
            throw '8000 端口被其他程序或另一份作品占用。请关闭该程序后重试；本启动器不会结束其他程序。'
        }
        if ($Prepare -or $DownloadDemoBooks -or $DownloadChineseBooks) {
            $setupArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'setup.ps1'), '-NoPause')
            if ($DownloadDemoBooks) { $setupArgs += '-DownloadDemoBooks' }
            if ($DownloadChineseBooks) { $setupArgs += '-DownloadChineseBooks' }
            & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') @setupArgs
            if ($LASTEXITCODE -ne 0) { throw '环境准备失败，请查看本窗口和 .local/launcher.log。' }
        }
        $pythonPath = $null
        foreach ($candidate in @((Join-Path $projectRoot 'runtime\python\python.exe'), (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
            if (Test-Path -LiteralPath $candidate) {
                try {
                    & $candidate -c 'import sys; assert (3,11) <= sys.version_info[:2] <= (3,13); import uvicorn, fastapi, sqlmodel, pymupdf' 2>$null
                    if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
                } catch {}
            }
        }
        if (-not $pythonPath) { throw 'Python 运行环境缺失或已失效。请先联网双击 prepare-medrag.cmd 完成准备，再启动网页。' }
        # A review package carries a public-book-only vector seed, never user history.
        $seed = Join-Path $projectRoot 'runtime\vector-seed'
        if (Test-Path -LiteralPath $seed) {
            & $pythonPath (Join-Path $PSScriptRoot 'restore_vector_seed.py')
            if ($LASTEXITCODE -ne 0) { throw '评审资料索引准备失败，请运行 check-medrag.cmd 查看环境。' }
        }
        $checkOutput = & $pythonPath (Join-Path $PSScriptRoot 'check_environment.py') --json
        $checkExit = $LASTEXITCODE
        $check = ($checkOutput -join [Environment]::NewLine) | ConvertFrom-Json
        if ($checkExit -ne 0 -or -not $check.ready) {
            foreach ($item in $check.checks) { if (-not $item.ok) { Write-Host ($item.name + '：' + $item.message) -ForegroundColor Yellow } }
            throw '环境检查未通过。请先运行 prepare-medrag.cmd；详细检查可双击 check-medrag.cmd。'
        }
        $process = $null
        $pidFile = Join-Path $localDir 'server.pid'
        if (Test-Path -LiteralPath $pidFile) {
            $savedProcessId = 0
            if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedProcessId)) {
                $saved = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $savedProcessId)
                if ($saved -and $saved.CommandLine -and $saved.CommandLine.Contains($expectedScript)) {
                    $process = Get-Process -Id $savedProcessId -ErrorAction SilentlyContinue
                    Write-Host '上一次启动仍在加载，继续等待同一个进程。'
                }
            }
        }
        if ($null -eq $process) {
            $process = Start-Process -FilePath $pythonPath -ArgumentList @('"' + $expectedScript + '"') -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localDir 'server.log') -RedirectStandardError (Join-Path $localDir 'server-error.log')
            Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII
        }
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $nextProgress = 0
        $ready = $false
        while ($timer.Elapsed.TotalSeconds -lt $StartupTimeoutSec) {
            $process.Refresh()
            if ($process.HasExited) { throw '后台服务退出。请查看 .local/server-error.log，或运行 check-medrag.cmd。' }
            $health = Get-Health
            if (Test-OurService $health) { $ready = $true; break }
            if ($timer.Elapsed.TotalSeconds -ge $nextProgress) {
                Write-Host ('正在加载本地教材和检索模型，已等待 ' + [int]$timer.Elapsed.TotalSeconds + ' 秒…')
                $nextProgress += 5
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw ('启动等待超过 ' + $StartupTimeoutSec + ' 秒。后台可能仍在加载，请查看 .local/server-error.log 后重试；不会结束其他程序。') }
        $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        if ($page.StatusCode -ne 200 -or -not $page.Content.Contains('MedRAG')) { throw '后台已启动，但网页文件异常。请运行 prepare-medrag.cmd 修复页面。' }
        if ($DownloadChineseBooks) {
            Invoke-RestMethod -Uri ($url + 'api/documents/ingest') -Method Post -ContentType 'application/json' -Body '{"rebuild":false}' | Out-Null
        }
        Open-Page
    }
} catch {
    $exitCode = 1
    Write-Host ('启动未完成：' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('启动记录：' + (Join-Path $localDir 'launcher.log'))
    Write-Host '本窗口会保留错误；请勿重新安装或删除已有教材、密钥与会话。'
    if (-not $NoPause) { Read-Host '按回车关闭' | Out-Null }
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    if ($locked) { $lock.ReleaseMutex() }
    if ($null -ne $lock) { $lock.Dispose() }
}
exit $exitCode
