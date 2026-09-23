# 数据服务与配置

免费数据默认可用。Financial API 是可选有凭据服务，具体额度、数据权限与收费以服务方账号为准；AKShare 是免费源适配库，并不能绕过源站限流。

标准来源为 Financial API + AKShare。下文覆盖表描述当前运行路径；status 的 direct_adapters 列出 7 组已确认保留的直接适配、来源和限制，status=retained。只有标准路径无法满足具体请求且有验证证据时才长期保留直接适配；替代通过验收后删除对应旧实现。

## 用户操作

用户可直接说「配置同花顺数据」「只用免费数据」「查看数据源状态」。使用当前 skill 的 `scripts/data_sources.py`，由选定的 Python 3.11+ 运行：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/data_sources.py" configure
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/data_sources.py" status
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/data_sources.py" mode public
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/data_sources.py" mode auto
```

`configure` 使用隐藏输入；自动化可用 `configure --stdin`，从受控标准输入传值。不要把 Key 放在 argv、仓库、研究日志或输出中。凭据写入 `$SCUTIO_HOME/config/credentials.env`（0600）；`HITHINK_FINANCE_API_KEY` 环境变量优先。

### AI 代为保存

用户提供 Key 并要求「配置同花顺数据」「用这个 Key 获取数据」时，由 AI 完成配置，用户不必手动执行命令：

1. 先解析实际配置路径：默认 `$SCUTIO_HOME/config/credentials.env`；设置 `SCUTIO_CONFIG_DIR` 时为该目录下的 `credentials.env`，可通过公开接口 `data_sources.home()` 获取目录。告知用户：「我会把 Key 以明文保存到本机的〈实际路径〉，以后可自动复用。」这是操作提示，已有配置或使用授权时不再追加确认。
2. 运行 `scripts/data_sources.py configure --stdin`，通过宿主提供的受控标准输入传入 Key，或使用 `configure` 的隐藏输入。禁止把 Key 拼入 shell 命令、`echo`、here-document、脚本源码或日志；若宿主无法安全传递输入，改为指导用户在隐藏输入提示中完成配置。
3. 检查命令是否成功，再运行 `status`；成功后只反馈保存路径和是否启用，不展示 Key。环境变量优先于文件，因此 `credential_source=environment` 时明确说明当前生效的是环境变量，不能声称刚保存的 Key 已被验证。保存不会改变已有 `public` 模式；只有用户要求启用同花顺服务时才切换为 `auto`，并检查环境变量是否覆盖了模式。

用户只是提到已有 Key 时，可以提示「我可以帮你保存到本机，后续自动使用」。用户明确说「仅测试一次」「不要保存」时只临时使用；不要从历史对话自行挑选旧 Key 写入配置。保存成功只代表本地配置完成，接口权限以实际调用结果为准。

若未单独设置 `SCUTIO_CONFIG_DIR`，也读取官方 `hithink-finance/credentials.env`：macOS 在 `~/Library/Application Support` 下，Linux 在 `$XDG_CONFIG_HOME` 或 `~/.config` 下，Windows 在 `%APPDATA%` 下。只解析明确变量，不执行 env 文件内容。

`SCUTIO_DATA_MODE=auto|public` 环境变量优先于保存的模式。`auto` 在有 Key 且能力匹配时优先尝试；`public` 禁止所有 Financial API 请求，包含显式 `sources=['hithink']`。

`status` 不联网、不展示 Key。配置存在不等于权限已核实；`verified_capabilities` 只列成功调用过的能力，冷却状态区分全局和单能力。

## 来源与覆盖

| 能力 | 有 Key 的 A 股 | 免费路径 |
|---|---|---|
| 当前上市名册 `universe.stock_universe` | 仍使用交易所名册 | AKShare 沪/深/北交易所名单；报价缺失不剔除成员 |
| 报价 | Financial API 分批快照，可复用已核验名册身份，按缺失标的回退 | 腾讯 → 新浪，每请求最多 100 只 |
| 日线 | Financial API，明确 none/qfq/hfq | AKShare 新浪 → 腾讯 → 东财（均经 AKShare） |
| 估值 | 独立估值端点，复用已有价格 | 报价侧 PE/PB；其他指标未知 |
| 分红 | 每股税前现金与合计送股比例 | AKShare 巨潮实施事件；失败时东财常规记录并标记特别分红缺口 |
| 财报摘要 | `detail='summary'`、Financial API 原生字段 | 显式标明视图的 AKShare 东财完整报表 |
| 完整财报 | 默认 `detail='full'`，保留全部原生字段 | A/港/美均走 AKShare 东财；A 股保留银行专有字段 |
| 跨公司财务特征 `financial_snapshot` | 仍使用 AKShare 跨公司表 | AKShare 东财 `stock_yjbb_em`，指定季末整表后按代码过滤 |
| 公司文本 | 巨潮公司概况/主营业务/经营范围 | AKShare 巨潮 |

指数、ETF、港美股不调用 A 股 Financial API 端点。代码目录必须精确匹配完整代码和 A 股资产类型。指数/ETF/周月线走对应 AKShare 接口，港美日线走 AKShare 东财 → 新浪；新浪股票日线适配不冒充指数接口。

全市场研究先使用 `stock_universe('a')` 的独立上市名册，再取报价/财务特征；名册包含 ST 与停牌成员，不以当前是否有报价决定证券是否存在。沪市主板、科创板、深市和北交所分段取数，保留成功分段及原取得时间；沪/北上游总数与分页证据未被 AKShare 暴露，因此 `is_complete=None` 表示完整性未核实，已知缺段为 `False`。`as_of` 是名册取得时间，不代表历史成分日期。详见 [`screening.md`](screening.md)。

名册原始规范身份可通过 `security_quote(..., identity_records=...)` 复用，避免全市场逐股查询 Financial API 代码目录；必须是具有完整 `symbol/exchange/asset_type` 且无冲突的真实名册记录，不从请求代码自行补造身份。快照响应仍核验本批精确 `thscode`，缺复用记录时继续查询原代码目录。腾讯、新浪和 Financial API 均以最多 100 只分块，单批失败保留其余已成功结果，再由现有报价链补缺失证券。

报价入口按证券回退，不会把所有源字段混成一条无来源的报价。`screen_market` 遇到 Financial API 行所需字段缺失或业务时点未知时，使用同一 `security_quote` 免费链批量补证；每个特征保留实际贡献来源与时点。Financial API 的 `provider_timestamp` 仍不充当报价时间。腾讯/新浪/东财数值缺失保持 `None`，零成交量、零成交额和零涨跌不等于缺失。

`financial_snapshot(report_date, codes=...)` 明确查询一个季末；3/6/9 月为累计值，12 月为全年，不是单季或 TTM。它复用整表快照，不依次请求每家公司的三表；即使设置了 Key，当前也不改为逐股 Financial API 摘要。该表可能含场外或退市发行人，`complete=False`，须与上市名册求交集。当前回溯材料、源同比未知基数、`disclosed_at` 为更新日而非首次披露日等边界见 [`03-fundamentals.md`](03-fundamentals.md)。

`sources=` 可限制候选源，单独指定 `hithink` 失败时不回退。财报完整视图不接受用 Hithink 摘要冒充；摘要回退返回完整源视图，必须读取 `detail` / `field_schema`。

`retrieved_at` 是取得数据的时间，`data_as_of` 是源数据时点，可能为空。Financial API 响应顶层时间保留为 `provider_timestamp`；未确认报价或指标的业务时间时，`data_as_of` 留空，不能把该时间解释为最近指标更新。K 线日期、财报报告期仍使用各自明确的业务日期字段。`fallback_reason` 与数据缺口分开，成功回退本身不导致 `partial`。缺失成交额、登记日期等保持 `None`。

## 请求控制

超时按一次数据操作设置，不限制整项研究的时间。所有来源共用同一套预算：

| 操作类型 | 例子 | 单次来源调用最长用时 | 整次操作最长用时 |
|---|---|---|---|
| 快速查询 | 报价、估值快照、公司概况 | 30 秒 | 60 秒 |
| 历史与列表 | K 线、财报、研报、公告 | 60 秒 | 180 秒 |
| 批量与聚合 | 全市场快照、多日期查询、宏观组合 | 120 秒 | 300 秒 |
| 原文下载 | PDF、HTML 披露原文 | 180 秒 | 300 秒 |

连接通常最多 5 秒，无数据读取等待通常最多 20 秒，下载最多 30 秒；个别端点可更短。传输在可终止的子进程中执行，即使响应持续缓慢发送数据，也受剩余总预算约束。AKShare 内部 HTTP 由其执行器统一终止。原文解析另受文本提取器的限制，表中下载预算不等于全文解析完成时间。

已开始操作后的来源排队、元信息查找、分页、退避、重试和切源都消耗剩余预算，不能每一步重新计时；尚未开始的独立批量项按[批量调用](01-runtime.md#批量调用)计时。单来源耗尽后可在整次操作剩余时间内回退；整次操作耗尽后不再发起新请求。已完成的日期分区、分页和聚合子结果保留，并标记 `partial/errors/coverage`；失败或不完整的响应体不会缓存为成功。错误区分 `queue_wait`、`rate_wait`、`retry_wait`、`response` 等阶段。

`screen_market` 用 `batch.fetch_many` 按 100 只运行独立报价操作，默认不对整次筛选另加全局截止时间；每项仍受查询预算与提供方配额约束。`security_quote` 自身的内部分块不会重置其 60 秒整次预算。需要总时限或取消时，使用现有 `request_timeout` / `cancel_event`，保留已完成批次并读取执行状态和覆盖。

需要更长等待时，由调用方显式覆盖；嵌套调用不能延长已经开始的操作：

```python
from scutio_data.core import request_timeout
from scutio_data.market import security_bars

