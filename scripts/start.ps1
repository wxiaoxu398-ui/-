param([switch] $NoBrowser, [switch] $DownloadDemoBooks, [switch] $DownloadChineseBooks)
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

function Start-ChineseIndex {
    if ($DownloadChineseBooks) {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/documents/ingest' -Method Post -ContentType 'application/json' -Body '{"rebuild":false}' | Out-Null
        Write-Host 'Chinese manuals are imported. Incremental indexing progress is shown in the library.'
    }
}

try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundledPython) {
            Invoke-Checked $bundledPython @('-m', 'venv', (Join-Path $projectRoot '.venv'))
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $foundPython = $false
            foreach ($version in @('-3.12', '-3.11', '-3.13')) {
                & py $version -c 'import sys; print(sys.version)' 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Invoke-Checked 'py' @($version, '-m', 'venv', (Join-Path $projectRoot '.venv'))
                    $foundPython = $true
                    break
                }
            }
            if (-not $foundPython) { throw 'Install Python 3.11-3.13 from python.org, then run this launcher again.' }
        } else {
            & python -c 'import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11-3.13 is required. Install it from python.org.' }
            Invoke-Checked 'python' @('-m', 'venv', (Join-Path $projectRoot '.venv'))
        }
    }
    $depsMarker = Join-Path $localDir 'dependencies.sha256'
    $dependencyFiles = @('requirements.txt', 'requirements-core.txt', 'requirements-lock.txt') | ForEach-Object { Join-Path $backendDir $_ }
    $dependencyHash = (($dependencyFiles | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join '-')
    $dependenciesMatch = (Test-Path -LiteralPath $depsMarker) -and (Get-Content -LiteralPath $depsMarker -Raw).Trim() -eq $dependencyHash
    if (-not $dependenciesMatch) {
        Write-Host 'Installing local backend dependencies. The first run may take several minutes.'
        Invoke-Checked $pythonPath @('-m', 'pip', 'install', '-r', (Join-Path $backendDir 'requirements-lock.txt'))
        Set-Content -LiteralPath $depsMarker -Value $dependencyHash -Encoding UTF8
    }
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
    $buildInputs = @(Get-ChildItem -LiteralPath (Join-Path $frontendDir 'src') -File -Recurse) + @(Get-Item -LiteralPath (Join-Path $frontendDir 'package.json'), (Join-Path $frontendDir 'package-lock.json'), (Join-Path $frontendDir 'index.html'), (Join-Path $frontendDir 'vite.config.ts'), (Join-Path $frontendDir 'tsconfig.json'))
    $buildHash = (($buildInputs | Sort-Object FullName | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join '-')
    $buildMarker = Join-Path $localDir 'frontend.sha256'
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'dist\index.html')) -or -not (Test-Path -LiteralPath $buildMarker) -or (Get-Content -LiteralPath $buildMarker -Raw).Trim() -ne $buildHash) {
        Push-Location -LiteralPath $frontendDir
        try {
            Write-Host 'Preparing the web interface...'
            if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules\vite\bin\vite.js'))) {
                Invoke-Checked $npmCommand.Source @('ci')
            } else {
                Invoke-Checked $npmCommand.Source @('install', '--no-audit', '--no-fund')
            }
            Invoke-Checked $npmCommand.Source @('run', 'build')
            Set-Content -LiteralPath $buildMarker -Value $buildHash -Encoding UTF8
        } finally { Pop-Location }
    }
    $existing = $null
    try { $existing = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 } catch {}
    if ($existing -and $existing.version -and $existing.llm_model) {
        Start-ChineseIndex
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/' }
        Write-Host 'MedRAG is already running.'
        exit 0
    }
    $serverScript = Join-Path $backendDir 'run.py'
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('"' + $serverScript + '"') -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localDir 'server.log') -RedirectStandardError (Join-Path $localDir 'server-error.log')
    Set-Content -LiteralPath (Join-Path $localDir 'server.pid') -Value $process.Id -Encoding ASCII
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($process.HasExited) { throw 'Local server exited. See .local/server-error.log.' }
        try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($health.status -eq 'ok') { $ready = $true; break } } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Local service did not become ready. See .local/server-error.log.' }
    Start-ChineseIndex
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8000/' }
    Write-Host 'MedRAG is running at http://127.0.0.1:8000. Use stop-medrag.cmd to stop it.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
