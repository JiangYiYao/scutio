# Scutio

**从公司与产业变化中发现、研究和复核投资机会的 AI skill。** 面向 A 股、港股和美股，运行在支持技能加载、网页检索和本地 Python 的 Agent 中。

你可以带着一家公司、一条消息或一个尚不完整的想法来。Scutio 围绕影响未来价值的问题搜集证据，追踪资源投入、客户行为和竞争变化，推演收益归属与价格条件，并按需保留研究记录。

**当前为 `0.1.0-alpha.1` 早期预览，仍在开发中。** 欢迎用真实问题测试，并指出遗漏的线索、错误数据和站不住的判断。研究质量依赖宿主模型及其工具，当前评测尚未证明相对不加载 skill 的稳定优势。

[快速开始](#快速开始) · [真实案例](#先看一次真实研究) · [数据覆盖](skills/scutio/references/toolkit/index.md) · [反馈](https://github.com/JiangYiYao/scutio/issues/new/choose)

## 先看一次真实研究

输入：“你怎么看中国连锁酒店行业未来两三年的投资机会？”

2026-09-10 的一次候选版本执行，将华住列为优先继续研究的对象，并追问了这些关系：

- **扩张和成熟门店表现不同。** 整体 RevPAR 增长 1.1%，同店却下降 3.0%；门店和品牌结构变化可能掩盖成熟门店的压力。
- **收入增长怎样转成股东收益。** 加盟收入增长与经营利润率改善值得跟进，还需拆分持续管理费、新店相关收入及加盟商回本周期。
- **经营判断还没有完成价格判断。** 三年股东回报计划并非保证收益，正常化盈利和价格要求仍需进一步核对。

这说明一次研究可以怎样推进问题。[完整回答、来源与对照评测](evals/reports/2026-09-10-discovery-comparison.md) 保留了执行记录；该轮酒店评测的两位评审均小幅偏好无 skill 的回答，不能把这段展示当作效果证明。

## 快速开始

需要 **Python 3.11+、Git**，以及能加载 skill、检索和阅读网页、执行本地脚本的宿主。无需 Financial API Key。Python 依赖安装需要联网，首次下载可能较慢。

先按所用 Agent 的说明确认技能目录，并替换下方的路径占位符。安装器会在该目录下创建 `scutio/`；技能的发现和启用方式由宿主决定。

### macOS / Linux

```bash
git clone https://github.com/JiangYiYao/scutio.git
cd scutio
# 替换为 Agent 实际加载技能的目录
SCUTIO_SKILLS_DIR="/path/to/agent/skills"
./install.sh --dest "$SCUTIO_SKILLS_DIR" --with-venv
```

### Windows PowerShell

```powershell
git clone https://github.com/JiangYiYao/scutio.git
cd scutio
# 替换为 Agent 实际加载技能的目录
$ScutioSkillsDir = "C:\path\to\agent\skills"
.\install.ps1 -Dest $ScutioSkillsDir -WithVenv
```

安装器默认复制独立 skill，并在 `~/.scutio/.venv` 安装和离线验证依赖。依赖准备成功后才替换技能。完成后，在宿主中加载或启用 Scutio，即可用自然语言提问；未发现技能时重新加载技能列表或重启宿主。

> 使用 Scutio，研究腾讯未来三年的增长来源。先判断哪些变化最影响未来价值，再查证关键依据，说明收益怎样传导，以及当前价格要求了什么。

### 检查、更新与卸载

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

- **选择 Python**：安装命令可追加 `--python python3.12` 或 `-Python 'C:\实际路径\python.exe'`。省略 `--with-venv` / `-WithVenv` 只复制文件，不准备依赖。
- **更新**：在仓库执行 `git pull --ff-only`，再运行相同安装命令。复制安装不会自动更新；重装会覆盖目标中的 `scutio`，有自定义修改时先保存。
- **依赖冲突**：追加 `--recreate-venv` / `-RecreateVenv` 备份重建环境；失败时恢复，成功后保留同级 `.backup-*` 供确认后删除。
- **手动安装**：将 `skills/scutio` 或发行包中的 `scutio` 复制到宿主扫描目录，在技能目录之外创建 Python 3.11+ 环境，用其 `python -m pip install -r <技能目录>/requirements.txt` 安装依赖。发行包附带 `tested-constraints.txt` 时追加 `-c <技能目录>/tested-constraints.txt`，再运行上述检查。
- **卸载**：关闭宿主，删除其扫描目录下的 `scutio`；链接安装只删除链接或 Junction。用户目录 `~/.scutio` 单独保留，需要彻底清理时先保存所需记录，再删除该目录。

自定义数据目录、解释器及失败排查见[运行时说明](skills/scutio/references/toolkit/01-runtime.md)。

## 适合怎样的问题

| 子能力 | 示例 |
|---|---|
| **查证** | “订单翻倍”的原始公告是什么，具体指哪一部分？ |
| **研究** | 这个行业未来两三年有什么值得继续研究的机会，谁可能保留收益？ |
| **推演** | 如果新业务投入翻倍，会怎样影响现金流与三年后的公司？ |
| **复核** | 这份新财报改变了原判断的哪一部分？ |
| **复盘** | 当时为什么这样判断，后来哪一步偏离了预期？ |

这些能力按问题组合。研究关注前瞻变化，财报用于核对经营基础、资金与回报；给出明确期限时，需要调查期间内可能改变公司的事件与依赖条件。程序负责取数、计算、筛选和记录，Agent 负责阅读、比较解释和追问缺口。

简单查证通常只需少量材料。重要研究可能持续多轮检索，支持 subagent 的宿主可分工推进；不支持时顺序完成。模型、搜索和子任务的使用量由宿主计费，Scutio 不包含模型额度。可在请求中说明时间或预算限制；没有统一的任务总时限。数据请求有独立的超时与降级机制。

## 数据与当前边界

默认使用 AKShare 适配的免费数据，少数缺口保留直接适配，并统一字段、校验与回退。**A 股覆盖最完整**；港股、美股覆盖行情、部分财务和披露，深度分部、订单、客户和资本开支仍需阅读公司 IR、年报等原始材料。免费源可能限流、缺字段或失效，离线测试通过不代表实时数据可用。

已有同花顺 [Financial API](https://github.com/HiThink-Tech/Financial-API) Key 时，可以对 Agent 说“配置同花顺数据”。Agent 会说明本地明文保存位置后代为配置，优先使用其支持的 A 股报价、日线、估值、分红和财报摘要，失败时回退。服务权限、费用和额度以提供方及账号实际情况为准。也可以说“只用免费数据”或“查看数据源状态”。[配置与覆盖边界](skills/scutio/references/toolkit/12-data-sources.md)

证券代码显式区分市场，例如 `600519`、`hk00700`、`usAAPL`。支持日、周、月 K 线，范围聚焦投资研究。[取数说明与覆盖范围](skills/scutio/references/toolkit/index.md)

Scutio 不连接券商账户、不执行交易，也不提供后台监控或主动提醒。可以按要求保存下次复核事项，用户再次发起时继续研究。研究可能遗漏重大线索或错误连接信息，需要检查原文和推理；内容不构成投资、税务或法律建议。

## 本地数据

技能目录保存说明与代码，用户数据默认位于 `~/.scutio`（Windows 为 `%USERPROFILE%\.scutio`）：

```text
.scutio/
├── .venv/               Python 环境
├── config/              Key 与数据源偏好
├── state/               限流、冷却、健康检查与进程锁
├── cache/
│   ├── api/             可重新获取的接口响应
│   └── documents/       披露原文、卖方研报与提取文本
└── journal/<记录ID>/    用户明确要求保存的记录
```

可通过 `SCUTIO_HOME` 改变根目录。配置 Key 和研究记录使用本地明文存储；更新 skill 不清除这些数据。记录按用户要求创建，保留初始依据和后续修订。讨论默认留在对话中，不自动建立本地记忆；采集 JSON 或研究报告只在指定输出文件时保存。接口缓存定期淘汰过期数据并控制容量，原文与记录不会自动清理。[记录说明](skills/scutio/references/journal.md)

## 项目说明与验证

欢迎通过 [Issues](https://github.com/JiangYiYao/scutio/issues/new/choose) 反馈使用问题、错误数据和研究遗漏。

`skills/scutio/` 是唯一安装包；其中 `references/` 存放能力、共享方法与取数说明，`scripts/` 提供程序。代码测试在 `tests/`，行为用例和真实执行在 `evals/`；测试和评测不随 skill 安装。

- [测试说明](tests/README.md)：离线回归、跨平台检查和数据源探针。
- [行为评测](evals/README.md)：完整回答、研究遗漏、对照结果与局限。
- [取数与字段验收](evals/reports/2026-09-11-data-acceptance.md)：接口覆盖、字段核验及未解决的来源差异。

名字取自 *scuttlebutt*，意为通过打听和交叉求证了解事情。主体采用 [MIT 许可证](skills/scutio/LICENSE)，依赖和接口来源见 [第三方说明](skills/scutio/THIRD_PARTY.md)，两份文件随 skill 分发。
