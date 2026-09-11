#Requires -Version 5.1
<# Install one Scutio skill. User data and Python stay outside the skill directory. #>
[CmdletBinding()]
param(
    [ValidateSet('link', 'copy')][string]$Mode = 'copy',
    [string]$Dest = '',
    [switch]$WithVenv,
    [string]$VenvDir = '',
    [string]$Python = '',
    [switch]$RecreateVenv
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $Root 'skills\scutio'
$ScutioHome = if ($env:SCUTIO_HOME) { $env:SCUTIO_HOME } else { Join-Path $env:USERPROFILE '.scutio' }
$ConfigDir = $env:SCUTIO_CONFIG_DIR
$SkillsDest = if ($Dest) { $Dest } else { $env:SCUTIO_SKILLS_DIR }
$VenvPath = if ($VenvDir) { $VenvDir } elseif ($env:SCUTIO_VENV) { $env:SCUTIO_VENV } else { Join-Path $ScutioHome '.venv' }
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'
$NewVenv = $false
$VenvBackup = ''
$Staging = ''

if (-not $SkillsDest) { throw '请用 -Dest 或 SCUTIO_SKILLS_DIR 指定技能目录' }
if ($RecreateVenv -and -not $WithVenv) { throw '-RecreateVenv 需同时指定 -WithVenv' }
$Req = Join-Path $Src 'requirements.txt'
if (-not (Test-Path -LiteralPath (Join-Path $Src 'SKILL.md')) -or -not (Test-Path -LiteralPath $Req)) {
    throw "技能包不完整: $Src"
}

# Resolve existing junctions/links as well as nonexistent suffixes without Python.
function Resolve-InstallDirectory {
    param([string]$Path, [int]$Depth = 0)
    if ($Depth -gt 64) { throw "目录链接过深或存在循环: $Path" }
    $absolute = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path))
    $volume = [IO.Path]::GetPathRoot($absolute)
    if ($absolute.TrimEnd('\', '/') -eq $volume.TrimEnd('\', '/')) { return $volume }
    $parent = Resolve-InstallDirectory (Split-Path -Parent $absolute) ($Depth + 1)
    $candidate = Join-Path $parent (Split-Path -Leaf $absolute)
    $entry = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        $target = [string](@($entry.Target)[0])
        if (-not $target -or -not (Test-Path -LiteralPath $candidate -PathType Container)) {
            throw "目录路径不是可解析的目录: $candidate"
        }
        if (-not [IO.Path]::IsPathRooted($target)) { $target = Join-Path $parent $target }
        return Resolve-InstallDirectory $target ($Depth + 1)
    }
    if ($entry -and -not $entry.PSIsContainer) { throw "目录路径不是目录: $candidate" }
    return $candidate
}

function Test-DirectoryOverlap {
    param([string]$Left, [string]$Right)
    $leftPrefix = $Left.TrimEnd('\', '/') + '\'
    $rightPrefix = $Right.TrimEnd('\', '/') + '\'
    return $leftPrefix.StartsWith($rightPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $rightPrefix.StartsWith($leftPrefix, [StringComparison]::OrdinalIgnoreCase)
}

$venvEntry = Get-Item -LiteralPath $VenvPath -Force -ErrorAction SilentlyContinue
if ($WithVenv -and $venvEntry -and ($venvEntry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "环境路径不能是链接，请用 -VenvDir 选择独立目录: $VenvPath"
}
$homeInput = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ScutioHome))
$configInput = if ($ConfigDir) { [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ConfigDir)) } else { '' }
$destinationInput = Join-Path ([IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SkillsDest))) 'scutio'
$ScutioHome = Resolve-InstallDirectory $ScutioHome
$VenvPath = Resolve-InstallDirectory $VenvPath
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'
if ($ConfigDir) { $ConfigDir = Resolve-InstallDirectory $ConfigDir }
$SkillsDest = Resolve-InstallDirectory $SkillsDest
$Src = Resolve-InstallDirectory $Src
$Dst = Join-Path $SkillsDest 'scutio'
$resolvedDst = Resolve-InstallDirectory $Dst
foreach ($dataRoot in @($ScutioHome, $ConfigDir, $homeInput, $configInput)) {
    if (-not $dataRoot) { continue }
    foreach ($skillRoot in @($Src, $Dst, $resolvedDst, $destinationInput)) {
        if (Test-DirectoryOverlap $dataRoot $skillRoot) {
            throw "SCUTIO_HOME/SCUTIO_CONFIG_DIR 与技能目录不能互相包含，拒绝覆盖: $dataRoot ($skillRoot)"
        }
    }
}

