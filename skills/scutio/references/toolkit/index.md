# Scutio Toolkit · 取数工具包

真实公开市场**取数**：不编造、不给买卖评级、不写完整研报。
**只调能力门面**；多源/限流/备胎由 `scutio_data` 内部处理——不要自选源站或手写 fallback。
字段与陷阱 → [01-runtime](01-runtime.md) … [11-fallback](11-fallback.md)（按需打开）。

这些接口为查证、研究、推演、复核和复盘共享。独立请求可用 [批量调用](01-runtime.md#批量调用)；有明确样本与条件时用 [程序筛选](screening.md)，不以热榜或筛选结果生成机会排名。

| 用 | 不用 |
|----|------|
| 行情 / 财务 / 公告 / 日频资金 / 宏观 | 纯观点闲聊 |
| 为当前问题提供数据 | 替代事实核实、因果解释或用户决定 |

---

## 1. 运行时

用 **`SCUTIO_PYTHON`**；包根 **`SCUTIO_TOOLKIT_SCRIPTS`**（含 `scutio_data`）→ `from scutio_data.<域> import …`。
路径 / env / 依赖 / 缓存 / 非入口 → [01-runtime](01-runtime.md)。

首次使用一个接口前，读取下方对应域的函数签名与返回字段；不从其它接口类推参数名。不确定时可离线运行 `inspect.signature(已导入的函数)`，修正调用后只重试失败项。

| 市场 | 写法 | 注意 |
|------|------|------|
| A | `600519` / `sh000001` | 裸 `000001` = 平安银行，不是上证指数 |
| 港 | `hk00700` / `00700.HK` | 裸 5 位**不**当港股 |
| 美 | `usAAPL` / `AAPL.US` | 裸 ticker **拒绝** |

代码按完整格式严格校验；超长位数、尾随字符或市场后缀冲突直接失败，绝不截断成另一只证券。

```python
from scutio_data.market import security_quote, security_bars
from scutio_data.core import envelope_items
from scutio_data.announcements import periodic_reports, download_announcement_pdf
from scutio_data.research import stock_reports, download_pdf, list_local_reports
# 其它域同理：feeds / capital / macro / fundamentals …
```

---

## 2. 路由

**市场图例**（以速览表为准；分域标题不重复标注）：
`A` 沪深京 · `港` · `美` · `✓` 支持 · `~` 部分 · `—` 勿调（港美披露改 IR / 年报 / SEC）。

详文位于本文件同目录：`NN-name.md`，下表与分域标题只写 `NN-name`。

### 速览

| 域 | A | 港 | 美 | 详文 |
|----|---|----|----|------|
| 行情 `security_*` | ✓ | ✓ | ✓ | [02-market](02-market.md) |
| 交易日历 / 停复牌 / 事件 `events` | ✓ | ~ | ~ | [02-market](02-market.md) |
| 市场宽度 / 指数成分 `breadth` | ✓ | ~ | ~ | [02-market](02-market.md) |
| 档案 `stock_info` | ✓ | ✓ | ✓ | [03-fundamentals](03-fundamentals.md) |
| 三表 `financial_report` | ✓ | ✓ | ✓ | [03-fundamentals](03-fundamentals.md) |
| 个股资料长文 `stock_materials` | ✓ | — | — | [03-fundamentals](03-fundamentals.md) |
| 估值快照（报价侧） | ✓ | ✓ | ✓ | [03-fundamentals](03-fundamentals.md) |
| 历史估值分位 | ✓ | — | — | [03-fundamentals](03-fundamentals.md) |
| 研报 / 一致预期与修订 | ✓ | — | — | [04-research](04-research.md) |
| 互联互通（北向/南向） | ✓ | ~ | — | [05-capital](05-capital.md) |
| 个股资金/两融/题材等 | ✓ | — | — | [05-capital](05-capital.md) |
| 资讯 `feeds` | 见下 | 见下 | 见下 | [06-feeds](06-feeds.md) |
| 公告 / 定期报告 / 互动易 / 解禁 | ✓ | ✓ MVP | ✓ SEC | [07-announcements](07-announcements.md) |
| 宏观（中美序列/债汇） | ✓ | — | ~ | [10-macro](10-macro.md) |

资讯细分：新闻 A✓ 港~ 美~ · 电报/全球 跨市场中文流（**≠** 交易所公告）。
宏观的「美 ~」= 美国**宏观序列**（CPI/非农等），**≠** 美股个股（个股走 [02-market](02-market.md) / [03-fundamentals](03-fundamentals.md)）。

### 行情 · `market` · [02-market](02-market.md)

- `security_quote` — 报价；读 `ok` / `quotes`
- `security_bars` — K 线；默认 `frequency='D'`、`adjust='none'`；读 `ok` / `bars`
- `events.trade_calendar` / `suspensions` / `earnings_calendar` / `performance_updates` / `company_events` — 交易日历、停复牌、披露预约、业绩预告/快报与公司事件；`company_events` 仅发现线索，事实优先专项门面/原公告
- `breadth.market_breadth` / `index_constituents` — 涨跌家数、中证成分与权重

### 基本面 · `fundamentals` + `valuation` · [03-fundamentals](03-fundamentals.md)

- `stock_info` — 结构化轻量档案（名/行业/股本/市值…）
- `financial_report` — 三表 `result_list`（`lrb`/`llb`/`fzb`；港美须 `hk*`/`us*`；美可 `period=quarter`）
- `stock_materials` — 资料长文信封（仅 A；目录读 `items`，正文读 `text`；**≠** `stock_info`）
- `valuation_snapshot(code, quote_env=None)` — 报价侧估值（**不含**一致预期）；已有 quote 必须复用
- `valuation_history(code, include_series=True)` — A 股 PE/PB/PS 等历史序列与 3年/5年/可得全期分位；东财→百度，紧凑调用传 `include_series=False`
- `forward_pe` / `calc_peg` / `pe_digestion` — 纯公式（EPS/CAGR 自备）
- `research.consensus_forecast` — 标准化一致预期（仅 A；原始 `eps_forecast` 仅用于源字段诊断）

### 研报 · `research` · [04-research](04-research.md)

- `stock_reports` / `industry_reports` / `broker_reports` — 列表；时效看 `publishDate`
- `report_page_detail` / `report_abstract` / `download_pdf` — `path` + `text_path`；缓存 `cache/documents/reports/{code}/`
- `list_local_reports` — 本地文件 + `stale`
- `consensus_forecast` / `consensus_revisions(reports=None)` — 标准化一致预期（AKShare 同花顺）/逐机构研报修订；已有研报列表须复用
- `eps_forecast` — 同花顺原始表，仅调试源字段时使用
- `local_report_search` / `local_stock_screen` / `dedup_articles` — 本地工具

### 资金 · `capital` · [05-capital](05-capital.md)

- `southbound_daily` / `northbound_daily` / `mutual_connect_daily` — 互联互通（**北向 ≠ 港股通**；南向=内地买港股资金流）
- `stock_fund_flow_120d` / `industry_comparison` / `concept_blocks` / 两融·大宗·股东·分红 — 个股与结构（仅 A）
- `share_repurchases` / `shareholder_changes` / `pledge_status` / `corporate_actions(preloaded=None)` — A 股结构化公司行动；聚合或明细二选一
- `ownership_filings` — 美股 SEC 股东与内部人申报列表
- `dragon_tiger_board` / `daily_dragon_tiger` — A 股龙虎榜，按上榜原因保留席位与机构统计；仅按需调用

### 资讯 · `feeds` · [06-feeds](06-feeds.md)

- `stock_news` — 媒体检索；默认时间倒序；`order='relevance'` 相关优先
- `telegraph` / `global_news` — 中文快讯流
  相关：公告 → [07-announcements](07-announcements.md)

### 公告 · `announcements` · [07-announcements](07-announcements.md)

- `stock_announcements` — 巨潮列表（仅 A）
- `periodic_reports` — A 巨潮 · 港巨潮原文 · 美 SEC；`file_url` / `pdf_url`
- `download_announcement_pdf` — 原文 + `.txt` → `cache/documents/filings/{a|hk|us}/{code}/`
- `extract_filing_text` — 本地 PDF/HTML → 文本
- `irm` — 深市互动易；`lockup_expiry` — A 股解禁。
  读披露优先 `text_path`

### 宏观 · `macro` · [10-macro](10-macro.md)

- `macro_snapshot` — 聚合快照（**重**，耗时取决于取数腿数）；子集用单序列
- `list_macro_series` / `cn_macro_series` / `us_macro_series` / `macro_series` — 先 list 再取；序列名见 [10-macro](10-macro.md)
- `lpr_history` / `rates_snapshot` / `bond_yields_cn_us` / `fx_usdcny` / `commodities_spot` / `index_board` — 利率/债汇/商品/指数板
- `economic_calendar` / `macro_surprises` — 宏观日历与 actual-forecast；actual-only fallback 默认关闭

非入口（`em_get` / 单源 quote / `_providers` / `_documents` / `self_check` 等）→ [01-runtime](01-runtime.md) / [11-fallback](11-fallback.md)。

---

## 3. 信封与硬规则

业务 API 返回 **dict**，至少 `ok` / `error` / `source`。

| 状态 | `ok` | 列表类 |
|------|------|--------|
| 成功有数 | `True` | `items` 等非空 |
| 合法空（如非交易日池） | `True` | `items=[]` |
| 请求失败 | `False` | 通常 `[]`；**禁止**说成「今日无数」 |

有部分可用数据时统一为 `ok=True, partial=True`；`ok=False` 表示没有可用业务数据。
上游未提供的数值字段用 `None`，不得用 `0` 冒充未知。

| 形态 | 数据键 |
|------|--------|
| `result_list` | `items` |
| `result_ok` | 对象字段（档案、下载信封等） |
| 行情 | `quotes` / `bars` |

少数纯数/原生结构见对应详文。列表用 `envelope_items(env)` 或 `env["items"]`，**不要** `for x in result`。下载成功读 `path` / `text_path`。

### 硬规则（必遵）

| # | 主题 | 规则 |
|---|------|------|
| 1 | 时点 | 引用数字带时点；`source` 只溯源，**勿**据此再写切源逻辑 |
| 2 | 盘态 | 盘后/隔夜须说明非实时成交 |
| 3 | 单位 | 金额按字段单位；报价 `amount`=本币元，`amount_wan`=本币万元（≈`amount/10000`），**勿**二次缩放 |
| 4 | 真值 | 成功给数；失败给 `error`/缺口，**不编造** |
| 5 | 研报 | 先列表看 `publishDate` 再下载；`cached`/本地文件 **≠** 最新；可用 `list_local_reports.stale`；**禁止**只扫 cache 下结论 |
| 6 | 披露 | 三表数字 **≠** 已有年报/10-K 原文；要原文 → `periodic_reports` → `download_announcement_pdf` |
| 7 | 港美 | 披露失败或空：跟 `fallback_hint` 自搜 SEC / HKEXnews / IR，**禁止**手搓假财报 |
| 8 | 复用 | 已取 quote/研报/SEC/子池/宏观腿时传给衍生或聚合门面；聚合与明细、主门面与底层 backup **不得并调** |
| 9 | 身份 | 只用文档规定的完整代码；非法代码必须失败，禁止自行截位、补错市场或按模糊裸码取值 |

可选 Financial API Key、免费源与状态查询：[数据服务](12-data-sources.md)。
