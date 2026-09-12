# Scutio 测试与通路探针

本目录是 **开发/运维** 用的验证入口，**不是** Agent 取数 skill 的一部分。  
日常 skill 任务：看返回信封 `ok` / `source` / `error` 并降级汇报即可；**不要**默认跑全量 `self_check`。

## 分层

测试按职责组织：`data/` 验证数据接口、组合、解析和筛选，`journal/` 验证记录，`runtime/` 验证安装、路径、运行时和示例。共用夹具放在 `fixtures/`，测试配置、运行脚本与通路自检位于本目录根部。

| 层 | 命令 / 入口 | 作用 | Agent 取数时 |
|----|-------------|------|----------------|
| 离线单测 | `./tests/run_tests.sh` | 解析、契约、fixture；无网可绿 | 不需要 |
| Live smoke | `./tests/run_tests.sh --live` | 公开数据门面与保留适配器的联网抽样 | 不需要 |
| 通路自检 | `tests/self_check.py` | 多探针 ok/degraded/fail，打印报告 | **默认不跑** |

发布前的完整取数验收还需按公开入口建立用例表，覆盖市场、资产类型、报告期、频率、复权、主备源及有/无 Key 路径。逐行核对身份、日期、币种、金额倍率、缺失值、分页和时效；选择原始响应、独立来源或官方原文核验关键字段。返回非空不能证明字段正确，正常空结果也不能冒充全量覆盖。未解决的来源差异和无法确认的单位应单列，不并入通过率。

最近一次实际结果见 [2026-09-11 取数与字段验收](../evals/reports/2026-09-11-data-acceptance.md)，保留 62 个公开入口的覆盖关系及逐例检查；临时执行器、凭据和原始批量响应不进入仓库。

[2026-09-12 Windows 与宿主复测](../evals/reports/2026-09-12-windows-host-retest.md)补充非 UTF-8 环境全量离线回归、32 个真实通路探针和 Grok/OpenCode 小规模验收；不替代上述 349 项字段验收。

[2026-09-12 Windows 升级验收](../evals/reports/2026-09-12-windows-upgrade.md)补充 alpha.1 → alpha.2 → 当前候选、重复安装、依赖解析失败保护及 venv 重建的实际结果。`runtime/test_upgrade.py` 覆盖外置数据与配置保留及旧记录继续追加；`runtime/test_requirements_encoding.py` 防止 pip 误解码带中文注释的依赖文件。

AKShare 升级后需重新检查源字段和单位，特别是已有本地修正的腾讯指数/深市成交量；固定 fixture 只能防止本地回归，不能证明上游仍维持旧行为。真实凭据只通过内存环境提供，使用临时配置与缓存目录；不把 Key、用户记录或批量第三方原文提交到仓库。

`self_check` 回答的是「当前环境哪些源还能用」，**不**修代码、**不**记「上次跑过」、**不**改业务结论。  
发现 degraded/fail 时，生产路径已有 fallback；Agent 侧正确动作是：**用门面取数 → 读 `ok`/`source` → 向用户说明缺口**。

## 离线 / live 测试

离线回归覆盖：按问题选择取数模块、身份与复用时点、单源失败隔离、显式样本筛选的覆盖统计，以及不含交易决定的研究记录、初始依据保护和本地目录迁移。独立安装测试从临时目录加载复制后的 skill，检查脚本运行与文档链接。行为质量另见 `evals/README.md`，不以代码测试代替。

```bash
# 仓库根；依赖见 requirements.txt
pip install -r tests/requirements.txt
./tests/run_tests.sh              # 默认离线
./tests/run_tests.sh --live       # 离线回归 + 公网 smoke
./tests/run_tests.sh --no-report
```

报表默认：`$SCUTIO_HOME/cache/test_reports/`。

## 通路自检（运维）

实现与 CLI 均在 **`tests/self_check.py`**（不在 `scutio_data` / skill scripts）。

何时跑：发版前、大面积取数失败排查。手动执行即可，**无** `self_check_meta` / 周期 `--if-due`。