function Test-SupportedPython {
    param([string]$Executable)
    try {
        $null = & $Executable -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Resolve-BasePython {
    param([string]$Preferred)
    $candidates = if ($Preferred) { @($Preferred) } else { @('python3.13', 'python3.12', 'python3.11', 'py', 'python', 'python3') }
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $exe = $command.Source
        if ([IO.Path]::GetFileNameWithoutExtension($exe) -eq 'py') {
            try {
                $resolved = & $exe -3 -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -ne 0) { continue }
                $exe = [string]($resolved | Select-Object -Last 1)
            } catch { continue }
        }
        if (Test-SupportedPython $exe) { return $exe }
    }
    throw '未找到可启动的 Python 3.11+。请用 -Python 指定兼容解释器；尚未安装 skill 或创建环境。'
}

# Preflight before creating or replacing either environment or skill.
if ($WithVenv) {
    if (Test-Path -LiteralPath $VenvPath) {
        $item = Get-Item -LiteralPath $VenvPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            -not (Test-Path -LiteralPath (Join-Path $VenvPath 'pyvenv.cfg'))) {
            throw "环境路径已存在但不是独立 venv，请用 -VenvDir 选择新目录: $VenvPath"
        }
        if (-not $RecreateVenv -and -not (Test-SupportedPython $VenvPython)) {
            throw '已有环境不兼容或无法启动。用 -RecreateVenv -Python 指定兼容解释器备份后重建，或用 -VenvDir 选择新目录。'
        }
    }
    if (-not (Test-Path -LiteralPath $VenvPath) -or $RecreateVenv -or $Python) {
        $BasePy = Resolve-BasePython $Python
    }
}

