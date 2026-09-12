# 安装与升级

[返回 README](../README.md)

## 快速开始

需要 **Python 3.11+**，以及能加载 skill、检索和阅读网页、执行本地脚本的宿主。下面的仓库安装方式还需要 Git；也可以[下载 ZIP 手动安装](#下载-zip-安装)。Python 依赖安装需要联网，首次下载可能较慢。

先按所用 Agent 的说明确认技能目录，并替换下方的路径占位符。安装器会在该目录下创建 `scutio/`；技能的发现和启用方式由宿主决定。以下命令固定到 `v0.1.0` 标签，避免安装尚未发布的主分支改动。

### macOS / Linux

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/JiangYiYao/scutio.git
cd scutio
# 替换为 Agent 实际加载技能的目录
SCUTIO_SKILLS_DIR="/path/to/agent/skills"
./install.sh --dest "$SCUTIO_SKILLS_DIR" --with-venv
```

### Windows PowerShell

```powershell
git clone --branch v0.1.0 --depth 1 https://github.com/JiangYiYao/scutio.git
cd scutio
# 替换为 Agent 实际加载技能的目录
$ScutioSkillsDir = "C:\path\to\agent\skills"
.\install.ps1 -Dest $ScutioSkillsDir -WithVenv
```

安装器默认复制独立 skill，并在 `~/.scutio/.venv` 安装和离线验证依赖。依赖准备成功后才替换技能。完成后，在宿主中加载或启用 Scutio，即可用自然语言提问；未发现技能时重新加载技能列表或重启宿主。

> 使用 Scutio，研究腾讯未来三年的增长来源。先判断哪些变化最影响未来价值，再查证关键依据，说明收益怎样传导，以及当前价格要求了什么。

### 下载 ZIP 安装

从 [GitHub Releases](https://github.com/JiangYiYao/scutio/releases/latest) 的 Assets 下载 `scutio-0.1.0.zip`。这是可独立安装的技能包；GitHub 自动生成的 `Source code` 压缩包则是整个仓库。

1. 解压，将包内整个 `scutio` 目录放进宿主扫描的技能目录。ZIP 不包含仓库安装脚本或 Python 环境。
2. 在技能目录之外创建 Python 3.11+ 虚拟环境，用该环境的 Python 安装依赖：

   ```text
   python -m pip install -r "<技能目录>/requirements.txt" -c "<技能目录>/tested-constraints.txt"
   ```

   将 `python` 替换为该环境解释器，`<技能目录>` 替换为已安装的 `scutio` 目录。`tested-constraints.txt` 记录该发行包构建和测试时使用的依赖版本。
3. 在宿主中设置 `SCUTIO_PYTHON` 为该解释器的绝对路径，按下面的检查命令验证，再加载 Scutio。

同页 `.zip.sha256` 可校验下载文件，`.manifest.json` 记录版本、提交和包内文件哈希。没有现成 Python 环境时，使用上面的仓库安装器可以自动完成环境准备。

### 检查安装

在设置好上述目录变量的终端中运行离线检查（默认 Python 环境位置）：

```bash
# macOS / Linux
"$HOME/.scutio/.venv/bin/python" -B "$SCUTIO_SKILLS_DIR/scutio/scripts/runtime_probe.py"
```

```powershell
# Windows PowerShell
& "$ScutioSkillsDir\scutio\scripts\resolve_runtime.ps1"
```

`status: ready` 表示解释器可启动、依赖可导入；`network_checked: false` 表示尚未检查数据源。其他安装位置请替换路径。若宿主限制脚本或网络权限，需要在该宿主中解决权限问题；终端安装成功不等于宿主内运行成功。

安装命令可追加 `--python python3.12` 或 `-Python 'C:\实际路径\python.exe'` 来选择 Python。省略 `--with-venv` / `-WithVenv` 只复制文件，不准备依赖。

手动安装或使用自定义环境时，设置 `SCUTIO_PYTHON` 为该环境的 Python 绝对路径；检查和实际取数使用同一个解释器：

```bash
# macOS / Linux：替换为已安装依赖的解释器
export SCUTIO_PYTHON="/absolute/path/to/venv/bin/python"
"$SCUTIO_PYTHON" -B "$SCUTIO_SKILLS_DIR/scutio/scripts/runtime_probe.py"
```

```powershell
# Windows PowerShell：-Python 显式指定探针使用的解释器
$env:SCUTIO_PYTHON = "C:\actual\venv\Scripts\python.exe"
& "$ScutioSkillsDir\scutio\scripts\resolve_runtime.ps1" -Python $env:SCUTIO_PYTHON
```

上述变量设置只作用于当前终端及其启动的进程；独立启动的宿主也需要配置相同的 `SCUTIO_PYTHON`。

自定义数据目录、解释器及失败排查见[运行时说明](../skills/scutio/references/toolkit/01-runtime.md)。

## 升级

Scutio 目前只通过 [GitHub Releases](https://github.com/JiangYiYao/scutio/releases) 发布，不自动下载或替换自身。按原来的安装方式升级即可；无需寻找另一套 skill。

**通过仓库安装**：先结束正在运行的 Scutio 任务，在原仓库目录获取并切换到目标版本，再重跑原安装命令。以下以升级到 `0.1.0` 为例；以后替换成 Releases 中的目标标签。

```bash
git fetch origin tag v0.1.0
git switch --detach v0.1.0
```

- macOS / Linux：`./install.sh --dest "$SCUTIO_SKILLS_DIR" --with-venv`
- Windows：`.\install.ps1 -Dest $ScutioSkillsDir -WithVenv`

重新打开终端后，需要重新设置技能目录变量；使用过自定义 `SCUTIO_HOME`、配置目录或 venv 时，沿用原路径及安装参数。

**通过 ZIP 安装**：下载目标版本 ZIP，结束正在运行的任务，将旧 `scutio` 目录移出宿主扫描范围留作备份，再放入新版完整目录，避免仅覆盖同名文件而残留旧脚本。使用原 Python 环境，按新包的 `requirements.txt` 和 `tested-constraints.txt` 重新安装依赖。

升级完成后，重新运行安装检查，并重新加载技能或重启宿主。研究记录、Key 和偏好位于独立的 `~/.scutio`（或自定义数据目录），更新 skill 不清除它们；技能目录内的个人修改需要自行保留。

如果需要重建 Python 环境，仓库安装命令可追加 `--recreate-venv` / `-RecreateVenv`：重建失败恢复原环境，成功后保留同级 `.backup-*`。普通复用环境的 pip 安装不提供任意中途失败的完整回滚保证。[Windows 升级验收](../evals/reports/2026-09-12-windows-upgrade.md)记录了历史版本升级、用户数据保留、旧记录继续追加和环境重建的实测范围。

**卸载**：结束 Scutio 任务后删除宿主技能目录中的 `scutio`；链接安装只删除链接或 Junction。用户数据目录单独保留，需要彻底清理时先保存所需记录。
