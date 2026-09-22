# Scutio

**面向 A 股、港股和美股的 AI 投资研究 skill。** 帮你发现投资线索、查证消息、研究公司和行业、推演前景，并记录和复盘投资判断。

Scutio 运行在支持 skill、网页检索和本地 Python 的 AI 工具中，默认使用免费数据，无需配置数据 API Key。

[下载](https://github.com/JiangYiYao/scutio/releases/latest) · [安装](#快速开始) · [升级](#升级) · [反馈问题](https://github.com/JiangYiYao/scutio/issues/new/choose)

## 怎么用

安装后，直接在 AI 工具里提问；也可以加上“使用 Scutio”来指定使用这个技能。例如：

**从市场或主题找机会**

> 我关注未来 1—3 年的 A 股机会，还没有公司名单。帮我寻找重要变化，研究谁真正受益、当前价格是否有吸引力，也说明这次调查的范围。

也可以从具体主题开始，例如电网扩建或 SpaceX 的业务变化，沿真实业务关系寻找对象，再研究经营影响与价格条件。

**深入研究一家公司**

> 英伟达未来三年的增长还能靠什么？客户为什么继续买，竞争对手能替代多少，哪些变化最可能打破它的增长预期？

**查证一个说法**

> “AI 芯片供不应求，所以台积电一定能持续提价”这个推断站得住吗？查查财报和管理层原话，分清需求、产能和定价权之间还缺哪些证据。

**比较公司和价格**

> 帮我比较英伟达、AMD 和台积电：结合最新财报、行情和估值，当前价格分别要求怎样的增长？哪家值得进一步研究，为什么？

**推演不同情景**

> 假设未来两年英伟达同类 AI 芯片的平均售价每年下降 20%，销量要增长多少才能维持收入？利润又会怎样？把假设列清楚，算算不同情景。

**用新信息复核观点**

> 我认为“云厂商自研芯片会削弱英伟达的优势”。结合最新财报和客户动向，哪些证据支持这个观点，哪些反驳它？这会改变未来三年的投资判断吗？

完成研究后，还可以保存和回看判断：

**保存研究，留待复核**

> 把这次对英伟达的判断、关键依据和还没查清的问题记下来，也记下下次财报要核对什么。

**回看判断哪里出了偏差**

> 读取我之前保存的英伟达研究，和现在的情况对照。当时哪些判断兑现了，哪些错了？是事实有误、推理有问题，还是后来出现了新变化？也检查你当时给我的分析。

按需选择问题即可，不必按顺序使用。

## 快速开始

需要 **Python 3.11+**，以及能加载 skill、检索网页和执行本地脚本的 AI 工具。

以下使用 Git 安装；不使用 Git 时，见 [ZIP 安装说明](docs/installation.md#下载-zip-安装)。先下载版本 `0.1.0`：

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/JiangYiYao/scutio.git
cd scutio
```

将下面的路径替换为你的 AI 工具使用的 skills 目录，安装器会在其中创建 `scutio` 文件夹，并准备 Python 环境和依赖。首次安装需要联网。

**macOS / Linux**

```bash
./install.sh --dest "/path/to/agent/skills" --with-venv
```

**Windows PowerShell**

```powershell
.\install.ps1 -Dest "C:\path\to\agent\skills" -WithVenv
```

完成后，在 AI 工具中加载 Scutio；如果没有出现，重新加载技能列表或重启工具。

自定义 Python 环境和安装检查见[详细安装说明](docs/installation.md)。

## 升级

新版本发布在 [GitHub Releases](https://github.com/JiangYiYao/scutio/releases)。升级前先结束正在运行的 Scutio 任务。

- **通过 Git 安装**：在原仓库目录获取并切换到目标版本标签，再按上面的命令安装到原 skills 目录。
- **通过 ZIP 安装**：备份并移走旧 skill 目录，放入新版完整目录，再更新 Python 依赖。

升级后重新加载技能或重启 AI 工具。研究记录和配置保存在独立的用户目录，升级会保留；技能目录内的个人修改请先备份。[具体升级步骤](docs/installation.md#升级)

## 数据与记录

A 股数据覆盖最完整，港股、美股支持行情、部分财务和披露。已有同花顺 Financial API Key 时，可以告诉 AI“配置同花顺数据”；没有也能使用。[数据源说明](skills/scutio/references/toolkit/12-data-sources.md)

研究记录按你的要求保存，默认位于 `~/.scutio`，Windows 对应 `%USERPROFILE%\.scutio`。记录和配置的 Key 均以本地明文保存。[记录说明](skills/scutio/references/journal.md)

Scutio 不连接证券账户、不执行交易，也没有后台监控或主动提醒。研究质量取决于所用模型、检索能力和数据来源，重要结论请核对原始资料。模型及搜索费用以所用 AI 工具为准。

---

[研究案例与评测](evals/README.md) · [测试说明](tests/README.md) · [MIT 许可证](skills/scutio/LICENSE) · [第三方说明](skills/scutio/THIRD_PARTY.md)
