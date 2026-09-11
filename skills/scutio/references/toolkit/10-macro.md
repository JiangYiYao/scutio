# 宏观（`macro`）

**范围**：中美利率/国债、中国与美国命名经济序列（含社融）、汇率/商品快照、A 股指数板。  
**市场语境**（本模块是**宏观序列**，不是个股市场 API）：

| 子域 | 说明 |
|------|------|
| 中国利率 / 经济序列 | LPR、SHIBOR、CPI·PMI·货币·**社融**·外储等，服务 **A 股/中国宏观** |
| 美国经济序列 | CPI、非农、失业率、ISM、联邦基金目标等（`us_*`） |
| 中美国债、USDCNY | 债/汇；**不是**美股个股报价 |
| 指数板 / 商品 | 主要 A 股指数与 COMEX/WTI 期货报价；港美个股仍走 `security_*` |
| 覆盖缺口 | 美国部分指标保留受限东财查询；带源时间 USDCNY 暂保留新浪 |

**原则**：只取数，不写宏观叙事或交易结论。  
**首选（但重）**：`macro_snapshot()` —— 多腿取数（各 AKShare 子调用有独立总时限，聚合可能较慢），仍不宜当轻量探针；超时或只关心子集时改用 `list_macro_series` + 单序列 / `rates_snapshot` / `bond_yields_cn_us` 等。
**按名取序列**：不确定有哪些名时先 `list_macro_series(market=...)`，再 `macro_series` / `cn_macro_series` / `us_macro_series`。

---

## 门面