with request_timeout(360, source_seconds=120):
    bars = security_bars("sh600519", count=250)
```

范围内的多次调用共用上述总预算；默认不包住整个研究流程。

Financial API 固定官方 HTTPS 域名、不跟随重定向；请求体业务 `code=0` 和 HTTP 成功同时满足才解析。同一凭据同机进程共用[提供方配额与执行协调](01-runtime.md#执行配额)。元信息和实际取数共用该来源预算，临时故障最多额外尝试一次。排队超时不记为凭据失效。

认证失败按凭据冷却 300 秒；能力权限失败仅冷却该路径；限流按凭据尊重 Retry-After 并立即回退；数据未准备不会缓存成无事件。成功缓存按凭据哈希、能力、参数、网络路径及解析版本隔离，报价 5 秒、估值 30 秒、日线 60 秒、摘要/分红 1 小时、元信息 6 小时；缓存保留原取得时间，不返回过期缓存冒充当前数据。

AKShare 使用经过回归验证的固定版本，选定接口在子进程运行，使用上表对应操作的来源预算；交易所名册、指定报告期的财务特征、回购和增减持快照共用精确请求的一小时成功缓存，保留原取得时间。AKShare 内部可能执行多次 HTTP，这一限制控制总等待，不保证源站不会限流。AKShare 覆盖财报、研报、公告、分红、估值、资金、公司行动和宏观等领域；具体调用范围见各域说明，直接适配的专项限制见 status。

`SCUTIO_AKSHARE_NETWORK=auto|environment|direct` 控制 AKShare 子进程网络：默认 `auto` 使用环境网络；同一接口与代理配置下已成功的直连路径记忆 300 秒。遇到 `ProxyError` 或 `SSLError` 才在剩余来源预算内补一次直连（证书校验保持启用），重试不重置计时。`environment` 始终遵循环境代理，`direct` 在子进程清除代理并设置 `NO_PROXY=*`，均不自动换线路，也不修改主进程或系统代理设置。认证、限流和普通连接失败不触发直连重试。错误会保留尝试路径与分类，不返回可能含凭据的源站错误原文。

雪球单股报价 `stock_individual_spot_xq` 和东财港股快照 `stock_hk_spot_em` 已停用并从允许调用列表移除，不参与取数、探测或自动重试。港股市场宽度依赖该快照，目前返回 `ok=false`、`unavailable=true`、`reason=source_disabled`，不返回零涨跌家数。个股报价继续使用已验证路径；其他市场快照及东财财报、K 线接口不受此停用影响。


复权价格采用源站原生口径，`adjustment_basis` 标记来源和跨源不可等同；不拼接不同源的复权序列。免费 A 股前复权走 AKShare 东财 → 新浪，后复权走 AKShare 新浪 → 东财；有 Key 且能力匹配时先尝试 Financial API。AKShare 1.18.94 腾讯实现优先选 `day`，无法证明所选序列为请求的复权口径，因此仅用于不复权日 K。


## AKShare 连续失败与版本检查

默认启用检查（`SCUTIO_AKSHARE_UPDATE_CHECK=0` 关闭），不自动安装或改动固定版本。一个接口在相邻失败间隔不超过 1 小时的情况下连续出现 3 次疑似适配错误（如字段缺失、JSON 解析失败、上游 API 错误），会触发一次后台 PyPI 检查。成功或其他错误打断连续计数；合法空结果、代理/连接/超时、HTTP 400/401/403/407/429 不触发检查。被停用的接口在计数前被拒绝。

同机共享状态、非阻塞文件锁、全局 24 小时冷却，避免每个标的都检查一次。后台版本检查总预算 8 秒，与前台取数分开；网络 `auto` 模式先用环境网络，包元数据查询失败或超时再直连，`environment`/`direct` 则固定线路。此恢复仅查询公开包元数据，不代表改变行情接口的重试条件。

数据源 `status` 的 `akshare` 字段展示安装版本、连续失败接口和检查状态。`update_available` 只表示发现适配当前 Python 的非撤回正式版，`fix_verified=false` 表示尚未证明能修复故障；`up_to_date` 表示当前没有更新的兼容正式版，`check_failed` 表示检查本身未成功。用户确认升级后仍需检查更新说明、字段/单位回归和原失败请求；尤其不能跳过腾讯日线的版本相关成交量修正验证。

手动检查（不安装）：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_TOOLKIT_SCRIPTS/data_sources.py" check-akshare
```

检查协议参考 [PyPI JSON API](https://docs.pypi.org/api/json/)，升级背景参考 [AKShare 官方安装说明](https://akshare.akfamily.xyz/installation.html)。仅传包名，失败记录不保存股票代码、请求参数、凭据或源站错误原文。

两融、大宗、质押的多日期查询及增减持方向组合采用批量预算。子请求只使用剩余时间，日期/方向快照成功缓存 1 小时；部分失败、日期边界、缺失字段与原取数时点显式返回。缓存不改变事件发生日期。

龙虎榜列表/席位/机构统计、沪深京预约披露和 A 股停复牌主源均为 AKShare。多上榜原因分组保存，预约变更与实际披露分开；未来停牌计划不标记为当前停牌。


宏观结果读取 `data_end/stale/partial`：历史序列返回成功不代表已更新至当前。黄金储备 `gold_reserves` 为价值（亿美元），美国非农统一为千人。

数据范围聚焦公司、行业与投资判断研究。行情仅保留日/周/月 K；龙虎榜归入 `capital` 按需调用。直接适配是否保留同时取决于研究用途和标准源缺口，不为来源独有字段单独扩大产品范围。
