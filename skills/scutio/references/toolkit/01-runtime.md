# 运行时核心

**范围**：路径、代码身份、统一信封、批量执行、关键配置。
**市场**：约定适用于 **A / 港 / 美**（代码身份与信封跨市场同一套；各业务能力是否支持见 index.md §2 与域文）。  
**原则**：业务只调能力门面；限流 / 切源 / HTTP 细节由 `scutio_data` 内部处理。

---

## 路径与 Python

默认根目录 **`~/.scutio`**（`SCUTIO_HOME`；Windows 为 `%USERPROFILE%\.scutio`）。

| 路径 | 用途 |
|------|------|
| `$SCUTIO_SKILL_DIR` | 当前 `SKILL.md` 所在目录，用于定位内置资源 |
| `$SCUTIO_SKILLS_DIR` | 宿主实际扫描的 skills 安装根；安装时显式指定 |
| `$SCUTIO_SKILL_DIR/scripts` | 含 **`scutio_data` 包**的目录（`SCUTIO_TOOLKIT_SCRIPTS`） |
| `$SCUTIO_HOME/.venv/.../python` | **`SCUTIO_PYTHON` 默认**（Unix `bin/`，Win `Scripts\`） |
| `$SCUTIO_HOME/config/` | `credentials.env` 与 `settings.json`；本地明文、私有权限 |
| `<配置目录>/settings.lock` | 配置更新锁；随 `SCUTIO_CONFIG_DIR` 定位到实际配置目录 |
| `$SCUTIO_HOME/state/` | 共享限流、冷却、健康与版本检查、进程锁 |
| `<记录根>/.locks/` | 记录写入锁；共用记录根的进程使用同一位置 |
| `$SCUTIO_HOME/cache/api/` | 提供方接口响应与全市场快照 |
| `$SCUTIO_HOME/cache/documents/filings/{a\|hk\|us}/{code}/` | 法定披露原文 + 旁路 `.txt` |
| `$SCUTIO_HOME/cache/documents/reports/{code}/` | 卖方研报 PDF + `.txt`（无个股码 → `_misc`） |

- 取数脚本直接调用 **`SCUTIO_PYTHON`**，不要 `activate`。
- **`SCUTIO_TOOLKIT_SCRIPTS`**：指向含 `scutio_data/` 的目录（默认  
  `$SCUTIO_SKILL_DIR/scripts`）。交互 `import` 时把该目录加入模块搜索路径。  
  （`PYTHONPATH` 是 Python 标准机制，不是 Scutio 配置名；配置请用 `SCUTIO_TOOLKIT_SCRIPTS`。）
- 先区分解释器缺失、进程启动失败与依赖导入失败，再报告具体缺口，不把所有错误归为“环境损坏”。不改系统包。
- `runtime_probe.py` 返回 Scutio、Python 与依赖版本，且不联网；提交诊断时先匿名处理 `python`、`scripts` 和错误中的本机路径。首次安装使用仓库安装器的 `--with-venv` / `-WithVenv`，依赖验证成功后才替换 skill；默认复制安装，用户数据独立保存。
- 内置脚本可用 `paths.ensure_toolkit_for_skill_script(__file__)` 自定位，**可不**手设搜索路径。
- 依赖安装：`pip install -r skills/scutio/requirements.txt`（含 `requests`、`pandas`、`lxml`、**`pypdf`**）。  
  **PDF→txt** 依赖 `pypdf`；未装时下载 PDF 仍可成功，抽取可能失败（看 `text_error` / 空 txt）。
- 示例脚本：`skills/scutio/scripts/examples/`。

### 本地存储维护

`config/` 保存 Key 与偏好，`state/` 保存限流、冷却与版本检查，二者不属于可清理缓存。配置更新锁 `settings.lock` 与 `settings.json` 放在同一目录；即使 `SCUTIO_HOME` 不同，共用 `SCUTIO_CONFIG_DIR` 的进程也会串行更新。`cache/api/responses/<提供方>/` 保存响应，缓存键包含请求、网络路径、解析版本；Financial API 进一步按凭据摘要隔离。成功写入缓存时，最多每 10 分钟清扫一次：删除过期响应，再按最旧写入时间淘汰到 128 MiB 以内。这是清扫时的容量上限，间隔内可短暂超过；没有新请求时不启动后台任务。缓存不可写或锁忙不会丢弃已获取的数据。

`cache/documents/` 存放阅读所需的原文与提取文本，不自动删除，避免破坏记录里的证据引用。研究结果默认在对话里返回，仅在用户要求导出时写文件。`journal/<记录ID>/` 只保存明确要求记录的内容，不自动归档讨论。记录锁位于实际记录根的 `.locks/`，保证不同 `SCUTIO_HOME` 共用同一记录根时仍能串行更新；任务运行时不要清理锁文件。

预览与执行 API 缓存清理（均不触及配置、运行状态、原文或记录）：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" clean-cache
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" clean-cache --apply
```

