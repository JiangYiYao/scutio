# 研报与研究（`research`）

**范围**：个股/行业/机构研报列表、详情摘要、PDF、一致预期表、本地筛选。  
**市场**：**A ✓ · 港 — · 美 —**（AKShare 东财研报、同花顺一致预期）。港美深度材料 → IR / 年报 / SEC / 交易所检索，**不要**对本模块传 `hk*`/`us*` 指望完整覆盖。
**门面**：`stock_reports` / `industry_reports` / `broker_reports` 等。

---

## 门面

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `stock_reports(code, …)` | ✓ | — | — | 个股研报 **result_list**（A 码） |
| `industry_reports(industry_code='*', …)` | ✓ | — | — | 行业研报 **result_list**；码见 `COMMON_INDUSTRY_CODES` |
| `broker_reports(kind='strategy'\|'macro'\|'morning', …)` | ✓ | — | — | 策略 / 宏观 / 晨报 **result_list** |
| `report_page_detail` / `report_abstract` / `download_pdf` | ✓ | — | — | 详情 / 摘要 / PDF+txt；默认 `$SCUTIO_HOME/cache/documents/reports/{code}/` |
| `list_local_reports(code, compare_online=True, online_reports=None)` | ✓ | — | — | 本地已下研报 + 与在线列表比 `stale`；可复用列表 |
| `eps_forecast(code)` | ✓ | — | — | 同花顺原始表；业务使用标准化一致预期接口 |
| `consensus_forecast(code)` | ✓ | — | — | 标准 `year/eps/analyst_count`；AKShare 同花顺逐公司年度 |
| `consensus_revisions(code, reports=None)` | ✓ | — | — | 同机构、同预测财年的 EPS 修订；可复用列表 |
| `forecast_price_changes(code, reports, bars, ...)` | ✓ | — | — | 已取材料的固定窗口预测与参考 PE 变化，纯计算 |
| `local_report_search(...)` | ✓ | — | — | 对 records 本地打分；未传 records 时先在线拉行业列表 |
| `local_stock_screen` / `dedup_articles` | ✓* | ✓* | ✓* | *纯本地工具 |

---

## 推荐工作流（时效硬规则）

1. **先在线列表**：`stock_reports(code)`（或 industry/broker），看 **`publishDate`**。  
2. **再读观点**：`report_page_detail` / `report_abstract`（不必先下 PDF）；下载时把已有 `detail` 传给 `download_pdf`。
3. **需要全文再落盘**：`download_pdf(record)` → **`path`** + **`text_path`** + **`publish_date`**。  
   默认 `$SCUTIO_HOME/cache/documents/reports/{code}/`（无码 → `_misc/`）。
4. **可选**：`list_local_reports(code, online_reports=reports)` 看本地有什么、`stale` 是否落后，复用第 1 步列表。
5. **禁止**：只扫 `cache/documents/reports` 或只信 `local_report_search` 就下结论。

法定披露：`$SCUTIO_HOME/cache/documents/filings/{a|hk|us}/{code}/`，与研报分目录。

原文文件名保留日期、机构与标题，并附加证券、源文档 ID 和已有原文链接的身份摘要；长标题按文件系统字节限制截短。同名研报使用不同文件，旁路 `.txt` 继承相同基名。缓存命中须身份一致且通过 PDF 格式校验；不凭标题复用已有原文。

---

## 解读

列表记录常见字段：`title`、`publishDate`、`orgSName`、`infoCode`、`encodeUrl`、`predictThisYearEps`、`predictNextYearEps`、`emRatingName`、`indvInduName`、`attachPages`、`attachSize`。  
行业记录另有 `industryName` / `industryCode` / `reportType`。

