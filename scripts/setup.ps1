param([switch] $NoPause, [switch] $DownloadDemoBooks, [switch] $DownloadChineseBooks)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$localDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDir -Force | Out-Null
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
function Invoke-Checked {
    param([string] $Executable, [string[]] $Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable (exit $LASTEXITCODE)" }
}
try {
    $working = $false
    if (Test-Path -LiteralPath $pythonPath) {
        try {
            & $pythonPath -c 'import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13)' 2>$null
            $working = $LASTEXITCODE -eq 0
        } catch {}
    }
    if (-not $working -and (Test-Path -LiteralPath (Join-Path $projectRoot '.venv'))) {
        $oldEnv = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.venv')).Path
        $backup = [IO.Path]::GetFullPath((Join-Path $localDir ('invalid-venv-' + [guid]::NewGuid().ToString('N'))))
        $prefix = $projectRoot.TrimEnd('\') + '\'
        if (-not $oldEnv.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not $backup.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid environment backup path.' }
        Move-Item -LiteralPath $oldEnv -Destination $backup
    }
    if (-not $working) {
        Remove-Item -LiteralPath (Join-Path $localDir 'dependencies.sha256') -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundledPython) {
            Invoke-Checked $bundledPython @('-m', 'venv', (Join-Path $projectRoot '.venv'))
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $foundPython = $false
            foreach ($version in @('-3.12', '-3.11', '-3.13')) {
                try {
                    & py $version -c 'import sys; print(sys.version)' 2>$null
                    if ($LASTEXITCODE -eq 0) {
                        Invoke-Checked 'py' @($version, '-m', 'venv', (Join-Path $projectRoot '.venv'))
                        $foundPython = $true
                        break
                    }
                } catch {}
            }
            if (-not $foundPython) { throw 'Install Python 3.11-3.13 from python.org, then run this launcher again.' }
        } else {
            & python -c 'import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11-3.13 is required. Install it from python.org.' }
            Invoke-Checked 'python' @('-m', 'venv', (Join-Path $projectRoot '.venv'))
        }
    }
    Write-Host '正在准备 Python 依赖，首次需要联网。'
    Invoke-Checked $pythonPath @('-m', 'pip', 'install', '-r', (Join-Path $backendDir 'requirements-lock.txt'))
    if (-not (Test-Path -LiteralPath (Join-Path $backendDir '.env'))) {
        Copy-Item -LiteralPath (Join-Path $backendDir '.env.example') -Destination (Join-Path $backendDir '.env')
    }
    if ($DownloadDemoBooks) {
        Invoke-Checked $pythonPath @((Join-Path $PSScriptRoot 'download_open_books.py'), '--output', (Join-Path $backendDir 'app\data\books'))
    }
    if ($DownloadChineseBooks) {
        Invoke-Checked $pythonPath @((Join-Path $PSScriptRoot 'download_chinese_books.py'), '--output', (Join-Path $backendDir 'app\data\books'))
    }
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) { throw 'Node.js 20.19+ is required. Install the LTS version from nodejs.org.' }
    Push-Location -LiteralPath $frontendDir
    try {
        Write-Host '正在准备网页，完成后日常启动不再需要 Node.js。'
        Invoke-Checked $npmCommand.Source @('ci', '--no-audit', '--no-fund')
        Invoke-Checked $npmCommand.Source @('run', 'build')
    } finally { Pop-Location }
    Invoke-Checked $pythonPath @((Join-Path $PSScriptRoot 'prepare_model.py'))
    Write-Host '环境准备完成。现在可以双击 start-medrag.cmd。' -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if (-not $NoPause) { Read-Host '按回车关闭' | Out-Null }
    exit 1
}
