# 行情、交易事件与市场宽度（`market` / `events` / `breadth`）

**范围**：报价/K 线、交易日历/停复牌/事件、市场宽度/指数成分。
**市场**：行情 **A/港/美 ✓**；事件和宽度 **A ✓ · 港/美 ~**。
**门面**：`market.security_*` · `events.*` · `breadth.*`。
**信封**：`ok` / `partial` / `error` / `warning` / `quotes` 或 `bars` / `missing` 等。

---

## 门面

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `security_quote(codes, sources=None)` | ✓ | ✓ | ✓ | 批量报价；单码失败不拖垮整批 |
| `security_bars(code, frequency='D', count=80, adjust='none', …)` | ✓ | ✓ | ✓ | K 线；**默认不复权** |
| `tencent_quote` / `sina_quote` / `eastmoney_quote` | ✓ | ✓ | ✓ | 单源调试；业务默认勿直接调 |
| `events.trade_calendar(market, start_date, end_date)` | ✓ | ✓ | ✓ | 本地交易所规则；历史可由真实日 K backup |
| `events.suspensions(day, code=None)` | ✓ | ~ | — | A AKShare 东财→百度；港股百度可选 |
| `events.earnings_calendar(report_date, code=None)` | ✓ | — | — | AKShare 东财，沪深/京市分段 |
| `events.performance_updates(report_date, kind='all', code=None)` | ✓ | — | — | 业绩预告/快报；结构化东财单源 |
| `events.company_events(day, code=None)` | ✓ | — | — | 公司动态摘要日历；`discovery_only` |
| `breadth.market_breadth(market='a')` | ✓ | ~ | ~ | 涨/跌/平家数；A 乐咕聚合→新浪全量 |
| `breadth.index_constituents(index_code, include_weights=True)` | ✓ | — | — | 中证官网成分/权重→新浪成分 backup |

```python
from scutio_data.market import security_quote, security_bars
from scutio_data.events import trade_calendar, suspensions, earnings_calendar
from scutio_data.breadth import market_breadth, index_constituents
```

- `sources=None`：按**每只票的市场**自动选链（混合批次按票分支）。
- 混合批次只要有一项失败：`ok=True, partial=True, error=None`，失败项进入
  `invalid` / `missing`，说明放在 `warning`；并返回 `requested_count` / `returned_count`。
- `security_bars` 的非法代码、非正 `count`、未知频率/复权参数会抛 `ValueError`；外部输入应先校验或捕获。源请求失败才返回失败信封。
- 默认链与覆盖方式见 [`11-fallback.md`](11-fallback.md)。
- 均线（MA5/10/20）对 OHLCV **本地算**，无单独均线上游。

### 事件与宽度的口径

- `earnings_calendar.report_date` 是报告期末，不是查询日；`scheduled_date` 已取最后一次变更预约日。
- `performance_updates` 的 `forecast`/`express` 数值来自结构化表；巨潮公告只作原文证据，不冒充同构 backup。
- `company_events` 只发现线索；若与业绩、披露、分红、回购等专项门面重叠，以专项结构化数据或原公告为准，不并入同一事实两次。
- `suspensions(code=...)` 无记录时为 `status=unknown`，不是 `active`；百度 backup 只覆盖当日停复牌事件，返回 `partial=True`。
- `trade_calendar` 的 K 线 backup 只证明过去实际开盘，不能推断未来；未来规则失败时返回失败。
- `market_breadth` 只计算同一时点可取到报价的证券，读 `coverage/complete/upstream_total` 判断覆盖。乐咕聚合仅含沪深 A 股，不含北交所，返回 `partial=True`；`total` 及涨跌占比的分母为上涨、下跌和平盘家数之和，不含单列的停牌家数，与源站含停牌分母的“活跃度”不同。其中涨跌停聚合数字只作为市场背景。
- 中证权重文件单腿失败时仍保留成分，返回 `partial=True, weight_error=...`。
- 港美事件接口缺稳定免费同口径源时返回 `unsupported_market`，不会把实际披露日冒充未来财报日。

---

## 代码写法

| 市场 | 推荐 | 亦接受 | 禁止 |
|------|------|--------|------|
| 港 | `hk00700` | `00700.HK` | 裸 `00700` |
| 美 | `usAAPL` | `AAPL.US` | 裸 `AAPL` |
| A | `600519` / `sh000001` | — | 指数勿省略交易所前缀 |

### 指数（必读）

沪市指数与深市个股六位码大量重叠。