- 可用 `attachPages` 过滤极短稿（如跳过 ≤2 页）。
- `eps_forecast` / `consensus_forecast` 只接受 A 股公司证券，保留完整 `symbol`；指数、ETF、债券返回 `unsupported_asset`。`sh000001` 不会改成平安银行的 `sz000001`。
- `dedup_articles` 优先按 `infoCode/uid` 去重，缺少标识时结合标题、发布日期、机构和证券；同名不同期研报保留。
- `local_stock_screen` 与筛选 CLI 共用数值解析：NaN、无穷和布尔值不参加数值筛选；逗号和百分号只做格式清理，百分数不缩放。
- 筛选和 `sort_by` 共用字段别名；“市值”“总市值”对应报价字段 `mcap_yi`，单位为该证券报价币种的亿元，跨市场比较前先统一币种。
- 同花顺未发布机构预测时可能为空 —— 说明**覆盖不足**，勿当「EPS=0」。
- `consensus_revisions.direction` 是按同一证券、机构和预测财年计算，`revision_scope=broker_report_updates`；不是供应商直接发布的全市场净上调家数。
- 已取 `stock_reports` 后，把同一信封传给 `consensus_revisions(reports=...)` 与 `list_local_reports(online_reports=...)`，禁止重复拉列表。

---

## 陷阱

- **时效**：本地 PDF/txt **不是**「最新研报」。`download_pdf` 的 `cached=True` 只表示该文件已在磁盘；信封 **`note` / `publish_date`** 必须看。  
- `publishDate` / `publish_date` 保留来源列表的展示日期，可能晚于 PDF 首页署名日期。判断材料新旧或事件先后时同时核对正文日期，不把平台收录日期当成报告撰写日。
- 要回答“最新研报/预期”时，用 `stock_reports`（或等价在线列表）核对时效；`stale` 是辅助。解释已给材料或复盘历史判断时可以只读适用的本地材料，标清其时点与范围。
- **不要**假设固定 PDF URL + 裸 `requests` 一定得到真 PDF；常见伪装成 PDF 的风控页。  
- 正确路径：详情页链接 + 模块内下载，校验 **`%PDF`**。  
- HTTP 200 仍可能是风控页；失败时报告缺口，不编造摘要。  
- 全文优先 **`text_path`**，先检查 `text_error`；扫描件或字体映射乱码会标记 `partial=True` 和解析错误。非空文本也需核对是否可读，不能把原文下载成功当成正文已解析。

### `list_local_reports` 信封

| 字段 | 含义 |
|------|------|
| `items` | 本地 `*.pdf`（含 path/text_path/publish_date/mtime） |
| `local_latest` | 本地文件名解析到的最新发布日 |
| `online_latest` | 本次 `stock_reports` 列表最大 `publishDate` |
| `stale` | `True`=已观察到比本地更新的报告；`False`=在线列表身份、日期与覆盖完整且本地未落后；`None`=未比较或无法确认 |
| `partial` / `online_error` | 在线比较失败或覆盖不足；本地文件仍保留 |
| `online_coverage` / `online_errors` | 原在线列表的覆盖与错误信息 |
| `note` | 含「缓存≠最新」硬提示 |

传入的 `online_reports` 会校验信封及每条记录的证券身份；错误证券、混合证券或无法确认身份时不做比较，也不隐式重取。在线失败、陈旧、截断或日期不明时，不能据此断言本地已是最新；仅当确实观察到更晚报告时仍可返回 `stale=True`。

---

## 边界

- 一致预期**只在本模块**；业务优先 `consensus_forecast`，需要同花顺原表才用 `eps_forecast`。`valuation_snapshot`（03）仅报价侧，不含预期。
- 要前向 PE/PEG：本表取 EPS + `valuation.forward_pe` / `calc_peg`（见 [`03-fundamentals.md`](03-fundamentals.md)）。
- 限流与批量失败时的退避见 [`11-fallback.md`](11-fallback.md)。

个股研报列表通过 AKShare 东财获取，保留 PDF 链接、机构、日期、评级及盈利预测；`forecast_years` 给出源字段对应年份。AKShare 不返回作者，`coverage.authors=False`；行业/策略/宏观/晨会列表保留精简的东财直接适配，原文获取由文档模块负责。

一致预期通过 AKShare 的同花顺逐公司年度表获取，机构数与报告数不混用，`report_count=None`。不采用把全市场众数年份统一套到每只股票的东财 EPS 表，避免年份错配；均值字段缺失时报错，不猜测数值列。

### 非个股研报的查询范围