```bash
# 仓库根
export SCUTIO_HOME="${SCUTIO_HOME:-$HOME/.scutio}"
export SCUTIO_PYTHON="${SCUTIO_PYTHON:-$SCUTIO_HOME/.venv/bin/python}"
# 可选：脚本会自动把 monorepo 的 skills/.../scripts 放进 path
export PYTHONPATH="skills/scutio/scripts:${PYTHONPATH:-}"

"$SCUTIO_PYTHON" tests/self_check.py
"$SCUTIO_PYTHON" tests/self_check.py --list
"$SCUTIO_PYTHON" tests/self_check.py --group market,capital
# 需要落盘时显式 -o（报告默认 stdout；-o 显式保存报告）
"$SCUTIO_PYTHON" tests/self_check.py --json -o /tmp/scutio_health.json
```

| 状态 | 含义 |
|------|------|
| ok | 联网成功，关键字段符合契约 |
| degraded | 主源挂、备胎可用，或可选字段缺失 |
| fail | 异常 / 不该空的空 / 关键字段消失 |

自检使用公开接口相同的来源顺序、共享限流与健康冷却；取数可能写入响应缓存、执行状态或版本检查状态。报告默认只输出到终端，指定 `-o` 才写入文件。

不校验具体股价。可编程入口：同文件内 `run_self_check` / `list_probes` / `render_markdown`（需能 import `scutio_data`）。

另见：`evals/README.md`（skill 行为评测，也不替代 pytest / self_check）。

取数层检查使用根目录 `ruff.toml`。安装开发依赖后运行 `python -m ruff check scripts skills/scutio/scripts tests` 和 `python -m ruff format --check scripts skills/scutio/scripts tests`，CI 执行相同检查。数据测试按能力组织。

## 取数回归与调用文档

| 测试文件（`data/` 下） | 主要覆盖 |
|---|---|
| `test_research_scope.py` | 产品范围、删除入口、源请求前频率校验及日/周/月资产路由 |
| `test_optional_sources.py` | 无 Key/public 零凭据请求、权限冷却、缓存、按标的回退、完整财报与摘要、跨进程设置更新 |
| `test_request_timeouts.py` | 复制安装后的真实 HTTP 子进程启动、脱敏错误分类、缓慢响应取消、共享预算、Retry-After、日历部分结果、凭据传递边界，以及线程/进程持有偏好锁时仍返回成功数据 |
| `test_security_identity.py`、`test_exchange_ids.py` | 公司入口的指数/基金/交易所边界、源响应身份、回退错证券与合法 A/港/美请求 |
| `test_research_local.py`、`test_ths_eps_and_valuation.py` | 同名不同期研报、市值别名与有限数值筛选、在线材料身份及不完整覆盖下的时效判断 |
| `test_akshare_market_contracts.py`、`test_hk_us_market.py` | 市场/资产路由、OHLC 缺失及非有限数值回退、复权、成交量、成交额与市值区分和行情覆盖 |
| `test_evidence_reuse.py` | 证券与预测年份复用、分页缺口、原文身份缓存、同名长标题及正文更新隔离 |
| `test_akshare_network.py`、`test_akshare_maintenance.py` | 停用接口、代理恢复、共享预算、失败分类和只检查不安装 |
| `test_capital_history.py`、`test_dragon_tiger_and_calendars.py` | 时间分区、截断、上榜原因、席位缺口和预约变更 |
| `test_source_contracts.py`、`test_source_semantics.py` | 身份、原生科目、单位、空值、日期、stale/partial |
| `test_content_adapters.py` | 新闻窗口、互动分页、业绩快报字段和失败信息 |
| `test_data_architecture.py` | 导入隔离、独立 worker、宏观请求复用和公告原文处理 |
| `test_ownership_filings.py`、`test_macro.py` | SEC 新旧股权披露表单与修订、指数缺项及错误身份、宏观快照部分覆盖 |