发现旧本地目录时，先停止使用该目录的取数与记录任务，再运行一次迁移：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" migrate
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" migrate --apply
```

若 `journal.py` 使用自定义 `--root`，迁移时同样指定该根：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" migrate --journal-root /path/to/journal
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/local_storage.py" migrate --journal-root /path/to/journal --apply
```

默认仅预览，输出路径与保留原因，不输出 Key 或记录正文。迁移在执行前检查所有目标冲突，绝不覆盖；执行中发生文件错误时回滚已完成的移动。记录 JSON 保留原内容，仅调整目录和文件名；所有附件随记录移动。被记录引用的旧原文目录与已有自选研究导出保留在原处，并在 `retained` 中列出。普通取数只使用新路径，没有双目录回读。旧 API 响应和执行健康状态不迁移为当前缓存或冷却；配置、仍有效的 AKShare 版本检查状态、原文和记录按上述规则处理。

根目录由 `SCUTIO_HOME` 指定；只需单独配置目录时用 `SCUTIO_CONFIG_DIR`，它不会改变状态和缓存位置。曾使用 `SCUTIO_DATA_HOME` 的环境需向迁移命令传入 `--legacy-data-home <旧目录>`，之后移除旧环境变量；运行时会明确提示迁移，不静默忽略旧配置。目标已有同名文件时先核对冲突再迁移，不要覆盖 Key 或用户记录。

### Windows 首次使用或启动失败

在当前宿主权限下运行技能自带的离线检查，无需先知道可用的 Python：

```powershell
& "$SCUTIO_SKILL_DIR\scripts\resolve_runtime.ps1"
```

它按 `-Python`、`SCUTIO_PYTHON`、`SCUTIO_VENV`、用户默认 venv 定位；未显式指定且默认不存在时，允许使用当前技能链接所指仓库的 `.venv`。不会全盘找解释器、安装依赖或写配置。读取 JSON 中的 `python`、`scripts`、`status`；ready 后在当前进程设置 `SCUTIO_PYTHON` 与 `SCUTIO_TOOLKIT_SCRIPTS`，并用该解释器执行任务。无需每条数据请求都重复检查。

- `missing_interpreter`：所选路径不存在；显式配置错误不静默换成另一个解释器。
- `startup_failed`：Python 尚未成功运行探针。结合错误与 `pyvenv.cfg` 的 base 路径检查访问边界；“Unable to create process”并不证明依赖损坏。宿主支持时，对同一离线探针申请执行权限并重试一次；权限外成功则说明是执行范围差异。后续实际取数也需要相应权限，不因探针成功就假定原权限已改变。
- `import_failed`：Python 已启动，检查 `failures` 中缺少的模块或加载错误，再针对性处理。按当前请求判断影响范围；例如缺少 PDF 抽取库不代表行情不可用。完整依赖检查不作为所有研究的共同门禁。
- `probe_timeout`：离线导入未在期限内结束；保留错误，不反复启动相同检查。

权限不可获得或本次不值得继续排查时，用可用的公开原文回答，并说明未运行哪些工具。不得通过更改 `SCUTIO_HOME`、复制解释器或关闭宿主隔离规避权限。ready 仅证明本次权限下可启动并导入依赖，不代表联网数据源可用。

以下为 Unix 环境示例；已有解释器可运行 `"$SCUTIO_PYTHON" -B "$SCUTIO_SKILL_DIR/scripts/runtime_probe.py"` 做同样的离线导入检查。

```bash
export SCUTIO_HOME="${SCUTIO_HOME:-$HOME/.scutio}"
SCUTIO_SKILL_DIR="<当前 scutio/SKILL.md 所在目录的绝对路径>"
export SCUTIO_PYTHON="${SCUTIO_PYTHON:-$SCUTIO_HOME/.venv/bin/python}"
export SCUTIO_TOOLKIT_SCRIPTS="${SCUTIO_TOOLKIT_SCRIPTS:-$SCUTIO_SKILL_DIR/scripts}"
# 仅当要 from scutio_data… 时：
export PYTHONPATH="${SCUTIO_TOOLKIT_SCRIPTS}${PYTHONPATH:+:$PYTHONPATH}"
```

---

## 代码身份

代码格式与交易所统一归一；**不要手写 secid**。公共入口还会校验该能力支持的证券类型，格式有效不代表可以查询公司数据。

| 市场 | 推荐写法 | 规则 |
|------|----------|------|
| A | `600519` / `sh000001` | 裸 `000001` = **平安银行**，上证指数须 `sh000001` |
| 港 | `hk00700` / `00700.HK` | 裸 5 位**不**当港股 |
| 美 | `usAAPL` / `AAPL.US` | 裸 ticker **拒绝** |

