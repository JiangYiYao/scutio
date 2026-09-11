#Requires -Version 5.1
<# Resolve the Windows runtime and probe imports without network or configuration writes. #>
[CmdletBinding()]
param([string]$Python = '', [ValidateRange(1,60)][int]$TimeoutSeconds = 15)
$ErrorActionPreference = 'Stop'

function Finish-Probe {
    param([hashtable]$Result, [int]$Code)
    $Result | ConvertTo-Json -Depth 8 -Compress
    exit $Code
}

$skillDirectory = Get-Item -LiteralPath (Split-Path -Parent $PSScriptRoot) -Force
if ($skillDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    $target = [string](@($skillDirectory.Target)[0])
    if (-not [IO.Path]::IsPathRooted($target)) {
        $target = Join-Path $skillDirectory.Parent.FullName $target
    }
    $skillDirectory = Get-Item -LiteralPath $target -Force
}
$scriptsDirectory = Join-Path $skillDirectory.FullName 'scripts'
$selectedBy = ''
if ($Python) {
    $selectedBy = 'argument'
} elseif ($env:SCUTIO_PYTHON) {
    $Python = $env:SCUTIO_PYTHON
    $selectedBy = 'SCUTIO_PYTHON'
} else {
    if ($env:SCUTIO_VENV) {
        $Python = Join-Path $env:SCUTIO_VENV 'Scripts\python.exe'
        $selectedBy = 'SCUTIO_VENV'
    } else {
        $dataRoot = if ($env:SCUTIO_HOME) { $env:SCUTIO_HOME } else { Join-Path $env:USERPROFILE '.scutio' }
        $Python = Join-Path $dataRoot '.venv\Scripts\python.exe'
        $selectedBy = 'user_venv'
        # Only the linked source repository is a development fallback, never a disk search.
        $repoRoot = $skillDirectory.Parent.Parent.FullName
        $repoPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -and
                $skillDirectory.Parent.Name -eq 'skills' -and
                (Test-Path -LiteralPath (Join-Path $repoRoot 'install.ps1') -PathType Leaf) -and
                (Test-Path -LiteralPath $repoPython -PathType Leaf)) {
            $Python = $repoPython
            $selectedBy = 'linked_repository_venv'
        }
    }
}
$base = @{ python=$Python; selection=$selectedBy; scripts=$scriptsDirectory; network_checked=$false }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Finish-Probe ($base + @{ok=$false; status='missing_interpreter'; error='Selected interpreter does not exist; explicit settings are not silently replaced.'}) 2
}
$Python = (Get-Item -LiteralPath $Python).FullName
$base.python = $Python
$venvConfig = Join-Path (Split-Path -Parent (Split-Path -Parent $Python)) 'pyvenv.cfg'
if (Test-Path -LiteralPath $venvConfig -PathType Leaf) {
    $base.venv_config = $venvConfig
    $homeLine = Get-Content -LiteralPath $venvConfig | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
    if ($homeLine) { $base.base_python_home = ($homeLine -replace '^home\s*=\s*','').Trim() }
}
$probeFile = Join-Path $scriptsDirectory 'runtime_probe.py'
$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $Python
$processInfo.Arguments = '-B "' + $probeFile + '"'
$processInfo.UseShellExecute = $false
$processInfo.CreateNoWindow = $true
$processInfo.RedirectStandardOutput = $true
$processInfo.RedirectStandardError = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $processInfo
try {
    $null = $process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        $process.WaitForExit()
        Finish-Probe ($base + @{ok=$false; status='probe_timeout'; error='Offline import probe timed out.'}) 2
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    try { $report = $stdout | ConvertFrom-Json } catch { $report = $null }
    if ($report -and $report.status -in @('ready','import_failed')) {
        $report | Add-Member -NotePropertyName selection -NotePropertyValue $selectedBy
        $report | ConvertTo-Json -Depth 8 -Compress
        exit $process.ExitCode
    }
    Finish-Probe ($base + @{ok=$false; status='startup_failed'; exit_code=$process.ExitCode;
        error=$stderr.Trim(); hint='Startup failure does not by itself prove a broken environment. Check the host permission boundary before reinstalling.'}) 2
} catch {
    Finish-Probe ($base + @{ok=$false; status='startup_failed'; error=$_.Exception.Message;
        hint='Check interpreter access and host permissions; do not change the data root.'}) 2
} finally {
    $process.Dispose()
}