| 指数 | 正确代码 |
|------|----------|
| 上证指数 | `sh000001` |
| 上证50 | `sh000016` |
| 沪深300 | `sh000300` |
| 中证500 | `sh000905` |
| 中证1000 | `sh000852` |
| 科创50 | `sh000688` |
| 深证成指 | `sz399001` |
| 创业板指 | `sz399006` |
| 北证50 | `bj899050` |

**`000001` 无前缀 = 平安银行，不是上证指数。**

---

## 解读

报价常见字段（以腾讯规范化结果为代表；多源字段子集可能更少）：

| 字段 | 含义 |
|------|------|
| `name` / `price` / `last_close` / `change_pct` | 名称、现价、昨收、涨跌幅 |
| `amount` | 成交额，**本币元**（CNY/HKD/USD，见 `currency`）；源缺失时为 `None` |
| `amount_wan` | 成交额，**本币万元**；有值时 `amount_wan ≈ amount / 10000` |
| `volume` / `volume_unit` | 成交量统一为股（`share`）；单位未核实或缺失时为 `None` |
| `volume_raw` / `volume_raw_unit` / `volume_precision` | 原始量、原始单位和换算精度（股）；按手报价的源精度为 100 股，不补造零股 |
| `pe_ttm` / `pb` / `mcap_yi` / `float_mcap_yi` | 估值与市值（`*_yi` = 亿元量级） |
| `pe_dynamic` / `pe_static` | 腾讯 A 股动态 PE；未核实的静态 PE 为 `None`，不能把动态 PE 当作静态 PE |
| `limit_up` / `limit_down` | A 股涨跌停；源未提供或港美无此语义时为 `None` |
| `exchange` / `symbol` / `currency` | 市场与货币 |
| `time` / `data_as_of` / `retrieved_at` | 源报价时间、可用的数据时点、抓取时间；保留各源时区与交易时段语义 |

- 字典键：**始终**含完整代码（`sh000001`、`hk00700`、`usAAPL`）；裸码不冲突时额外提供裸码键。
- 同一批次若出现相同裸码（如 `sh000001` 与 `sz000001`），不会提供歧义裸键 `000001`；必须读完整代码键。
- PE/PB/市值等上游未提供时为 `None`，不得把未知解释成 0。
- 指数不适用的涨跌停价为 `None`；不能把源站的 `-1` 占位值当作价格。
- 腾讯普通 A 股报价成交量按手换算，科创板及港美股按股；东财 A 股（含科创板）按手换算。Financial API 报价成交量为股。日线成交量使用各自的解析规则。
- K 线行字段为 `datetime/open/high/low/close/volume`，日期读 `bar["datetime"]`；`date` 与报价的 `time` 不是 K 线日期字段。`adjust` 默认 `none`；可显式 `qfq` / `hfq`（视源能力）。返回顶层 `quality.status=ok|partial`、请求/返回条数、最大日期间隔与问题列表；极少样本跨超长时间断层会拒绝该源，不能用来计算“单日”涨跌。
- OHLC 缺失、非有限数值或高低价关系错误会拒绝该来源并尝试后备来源；缺失值保留为 `None`，不补零。前复权历史价格可能为负，不仅凭正负判断数据错误。
- 美股 K 线经 AKShare 东财解析市场 ID，并以 AKShare 新浪作为日线后备；调用方只传 `usAAPL` 等标准完整代码。
- **成交额契约（硬）**：`amount` = 元，`amount_wan` = 万元；跨源/跨市场已归一。
  **禁止**再对 `amount` 或 `amount_wan` 二次 /10000；展示用万直接读 `amount_wan`，用元读 `amount`。
- 新浪美股报价没有成交额，`amount/amount_wan=None`、`coverage.amount=False`，并标记 `partial=True`；其总市值归入 `mcap_yi`。缺失成交额不能当作 0，也不能用市值或现价乘成交量替代。
  （实现：腾讯 A 源字段本是「万」时内部已升为元；其它源按元入。）

---

## 陷阱

- 需要实时价量时走 `security_*`，不要为「换源」改调东财列表类接口。
- 空 `quotes`/`bars` 且 `ok=False` 是**失败**，不是「无行情」。
- 东财美股内部会解析并缓存 105/106/107 市场号；调用方仍只传 `usAAPL` / `AAPL.US`，不要手写 secid。
- 盘后 / 隔夜不是实时成交，汇报须说明时点。
- 免费报价可能延迟，尤其港股；不能用抓取时间掩盖源时间，也不能把不同时点的跨源价格差直接判为数据错误。
- 盘中返回的当日、当周、当月 K 线可能尚未收盘，最后一根仍会变化；与只返回已收盘日线的源比较时先对齐日期和交易时段。
- **`amount` / `amount_wan` 单位已固定**；勿按 `source` 猜测缩放（标准报价的 `amount` 为元，`amount_wan` 为万元）。