配套：`normalize_code`、`canonical_symbol`、`market_of`（`a`/`hk`/`us`）、`em_secid` / `source_symbol`（实现用，Agent 一般不直接调）。

代码身份采用严格全字符串校验：`600519foo`、七位 A 股码、六位港股码、
`600519.HK` 等不会被截断或改写为另一只证券，而是直接返回参数错误。

指数与行情字段见 [`02-market.md`](02-market.md)。

公司档案、财报、历史估值、个股研报及公司事件等入口拒绝 A 股指数、基金和交易所不匹配的代码，返回 `unsupported_asset`。例如 `stock_info("sh000001")` 不会把上证指数的裸码交给公司接口；指数或 ETF 的行情使用 `security_quote` / `security_bars`。源响应与复用材料中的证券代码、交易所和完整 `symbol` 必须一致，不能把另一只证券的数据改名后返回。

仅支持特定市场的代码型门面会在联网前检查能力矩阵；越界返回
`ok=False, error_code="unsupported_market"`，不会请求注定失败的上游接口。

---

## 统一信封

取数门面通常返回 **dict**，至少含 `ok` / `error` / `source`。完整约定见 index.md §3；此处只强调：

| 状态 | `ok` | 列表类 `items` |
|------|------|----------------|
| 成功有数 | `True` | 非空 |
| 合法空（如非交易日池） | `True` | `[]` |
| 请求失败 | `False` | 通常 `[]` |

- 纯计算/路径辅助函数不使用取数信封；`security_bars` 的无效请求参数会抛 `ValueError`。
- 部分成功：`ok=True, partial=True`，并在 `errors` / `warning` 说明缺腿。
- `data_quality=partial_fallback` 的档案/报价降级同样会在外层标明其不完整性；不要按完整字段集消费。
- 没有任何可用业务数据时才用 `ok=False`。
- 上游缺失数值用 `None`；`0` 只表示上游明确给出的真实零值。

- 列表：用 `envelope_items(env)` 或 `env["items"]`，**不要** `for x in result`（含 `financial_report` / 研报列表 / `eps_forecast`）。
- **禁止**把 `ok=False` 说成「今日无数 / 无人气 / 零涨停」。
- 构造辅助（实现）：`result_ok` / `result_err` / `result_list` / `result_list_err`。

---

## 批量调用

少量取数直接调用公开领域接口。多个独立请求用 `fetch_many`，键是调用方为结果起的名称，没有预设研究模块：

```python
from functools import partial
from scutio_data import fundamentals, market
from scutio_data.batch import fetch_many

batch = fetch_many({
    "quote": partial(market.security_quote, ["600519"]),
    "income": partial(fundamentals.financial_report, "600519", "lrb", num=4, period="annual", detail="summary"),
    "cashflow": partial(fundamentals.financial_report, "600519", "llb", num=4, period="annual", detail="summary"),
}, max_workers=4)
for name, item in batch["results"].items():
    env = item["result"]  # 原领域信封；抛出的异常转为失败信封
    print(name, item["state"], env.get("ok"), env.get("error"))
```

构造请求前先导入所用领域模块。参数名称按各接口签名填写：例如 K 线用 `count`，新闻与公告用 `page_size`，财报用 `num`。批量绑定失败返回 `error_code="invalid_arguments"`，列出函数名、必填与可选参数，不发送该项请求；检查对应域说明或 `inspect.signature()` 后只重试失败项。其它异常仍隐藏任意异常正文，避免泄露凭据；参数错误不应当作源站故障反复重试。

函数同步等到每项终态才返回，`batch_state="finished"` 表示全部执行结束；中断为 `interrupted`。这不代表每项成功，更不代表研究完成。逐项读取 `state`、`result` 和 `timing`。状态为 `success` / `failed` / `timeout` / `cancelled` / `not_started`；成功还需从信封区分合法空和部分覆盖。异常与未执行项也提供 `ok=False` 的信封。根据实际问题判断关键证据是否取得、源日期是否足够新；十个次要接口成功不能弥补关键财报或业务指标缺失。

`max_workers` 只限制本次最外层 `fetch_many`，不是进程内所有独立批次的共同上限。领域组合嵌套调用时在原工作线程执行，避免层层扩张；源站配额另由共享执行层约束。默认没有整批截止时间，各领域接口保留自己的操作预算。普通排队不消耗尚未启动单项的默认预算；外层显式 `request_timeout` 则包含排队和实际执行，超时后停止未启动项并结束本批拥有的取数 worker。每项 `queue_seconds` 为排队时间，`call_seconds` 为调用经过时间；提供方等待和真实尝试另有细分，不能把等待当成源请求超时。批次 `timing` 含 `total_seconds` 与 `first_success_seconds`。