$fullDest = [IO.Path]::GetFullPath($SkillsDest).TrimEnd('\', '/')
if ($fullDest -eq [IO.Path]::GetPathRoot($fullDest).TrimEnd('\', '/') -or
    $fullDest -eq ([string]$env:USERPROFILE).TrimEnd('\', '/')) {
    throw '安装目标过于宽泛，请指定宿主的 skills 子目录'
}
$sourceRoot = Split-Path -Parent $Src
if ($SkillsDest -eq $sourceRoot -or $SkillsDest.StartsWith($sourceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '安装目标不能是仓库 skills 源目录或其子目录'
}
$item = Get-Item -LiteralPath $Dst -Force -ErrorAction SilentlyContinue
if ($item -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    $targetRoot = [IO.Path]::GetFullPath($Dst).TrimEnd('\', '/')
    $checkout = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if ($checkout -eq $targetRoot -or $checkout.StartsWith($targetRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "安装目标包含当前仓库，拒绝覆盖: $Dst"
    }
}
if ($WithVenv) {
    $checkPython = if ($BasePy) { $BasePy } else { $VenvPython }
    $configPath = if ($ConfigDir) { $ConfigDir } else { Join-Path $ScutioHome 'config' }
    & $checkPython -c @'
import sys
from pathlib import Path
env, home, config, *skills = (Path(p).resolve() for p in sys.argv[1:])
if any(p.is_relative_to(env) for p in (home, config)) or any(env.is_relative_to(p) or p.is_relative_to(env) for p in skills):
    sys.exit('Python 环境与技能源码、安装目录不能互相包含；请选择独立 -VenvDir')
'@ $VenvPath $ScutioHome $configPath $Src $Dst
    if ($LASTEXITCODE -ne 0) { throw '环境目录检查失败' }
}
foreach ($legacy in @('scutio-toolkit', 'scutio-research', 'scutio-scout')) {
    if (Get-Item -LiteralPath (Join-Path $SkillsDest $legacy) -Force -ErrorAction SilentlyContinue) {
        throw "发现旧入口 $legacy。请先移出宿主扫描范围并保留本地修改，再安装单一 scutio。"
    }
}

function Remove-InstallStaging {
    param([string]$Path)
    # Remove links themselves before recursively removing the private staging folder.
    foreach ($name in @('scutio', 'previous')) {
        $child = Get-Item -LiteralPath (Join-Path $Path $name) -Force -ErrorAction SilentlyContinue
        if ($child -and ($child.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $child.Delete() }
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

try {
    if ($WithVenv) {
        if ($RecreateVenv -and (Test-Path -LiteralPath $VenvPath)) {
            $VenvBackup = $VenvPath + '.backup-' + [guid]::NewGuid().ToString('N')
            Move-Item -LiteralPath $VenvPath -Destination $VenvBackup
        }
        if (-not (Test-Path -LiteralPath $VenvPath)) {
            $NewVenv = $true
            & $BasePy -m venv $VenvPath
            if ($LASTEXITCODE -ne 0) { throw 'venv 创建失败' }
        }
        & $VenvPython -m pip install -r $Req
        if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，未替换 skill。请检查网络、代理、安装源与 Python 兼容性后重试。' }
        & $VenvPython -B (Join-Path $Src 'scripts\runtime_probe.py')
        if ($LASTEXITCODE -ne 0) { throw '依赖探测失败' }
    }
    New-Item -ItemType Directory -Force -Path $SkillsDest | Out-Null
    New-Item -ItemType Directory -Force -Path $ScutioHome | Out-Null
    $Staging = Join-Path $SkillsDest ('.scutio-install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Staging | Out-Null
    $stagedSkill = Join-Path $Staging 'scutio'
    if ($Mode -eq 'link') {
        try {
            New-Item -ItemType Junction -Path $stagedSkill -Target $Src | Out-Null
        } catch {
            Write-Warning 'Junction 创建失败，改为复制独立 skill。'
            Copy-Item -LiteralPath $Src -Destination $stagedSkill -Recurse
        }
    } else {
        Copy-Item -LiteralPath $Src -Destination $stagedSkill -Recurse
    }
    if (Get-Item -LiteralPath $Dst -Force -ErrorAction SilentlyContinue) {
        Move-Item -LiteralPath $Dst -Destination (Join-Path $Staging 'previous')
    }
    Move-Item -LiteralPath $stagedSkill -Destination $Dst
    $NewVenv = $false
    Write-Host "已安装: $Dst"
    if (-not $WithVenv) { Write-Host '未安装或验证运行依赖；首次使用前运行 scripts/resolve_runtime.ps1。' }
    if ($VenvBackup) { Write-Host "旧环境备份: $VenvBackup（验证完成后可自行删除）" }
    Write-Host '运行前可在宿主进程中设置：'
    Write-Host ("  `$env:SCUTIO_HOME = '" + $ScutioHome.Replace("'", "''") + "'")
    if ($ConfigDir) { Write-Host ("  `$env:SCUTIO_CONFIG_DIR = '" + $ConfigDir.Replace("'", "''") + "'") }
    Write-Host ("  `$env:SCUTIO_VENV = '" + $VenvPath.Replace("'", "''") + "'")
    Write-Host ("  `$env:SCUTIO_TOOLKIT_SCRIPTS = '" + (Join-Path $Dst 'scripts').Replace("'", "''") + "'")
    if (Test-Path -LiteralPath $VenvPython) {
        Write-Host ("  `$env:SCUTIO_PYTHON = '" + $VenvPython.Replace("'", "''") + "'")
    }
    Write-Host '安装成功后在宿主中选择 Scutio；未发现时重启宿主。'
} finally {
    if ($NewVenv) {
        if (Test-Path -LiteralPath $VenvPath) { Remove-Item -LiteralPath $VenvPath -Recurse -Force }
        if ($VenvBackup) { Move-Item -LiteralPath $VenvBackup -Destination $VenvPath }
    }
    if ($Staging -and (Test-Path -LiteralPath $Staging)) {
        $previous = Get-Item -LiteralPath (Join-Path $Staging 'previous') -Force -ErrorAction SilentlyContinue
        if ($previous -and -not (Get-Item -LiteralPath $Dst -Force -ErrorAction SilentlyContinue)) {
            Move-Item -LiteralPath $previous.FullName -Destination $Dst
        }
        Remove-InstallStaging $Staging
    }
}