---

## 边界

| 支持 | 不支持（本模块非目标） |
|------|------------------------|
| A/港/美 报价与日/周/月 K | 龙虎榜（见 capital） |
| A 交易事件/宽度/中证成分；港美交易日历 | 港美完整财报预测日历、港美指数全量成分 |
| | 完整 SEC/HKEX 原始披露解析 |
| | 港美 Level-2 / 美股盘前盘后精细字段 |

单源调试 API（`tencent_quote` / `sina_quote` / `eastmoney_quote`）属于实现/排障，**业务默认不要直接调**。
模块分工：`market` 取数编排；`_providers.quote_parse` 负责源字段解析，仅内部使用。


## 数据覆盖

有 Key 的 A 股报价/日线优先 Financial API；报价不包含 PE/PB，估值请调用 `valuation_snapshot`。响应顶层时间仅保留为 `provider_timestamp`，其业务含义未经确认，不能当作报价时间。报价 `time` / `data_as_of` 留空，`coverage.timestamp=False` 并标记 `partial`；`retrieved_at` 也不能当作交易时点。

免费 A 股不复权日线走 AKShare 新浪 → 腾讯 → 东财；港美日线走 AKShare 东财 → 新浪。指数和 ETF 使用对应的 AKShare 接口，新浪 ETF 专用接口仅提供不复权日线，周/月线走 AKShare 东财。A 股、美股市场宽度和指数成分/权重同样经 AKShare 获取；港股宽度因东财港股快照停用而暂不可用。报价已尝试 AKShare 全市场及盘口候选，字段/时点与可用性尚未满足；直接报价适配登记为 `status=retained`，详见数据源状态。

`amount=None` 表示源未提供已验证成交额，不能当作 0。顶层 `coverage` 区分成交额覆盖和样本条数质量。支持的频率为 `D/1D`、`W/1W`、`M/1M`；其他频率在发起源请求前抛 `ValueError`。

`attempted_sources` 是尝试过的源，`sources_used` 才是实际贡献数据的源。自动回退本身不表示数据不完整，原因放在 `fallback_reason`。


复权价格采用源站原生口径，`adjustment_basis` 标记来源和跨源不可等同；不拼接不同源的复权序列。免费 A 股前复权走 AKShare 东财 → 新浪，后复权走 AKShare 新浪 → 东财；有 Key 且能力匹配时先尝试 Financial API。AKShare 1.18.94 腾讯实现优先选 `day`，无法证明所选序列为请求的复权口径，因此仅用于不复权日 K。


业绩快报 `performance_updates(kind='express')` 已通过 AKShare，保留金额、同比、每股指标及报告期；源接口没有暴露 `is_latest`/`data_type`，字段和覆盖说明明确标记缺失。业绩预告 `forecast` 暂留直接取数：AKShare `stock_yjyg_em` 未保留预测金额与变动幅度上下限，不能把中值冒充区间。该分支仍需检查 Financial API 或其他候选。

快报收入、利润金额为元，每股指标为元/股，比率为百分数；预告区间按 `metric` 区分，不能把每股收益也当作总金额。预告金额、上年同期值可能经过取整，增长率也涉及追溯调整等比较口径；遇到不一致须回到公告核对比较基准，不能仅凭结构化摘要推断内生增长。预告分页会核对数量及证券、报告期、科目身份，重复或缺页按来源错误返回。

预约披露通过 AKShare 提供，`report_date` 仅接受季末日期，`report_date_basis=requested_period` 明确其来自查询参数。最后一次变更（而非日期最大值）决定 `scheduled_date`，`actual_date` 单独保留。全市场请求覆盖沪深与京市，缺失段显示在 `coverage.missing_segments`；查询失败返回错误信封。

A 股停复牌快照可能含未来计划，`status=scheduled` 表示停牌尚未开始。`resume_date_basis=expected` 表示预计复牌，不证明实际交易状态；预计复牌到期但未确认时 `status=unknown`，无数据也不是 active。停复牌主路径与百度事件备用均通过 AKShare；百度事件不能证明当前交易状态。