可选 `on_progress` 接收 `completed` / `total`、`running` 阶段和 `pending` 名称，不携带业务数据；回调在调用线程串行执行，显示异常不丢弃取数结果。外部取消可传 `cancel_event`，其 `is_set()` 为真时终止本批。进度只说明执行情况，必须等最终返回并检查信封后再形成结论或写结果文件。取消不能用“先返回一半结果、后台继续跑”的方式处理。

取消保障覆盖本库管理的 HTTP / AKShare worker 及可取消等待。普通 Python 可调用对象需要主动检查取消，线程中的任意阻塞或死循环不能强杀；因此此入口用于组织公开取数接口，不是任意程序的隔离执行器。批次取消后仍会等实际运行项结束，已经完成的成功数据保留。

有依赖的请求用普通 Python 先后组合，例如先取报价，再把原信封通过 `valuation_snapshot(..., quote_env=quote)` 传入；研报通过 `consensus_revisions(..., reports=reports)`，领域快照通过 `preloaded` 复用。传入失败信封也会保留失败，不因复用自动再请求；不要重新标注原数据时间。无依赖的接口不必因另一项失败而放弃。

### 执行配额

每次真实尝试才占用来源槽，缓存命中和等待同一请求不占槽。默认本地策略为 Financial API 同凭据最多 4 个在途、每秒启动 4 次；东财最多 2 个在途、每秒启动 1 次，同族重扫描接口额外串行；SEC 最多 4 个在途、启动间隔至少 0.15 秒；其他已登记来源最多 4 个在途，显式归入 `unknown` 组的适配器最多 2 个。这些是本地保护配置，不是源站承诺的额度。

需要调整时用 `data_sources.set_setting("execution", {"providers": {provider: settings}})` 保存配置。`settings` 可含 `max_inflight`、`starts_per_second`、`failure_threshold`、`cooldown_seconds`；`starts_per_second=0` 表示不额外固定启动速率。健康冷却按提供方、凭据及具体接口范围共享，不因单个不支持的能力关闭所有能力。日常调用保留默认值，先看失败原因与覆盖缺口再决定是否调整。

---

## 环境变量（调用方可能碰到）

| 变量 | 作用 |
|------|------|
| `SCUTIO_HOME` | 用户根（默认 `~/.scutio`） |
| `SCUTIO_SKILLS_DIR` | 宿主实际扫描的 skills 安装根；安装时显式指定 |
| `SCUTIO_TOOLKIT_SCRIPTS` | 含 `scutio_data` 的目录（默认 `$SCUTIO_SKILL_DIR/scripts`） |
| `SCUTIO_PYTHON` | 取数解释器（默认 `$SCUTIO_HOME/.venv/bin/python`） |
| `SCUTIO_TRUST_ENV=0` | 忽略环境代理（坏代理时常有用） |
| `SCUTIO_SEC_UA` | SEC 请求 User-Agent（建议 `AppName real@email`；fair-access） |

依赖：`requests`；表格/一致预期另需 `pandas` + `lxml`；中证官网 `.xls` 需 `xlrd`；交易日历需 `exchange-calendars`；PDF 文本抽取需 **`pypdf`**；免费源适配另需固定版本 `akshare`，运行时需要 Python 3.11+。详见 toolkit `requirements.txt`。

同机、同一 `SCUTIO_HOME` 状态根的进程共用提供方请求槽、起始速率和健康冷却；不同机器或状态根不共享配额。AKShare 与直接东财请求共用东财配额，但 AKShare 限制的是适配器调用发起，库内分页和 HTTP 次数不可逐次观测，不能视为精确 HTTP RPS。协调状态不可写或锁不可用时返回可诊断失败，不静默绕过配额。

---

## 边界（非默认入口）

| 符号 | 说明 |
|------|------|
| 单源 quote / `em_get` | 实现细节；不再从包根导出；行情用 `security_*` |
| `_providers.quote_parse` | 源字段解析，仅内部与测试使用 |
| `_providers` / `_documents` | 数据源适配及原文处理；经各领域门面调用 |
| `feeds` 的 `parse_*` / `rank_*` | 实现/测试；只调 `stock_news` 等门面 |
| `tests/.../self_check.py` | 运维通路探针，非取数默认流程 |

降级模式、行情链与来源覆盖 → [`11-fallback.md`](11-fallback.md)。

## Python 导入边界

调用方按领域导入，资金、宏观和研报使用包入口；包内拆分不改变调用方式：

```python
from scutio_data.market import security_quote, security_bars
from scutio_data.capital import corporate_actions, stock_fund_flow_120d
from scutio_data.macro import macro_snapshot, cn_macro_series
from scutio_data.research import stock_reports, local_report_search
from scutio_data.announcements import periodic_reports, download_announcement_pdf
from scutio_data.data_sources import status
```

`_providers`、`_runtime`、`_documents` 为内部实现，不作为业务脚本的导入入口。