`industry_reports`、`broker_reports` 的 `begin=None` 默认取结束日前 730 天；`end=None` 默认北京时间当天。可传 `begin="2025-01-01", end="2025-12-31"` 查询历史窗口。`max_pages` 为 1–50 的整数；行业每页 100 条，机构研报的 `page_size` 为 1–100 的整数。分页、重试共用[历史列表预算](12-data-sources.md#请求控制)，超时保留已完成页。

- `coverage` 包含 `begin/end`、`pages_fetched/total_pages`、`max_pages/page_size`、去重后的 `returned`，以及 `complete/truncated`。`complete=True` 只表示本次分页已到源接口报告的末尾，不保证供应商覆盖所有研报。
- 达到页数上限且尚未确认取完时，`truncated=True`。源站未给页数时，`total_pages=None`，继续请求至空页或上限，不假定只有一页。
- 后续页失败保留已取条目，返回 `ok=True, partial=True`，`errors` 列出失败页；首页失败返回 `ok=False`。不要把部分成功当作完整列表。错误响应或字段格式变化不会被转换为空列表成功。
- `local_report_search` 未传 `records` 时继承上述默认时间窗口，透传上游 `coverage/partial/errors`；另用 `search_coverage` 表示候选数、匹配数、返回数及是否受 `limit` 截断。

替代来源复核（2026-09-10）：[AKShare 官方文档](https://akshare.akfamily.xyz/data/stock/stock.html)的 `stock_research_report_em(symbol)` 提供个股研报，已用于 `stock_reports`；当前固定版本没有对应的行业、策略、宏观、晨会列表接口。[Financial API 官方说明](https://github.com/HiThink-Tech/Financial-API/blob/main/README.md)未开放研报数据，研究工作流也不是研报列表或原文服务。因此目前不把这部分切到这两个来源。

### 个股研报与 EPS 修订的复用契约

`stock_reports(max_pages=...)` 的参数为 1–50 的整数，控制本地最多返回 `max_pages * 50` 条；AKShare 内部仍先获取该股票的研报数据。`coverage.available_count/returned_count/limit/truncated` 描述 begin 过滤后的本地覆盖，`limit_scope=local_rows`；截断时 `partial=True`，不声称限制了源端请求页数。PDF 链接统一使用 `pdf_url`，下载入口同时接受已有记录的 `pdfUrl`。

`consensus_revisions(reports=...)` 与 `list_local_reports(online_reports=...)` 共用证券身份校验；手工材料每条须提供 `stockCode` 或完整 `symbol`，空列表须在信封中提供身份，所有代码与交易所声明必须一致。错票、混合证券及无身份返回错误。每个 EPS 修订条目对应一个明确的 `fiscal_year`，包含 `eps/previous_eps/change/direction`、前后 `info_code/previous_info_code` 和 `date/previous_date`。没有先前同财年预测时为 `new`，不会把跨年的“本年 EPS”直接相减。`forecast_years`、发布日期、机构或有效 EPS 缺失时跳过并记录 `skipped_reports`，返回部分覆盖；同日 EPS 或显式口径冲突时整组标记 `conflict=True`，第一条也不生成涨跌方向，相邻比较不跨过冲突组取旧值。第一版不按同日时间或版本号自动消歧。相同观测去重但保留 `report_references`；上游 `coverage/errors/partial` 继续保留。

### 固定窗口的预测与价格变化

`forecast_price_changes` 为[发现](../capabilities/discover.md)提供可选线索，自身不联网、不扫描市场、不做收益回测。它与相邻修订共用预测解析；不要把 `consensus_revisions.direction` 直接当作本窗口方向。

```python
from scutio_data.research import forecast_price_changes

# reports 和 bars 是已经取得的同一证券信封；缺少口径核验时保留补证结果。
comparison = forecast_price_changes(
    "688981", reports, bars,
    start_date="2026-06-22", end_date="2026-09-18", fiscal_year=2026,
    max_age_days=120, eps_floor=0.01, share_basis=None,
)
```

窗口、目标财年、陈旧度 `max_age_days` 和近零界限 `eps_floor` 必须显式指定，不能为取得命中再改参数。日线信封须有证券身份、`currency`、`frequency=D/1D`、`adjust=none`，并包含两个指定交易日的收盘价；停牌或缺端点不向前填价，复权价不参加参考 PE 计算。手工复用须保留来源与身份，不能给另一只证券的数据补上请求代码。

预测可来自 `stock_reports`，也可复用 `consensus_revisions` 的标准观测。每条合格端点还须有从原文核实的 `currency`、`eps_basis`（股数/每股基准）、`eps_definition`（如年度归母基本或摊薄 EPS）；两端各字段须一致且币种匹配价格。源未提供时保持未知，不因接口成功就补成相同口径。标准观测包含 `date/source_date`、显式 `published_at/available_at`、`info_code/report_url/report_references`、`retrieved_at`；后者不能当成历史可用时间。

`share_basis` 是本次核验记录，含同一证券的 `symbol`、与调用一致的 `start_date/end_date`、`status=unchanged|changed|unknown`、原文引用列表 `evidence`、`cash_dividends=none|present|unknown`。只有有证据支持的 `unchanged` 才允许自动比较；它表示跨端点预测与价格的每股基准已核对，不等于仅查到一条股本公告。公司行动接口空结果不能证明没有变化。拆股、送转、股数或预测定义变化第一版不自动换算；尚未完成核验时传 `None`，结果保留原值及补证原因。现金分红单独提示，原价 PE 不做派息调整。

每个机构、同一绝对财年分别取端点前最后可用预测。只有日期、没有明确发布时间的材料，从该日期之后才可用于端点；显式时间须带时区，并按 A 股端点日 15:00 判断，首次可用时间延后不会让旧预测变新。没有端点预测、过旧、同日冲突或未知口径均保留原因，不退到更有利的旧预测。今天取得的材料一律标记 `mode=retrospective_current_materials`、`point_in_time_verified=False`，不能据此声称历史当时可交易。

端点冲突只在当时可用的材料内判定，盘后记录不会阻断收盘前已有预测；同日多条已可用记录仍不按时间自动消歧。顶层 `conflicts` 保留全部输入的冲突，端点的 `conflict_scope=endpoint_available_materials` 标明其取舍范围。

| 输出 | 如何解读 |
|---|---|
| `items` | 全部已覆盖机构的端点预测/原文引用、端点价格、可比性与 `reasons`；不是只返回命中机构 |
| `eps_change` / `direction` | 口径可比时的绝对变化与上修/下修/持平；负 EPS 可保留绝对变化 |
| `eps_change_pct` / `pe0` / `pe1` / `pe_change_pct` | 两端 EPS 均大于显式近零界限且为正时计算；PE 是该机构对该财年预测的参考倍数，不是 TTM PE |
| `metrics_computable` / `matched` | 同一合格机构同时 EPS 上修且参考 PE 下降才命中；不可计算为 `matched=None`，可计算但不命中为 `False` |
| `summary` | 配对/可计算/命中机构数，上修、下修、持平及分歧；`not_updated` 另列，可与持平重叠；中位数带实际分母 |
| `coverage` / `input_provenance` | 公司输入、可计算、命中、缺口计数及机构展示范围；同时保留预测与价格的源、时间和覆盖缺口 |

公司级命中须至少有一个**相同机构配对**同时满足条件，不能拼接不同机构的有利指标。`limit` 只截取展示，`summary` 和总数始终基于全部机构。公司计数描述不同覆盖维度，存在可计算配对与尚待补证机构时，`computable`、`missing` 可以同时为 1。

`coverage_status` 描述两个端点是否有未过期、无冲突的预测：`paired/new_coverage/old_only/unavailable`。两端存在预测仍可能因股数或盈利口径未知而不可比；`summary.paired` 只统计口径也已确认可比的配对，不能用它替代机构覆盖总数。

`unchanged_report/not_updated` 只描述两端是否沿用同一预测，即使口径尚不可比或预测已过期也单独计数；未更新不能解释为盈利预期稳定。

公式为 `EPS变化%=(E1/E0-1)*100`、`PE0=P0/E0`、`PE1=P1/E1`、`PE变化%=(PE1/PE0-1)*100`。虚构的 `E0=2, E1=2.4, P0=20, P1=22` 得到 EPS 上修 20%、价格上涨 10%、参考 PE 从 10 降至约 9.17。它也可能命中价格大跌的公司，不能解释成“价格反应温和”或“市场遗漏”。筛选继续复用 [screen_records](screening.md)，再核实经营原因、价值归属和价格要求；没有收益预测有效性的承诺。