| 函数 | 中国 | 美国 | 说明 |
|------|------|------|------|
| `macro_snapshot(preloaded=None)` | ✓ | ✓ | 聚合快照（**重；耗时随取数腿数增加**）；已取腿可复用 |
| `lpr_history` / `rates_snapshot` | ✓ | — | LPR；SHIBOR ON/1W/1M/3M/1Y |
| `cn_macro_series(name)` | ✓ | — | 中国命名序列 |
| `us_macro_series(name)` | — | ✓ | 美国命名序列 |
| `macro_series(name)` | ✓ | ✓ | 统一路由（`us_*` → 美，其余优先中） |
| `bond_yields_cn_us` | ✓ | ✓ | 中美国债收益率 |
| `fx_usdcny` | ✓ | ✓ | 美元兑人民币 |
| `commodities_spot` / `index_board` | ✓ | ~ | 金/油、A 股指数板 |
| `economic_calendar(day, …)` | ✓ | ✓ | 日历、实际/预期/前值；AKShare 百度聚合口径，逐日查询、区间最多 14 日，使用[批量预算](12-data-sources.md#请求控制) |
| `macro_surprises(names, limit, fallback=False)` | ✓ | ✓ | `actual-forecast` 历史；actual-only 备源须显式开启 |
| `list_macro_series(market=None)` | ✓ | ✓ | 清单；`market='CN'/'US'` 过滤 |

```python
from scutio_data.macro import (
    macro_snapshot, cn_macro_series, us_macro_series, macro_series, list_macro_series,
)
```

### 指数板（`index_board`）

```python
from scutio_data.macro import index_board

board = index_board(["000001.SH", "sz399001"])
```

默认查询六个主要 A 股指数。代码按完整交易所身份规范化并去重，示例返回的 `indices[*].code` 为 `sh000001`、`sz399001`；沪市指数必须明确写 `sh` 或 `.SH`。报价复用 `security_quote`，只为未取到的标的继续回退，不按数字后缀匹配其它证券。

检查 `partial/missing/invalid`、`requested_count/returned_count` 和 `coverage.requested_codes/returned_codes/missing_codes`。全部失败为 `ok=False`；部分标的或字段缺失为 `ok=True, partial=True`。`sources_used/attempted_sources/errors/fallback_reason` 保留取数路径，行内 `source/partial/warning` 保留单项状态；回退已补齐时，早先来源的错误不代表最终数据不完整。`macro_snapshot` 在 `indices_status` 中保留这些元数据，指数缺项会使聚合结果标记 `partial=True`。

### 中国序列（`cn_macro_series`）

| 名 | 含义 |
|----|------|
| `cpi_yoy` / `ppi_yoy` | CPI / PPI 同比 |
| `pmi_mfg` / `pmi_non_mfg` | 官方制造 / 非制造 PMI |
| `m2_yoy` | M2 同比（同行 M1/M0） |
| `rmb_loan` | 新增人民币贷款 |
| `social_financing` | **社融增量**（亿元；AKShare 商务部；含贷款/债券/股票等分项） |
| `export_yoy` / `import_yoy` | 出口 / 进口同比 |
| `industrial_yoy` / `retail_yoy` / `fai_yoy` / `gdp_yoy` | 工业 / 社零 / 固投 / GDP |
| `rrr` | 存款准备金率（大型机构调整后） |
| `forex_reserves` / `gold_reserves` | 外汇储备 / 黄金储备 |
| `fdi` | 当月实际使用外资（`unit=k_usd`，千美元；`ytd` 为年初累计） |
| `consumer_confidence` / `boom_index` / `tax_revenue` | 消费者信心 / 企业景气 / 税收 |

### 美国序列（`us_macro_series`）

| 名 | 含义 |
|----|------|
| `us_cpi_yoy` / `us_core_cpi_yoy` / `us_cpi_mom` | CPI 同比 / 核心同比 / 环比 |
| `us_unemployment` / `us_nfp` | 失业率 / 新增非农 |
| `us_ism_pmi` / `us_ism_services` | ISM 制造 / 服务 PMI |
| `us_fed_funds_upper` / `us_fed_funds_lower` | 联邦基金利率目标上/下限 |
| `us_gdp_qoq` | GDP 环比 |
| `us_retail_sales_mom` | 零售销售环比 |
| `us_michigan` / `us_cb_confidence` | 密歇根 / 谘商会信心 |
| `us_ppi_core_yoy` | 核心 PPI 同比 |

统一点字段习惯：`date` · `name` · `value` · `unit` · `freq` · `source`（美序列另有 `market=US`、`indicator_id`、`prev_value`）。

---

## 解读

| 类别 | 主源类型（溯源用） |
|------|-------------------|
| 中国经济表 / LPR / SHIBOR / 国债 | AKShare 东财公开表 |
| 社融增量 | AKShare 商务部月表 |
| 美国命名序列 | 优先匹配 AKShare；核心指标字段缺口或源数据过期时走已登记的受限东财查询 |
| 一致预期历史 | AKShare 金十；保留历史日期、预期和前值，可能停止更新 |
| USDCNY | 新浪带源时间报价；标准来源的字段与时点限制见数据源状态 |
| 金 / 油 | AKShare 外盘期货报价，不代表实物现货 |
| 指数板 | 公共 `security_quote` 门面，按标的身份与市场回退 |

美国 AKShare 金十候选中的实际值多停留在 2025 年，且全历史分页可能中途失败。命名实际值序列在历史过期、空缺或 AKShare 失败时复用既有的东财指标白名单；返回来源和统计期，不扩展指标范围。该回退不提供一致预期，不能用于补造 forecast。核心 CPI/PPI 同比及联邦基金目标上下限尚无已验证的同构映射。不会把旧数据当作最新发布值。

- `macro_surprises` 默认不返回无 forecast 的重复实际值；显式 `fallback=True` 时才返回 `actual_only_fallback`，其 `surprise=None`。
- `economic_calendar` 后续日期失败时保留已完成的日期，标记 `partial/errors/completed_days`；未查询日期不解释为无事件。
- `economic_calendar` 的市场预期没有“官方预期 backup”：统计机构发布日程和实际值，不发布市场一致预期。
- 金十网页静态日历与东方财富财经日历不能作为默认同口径 backup：前者 CDN 可用性不稳定，后者仅有日程、没有一致预期；Trading Economics 字段同构但需要 API key。不要把这些来源拼接成百度结果。

`source` 字段仅作溯源标签；**不要**据此再写一套切源逻辑。

---

## 陷阱

- 失败用 `result_list_err` / `result_err` —— **禁止**把失败说成「今日无数」。
- `macro_snapshot` **允许部分腿失败**：成功腿保留真值，`errors` 写缺口。  
- 已取单项宏观腿时传入 `macro_snapshot(preloaded=...)`，或直接使用单项结果；不要再次全量采集。
- `macro_snapshot` 仍较重：按注册的单序列路由取数；需要子集时直接调用对应序列，避免重复拉取。
- 美国表常见 **未发布占位行**（`VALUE` 为空）—— 模块会跳过，取最近有数点；**勿把 `prev_value` / 金十「前值」当本期今值**。
- 社融 `value` = **当月增量（亿元）**，可负；分项字段见 `rmb_loan` / `corp_bond` 等。
- AKShare 在代理或 TLS 错误时可在剩余预算内直连重试，仍验证证书；失败时 `ok=False`，不以其它指标冒充社融。
- 汇率/商品代码**不**走 A 股 `split_code` 启发式。
- 非农 `us_nfp` 单位为 **千人**（`k_persons`）；外储多为 **亿美元**。
- `fdi` 金额为千美元（`k_usd`），已按商务部 2023 年 1–5 月累计 843.5 亿美元核对；月份和累计不能混用，源历史仍可能过期。

---

## 边界 / 缺口

当前**不保证**或不覆盖：

- 社融 **存量** / 同比结构（当前仅 **增量** 月表）
- 财新 PMI、城镇调查失业率（统计局源未接入）
- 中债官网全曲线
- 欧日英等其它国家宏观（仅中美命名序列 + 中美债）
- AKShare 没有全序列备用；无已登记替代时失败如实返回
- 腾讯、同花顺 **不做** 宏观序列备胎

排障总索引 → [`11-fallback.md`](11-fallback.md)。

当前取数：国内宏观、利率/国债、一致预期历史及商品报价走 AKShare；部分美国指标和带源时间 USDCNY 存在已登记缺口。宏观返回 data_start/data_end、stale、partial；FDI、金十等源可能停止更新，不得把取数时间当最新统计期。中国 GDP 累计季度使用期末日，美国 GDP 是季度环比折年率，金十使用发布日期，不能直接拼接；时效标记是粗粒度检查，最新修订仍需核对发布机构。黄金储备是货币价值（yi_usd，亿美元），非实物吨数；非农万人转千人时 actual/forecast/previous 同步乘 10。SHIBOR rate 为百分比，change 为基点。消费者预期指数的库错误列暂返回 None。USDCNY 使用源最新价并保留买卖价、日期和时间；COMEX/WTI 为期货报价。