`runtime/test_single_skill_install.py` 验证安装目标与源码、数据及配置目录重叠时拒绝覆盖，以及链接/复制替换、相对路径安装后换目录运行；`journal/test_journal.py` 覆盖类别股记录创建和追加、路径约束，以及不同 `SCUTIO_HOME` 共用记录根的并发更新。慢响应测试使用本地 socketpair，不访问外部服务。

`runtime/test_windows_locks.py` 用另一进程已持有的空锁文件验证记录锁、配置锁会等待并随后成功，维护锁会返回忙；防止首次初始化锁文件时发生未受保护的写入。

`runtime/test_examples.py` 离线调用示例，验证身份、公开入口和失败退出码；`runtime/test_documented_api.py` 检查调用文档的 Python 导入/参数名称、示例入口和测试目录有效性。它们不验证源站实时可用性。

报表中的逐条说明由 `case_catalog.py` 的 `CASES` 提供；其余用例按 `CASE_GROUPS` 或测试层归组，保留完整 nodeid，输入和精确断言以测试源码为准。新增数据测试文件需补充分组，重命名/删除用例需同步逐条说明，避免把分组覆盖范围当成单个用例的断言。

```bash
# 只检查调用文档与示例，无真实取数
./tests/run_tests.sh --no-report -k 'example or documented or catalog'
# 显式启用、只跑公网 smoke；不验证 Financial API 账号权限
./tests/run_tests.sh --live --live-only
```

测试夹具隔离用户凭据和缓存路径，并关闭 AKShare 后台版本检查；离线用例在所属 provider 边界 mock，不能绕过 worker 禁网约束。Financial API 认证、降级和解析通过合成响应/fixture 验证，不使用真实 Key。

探针维护：`self_check.py --only` 含未登记的名称会在执行任何探针前报参数错误。工作流当前抽查 A/港/美行情以及概念、研报、港股披露，其 ID 与 registry 的一致性由离线测试校验。提交前应同时执行 Ruff lint 与 `ruff format --check`。


## 安装与发行验证

`runtime/test_single_skill_install.py` 还验证默认独立复制、解释器不可用时不覆盖、依赖安装失败时保留旧 skill、重建失败时恢复旧环境，以及环境与 skill 路径不能重叠。安装失败用例通过 `PIP_NO_INDEX=1` 禁止联网，使用独立临时 venv。

`runtime/test_release.py` 验证发行包和逐文件哈希、依赖清单的 extras/版本约束、解压后脱离仓库的离线启动，以及个人路径检查不会误伤公开 URL。`runtime_probe.py` 返回 Scutio、Python 与依赖版本，便于复现问题。

CI 在 Linux/Python 3.11–3.13 运行全量离线测试；macOS/Windows/Python 3.12 运行安装、运行时、记录、可选源配置与请求取消用例。手动或每周 canary 将 HTTP JUnit 和源探针 JSON 保存为 `data-source-canary` artifact，并把成功/降级/失败显示在摘要中。探针失败会让该次 canary 失败，普通 push 不触发公网检查。

Windows 另以 `PYTHONUTF8=0` 运行安装、记录及含中文数据夹具的回归，避免 CI 默认 UTF-8 模式掩盖隐式编码。仓库文本和 JSON 显式按 UTF-8 读写；Python 子进程需要 UTF-8 文本通信时，发送端与接收端同时指定编码。

`release` 工作流另验证压缩包在三个系统上的依赖安装和独立启动。手动运行仅产出 artifact；推送匹配 `VERSION` 的 `v*` 标签时，检查通过后发布 GitHub Release。正式版本设为 latest，带 alpha/beta/rc 后缀的版本标记为 prerelease。运行状态以实际 Actions 结果为准，流程存在不代表所有平台已经通过。

本地存储回归覆盖配置与状态分离、默认与自定义记录根迁移后继续追加、冲突预检与失败回滚、保留记录字节与证据引用，以及 API 缓存过期和容量淘汰；清理不得触及 Key、限流状态、文档或用户记录。
