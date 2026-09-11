# 2026-09-11 取数与字段验收

当前候选已完成 **62 个公开取数入口**的真实调用与字段验收。发现的本地代码错误已修复并对相关用例复测；结果仍包含来源不可用、缺字段、陈旧数据和两项未确认口径，不能称为“全部接口、全部字段通过”。

完整用例与每项检查见 [验收矩阵 JSON](2026-09-11-data-acceptance.json)。该文件是筛选并去除机器路径的审计摘要，不是原始响应副本；原始响应、首次失败、复测结果和原文保存在本次临时验收目录，不随源码发布。

## 范围与结果

测试日期为 2026-09-11，盘中执行；环境为 macOS、Python 3.12.3、AKShare 1.18.94、pandas 2.3.3、requests 2.34.2、pypdf 5.9.0。使用独立配置/缓存目录及安装后的 skill 副本；最后另在仓库外运行采集 CLI，验证了 80 根日线、报价复用及估值时间一致性。

候选以提交 `d15681d299ca9dee39298f0f370f6c6c94483257` 的工作区为基础，包含未提交改动。验收过程中修复了代码，受影响分支已复测；JSON 记录最终 112 个 skill 文件的 SHA-256 和运行依赖。不能把结果视为该基础提交本身已经通过，也不能用它代表此后更新的数据源。

覆盖 62 个可能联网的公开数据入口，包括默认使用本地规则的交易日历及可复用已有结果的聚合入口；纯解析、公式和路径辅助函数另有离线验证。用例含 A 股主板、银行、科创板、创业板、ETF、沪深/北证指数、港股腾讯、美股苹果及 BRK.B 类别股；还覆盖三表/期间、原文、资讯、事件、资金、20 个中国和 14 个美国宏观序列及 15 个预期分支。349 个逻辑用例包含正常输入、明确不支持输入、原文与辅助检查，不是 349 个不同接口；复测替代初次结果，未重复计数。

| 范围 | 用例 | 通过 | 降级 | 不可用 | 明确不支持 | 未确认 | 未修复字段错误 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 行情与Financial API | 73 | 49 | 9 | 15 | 0 | 0 | 0 |
| 财务、估值、研报与原文 | 155 | 95 | 41 | 0 | 19 | 0 | 0 |
| 资金与宏观 | 92 | 47 | 36 | 7 | 0 | 2 | 0 |
| 资讯、事件与宽度 | 29 | 17 | 8 | 1 | 3 | 0 | 0 |
| 合计 | 349 | 208 | 94 | 23 | 22 | 2 | 0 |

“通过”仅表示该用例列出的字段检查通过；“降级”仍存在可用范围或时效限制；“不可用”无法取得可核验数据；“未确认”不能计入通过。强制单一来源的失败不代表默认多源入口全部失败。部分初次代理路径失败在显式直连后恢复，具体网络模式及恢复关系保留在矩阵中。

## 字段检查与实质发现

检查包括证券身份、代码歧义、日期与财季、源时点、币种、股/手及元/万元倍率、空值、OHLC 关系、成交量/金额、复权、原生财报科目、累计与单季、分页完整性、事件状态、原文可读性及来源时效。原生响应映射核对与独立外部证据核对分开记录，不用“非空”代替字段正确性。

| 已确认问题 | 修复与真实验证 |
|---|---|
| 腾讯港股 PB 读错扩展字段 | 腾讯样本由错误的负数改为 3.00；港股与美股使用各自位置。净资产为正与合理数量级得到官方财报佐证，供应商精确汇率/分母仍有局限。 |
| 新浪 A 股丢源时间、BRK.B 转码错误 | 保留日期及时间；类别股使用实际可返回的 `$` 形式，归一回完整代码；零涨跌额也能得到昨收。 |
| 腾讯沪深指数日线成交量少 100 倍 | 纠正 AKShare 留在“手”的指数数据；上证 9 月 10 日由 484,675,114 改为 48,467,511,400 股，与独立新浪序列一致。深证按整手精度匹配；北证不重复换算。 |
| 新浪 ETF 错调股票接口 | 改用已实测的 ETF 专用不复权日线接口，510300 价量额与腾讯对应；不把未验证的复权能力当作支持。 |
| 指数无意义占位值及丢失时点 | `-1` 涨跌停、`0` PB 改为空；指数看板保留六只指数各自的源报价时间，另记录抓取时间。 |
| 金十先截断后排序，选出几十年前数据 | 全部返回记录按日期排序后取最近条目；陈旧预期传播到顶层 `partial`。仍然陈旧的源不会被修复排序后当作最新。 |
| 美国 GDP 频率、增长口径及实际值回退 | 明确季度、环比折年率；源失败时复用已有同口径实际值备用。预期失败仍保持缺口，不用实际值代替预测。 |
| USDCNY 买价当作最新价、单位不明确 | 最新价改为源对应字段，保留买卖价及日期；明确 SHIBOR 变动为基点、南北向成交金额为百万港元/人民币、FDI 为千美元。 |
| 沪深宽度声称完整 A 股 | 明确不含北交所，标记部分覆盖；涨跌比例分母不含单列停牌。 |
| 业绩预告分页重复并漏科目 | 增加稳定科目排序、身份/报告期/计数检查。4901 行原仅 4899 个唯一记录；复测为 4901 个，找回两项遗漏。 |
| SEC 三种财季请求返回相同 10-Q | 根据实际年度截止日推断财季并筛选，缺少锚点保留不确定性。苹果 Q1/Q2/Q3 分别对应 2025-12-27、2026-03-28、2026-06-27。 |
| 中文 PDF 非空乱码被当成解析成功 | 识别本次字体映射异常，尝试已有文本工具；失败标记 `partial/text_error`，保留 PDF。腾讯官方英文版本已成功读取。 |
| 沪市误调用深交所互动易 | 对未覆盖交易所明确返回 `unsupported_exchange`。上证替代路线实测为空，没有加入无法证明可用的备用。 |
| 回购实际金额冒充原计划区间 | 完成快照将实际 29.9993374957 亿元填入两端；原授权为 15–30 亿元。屏蔽已识别的被覆盖计划金额，保留实际支出并提示查原公告。 |

回购依据为[贵州茅台原公告](https://www.moutaichina.com/mtgf/articleFileDir/2025-12/29/fea1fecbb20e44448f7e99e7fa824907.pdf)；FDI 的千美元倍率及美元同比依据[商务部同期间披露](https://dcj.mofcom.gov.cn/article/xwfb/xwsjfzr/202306/20230603416692.shtml)。单位判定不能只信第三方接口表格，需核对原始数量级和官方数据。

## 独立数值对照

- 同花顺与 AKShare：茅台、平安银行，三表 × 年报/全部期间，共 12 组、36 个报表期间、276 项关键字段/币种对照，无差异。银行不适用项目保持空值；`total_debt` 是总负债，`holder_equity_total` 是总权益，不能当作有息债务或归母权益。
- 官方原文：64 项合并营收、利润、EPS、资产/负债/权益及现金流抽样无差异。覆盖[茅台年报](https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF)、[平安银行年报](https://static.cninfo.com.cn/finalpage/2026-03-21/1225022887.PDF)、[腾讯年报](https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf)及[中报](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082500556.pdf)、[苹果 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm)及[最新 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm)。这不代表每个历史科目都经过官方逐笔审计。
- 估值：茅台与平安银行 PE/PB 可与取得的 TTM 归母利润、普通股权益核算；银行 PB 要扣除永续债权益。苹果样本 PE 37.45 对应价格 326.57 / TTM 稀释 EPS 8.72。各源时点、EPS 或权益分母不同，不能只因两个 PE 不同就判错。
- 行情：不复权价量跨源、已完成周/月与日线聚合、指数股数和跨除息日复权共 1311 项算术比较，1308 项在声明精度内一致。苹果三个月的跨源成交量仍有低于 4 ppm 的微差，保留来源修订口径未证实说明；没有为使测试通过而静默放宽阈值。
- 复权：90 个交易日覆盖茅台 2026-06-26 除息，同花顺前复权扣减每股现金 28.02423 元，新浪为比例法。两者都对应源原生序列，绝对复权价不可直接混用；后复权全部历史起算基准未重新构建。
- 事件：交易日历与交易所 2026 年安排核对；成分/权重核对数量、代码、时点及总权重；业绩预告/快报核对全表映射并抽样对公告原文，保留追溯调整及公告约数精度。

## 仍有的限制

| 类别 | 本次结论 |
|---|---|
| 不可用来源 | 23 个指定路径用例未取到可核验数据：15 个东财行情/K 线分支、6 个金十预期分支、1 个股东减持及1 个美股宽度。不能由此推断其他来源同样失败。 |
| 美股周 K / 部分复权 | 本轮苹果周 K 及部分东财分支失败；日线新浪可用，月线在直连复测恢复。不同频率必须分别读实际结果，不按其中一个成功泛化。 |
| 陈旧数据 | FDI 当月有效值止于 2023-05，社融止于 2026-04，部分金十预期止于 2025；美国 GDP 源止于 2026 Q1，而 [BEA 已发布 Q2](https://www.bea.gov/news/2026/gdp-second-estimate-and-corporate-profits-2nd-quarter-2026)。不得称为当前值。 |
| 未确认金额 | `cn_macro_series(export_yoy/import_yoy)` 的主要同比已核对，附带贸易金额的单位及历史修订口径仍未完全确认。AKShare 文档与自身样例数量级冲突，缺少对应修订版官方原值；不要据此计算贸易金额。 |
| 披露与覆盖缺口 | 北向当前买/卖/净额缺失，不能当作零流入；部分资金流入字段币种未证实；沪深宽度不含北交所；部分行情、分红、公司资料缺辅助字段。 |
| 大范围历史扫描 | 股东减持需扫描 226 页、112760 行，超出本次 120 秒分支预算；源可连接不代表查询能在合理时间内完成。 |
| 原文与日期 | 腾讯中文 PDF 仍不能可靠抽字，需看 PDF 或官方英文版；研报平台展示日期可能晚于 PDF 署名日期，不把二者等同。 |
| 平台与更新 | 本轮只实测本机环境；未重新执行 Windows/Linux 全平台安装。AKShare 更新后必须重查上游单位，尤其已有本地成交量修正的分支。 |

## 自动化验证与发布使用

最终候选离线回归 **607 通过、3 跳过**；跳过项仅为本机不能执行的 Windows 运行时解析器。仓库现有 **13 项公网 smoke 全部通过**。Ruff 检查、120 文件格式检查、skill 结构验证及公开内容扫描通过；公开扫描不包含 Git 历史审计。采集 CLI 已从仓库外的安装副本实际运行，未访问用户记录。

现有 smoke 全通过仍未发现上述多数语义错误，因此它适合检查通路，不能替代本轮字段验收。本轮没有提交、推送或发布。发布说明应明确这些来源与时效限制，不能宣传所有市场、频率、字段都稳定可用；研究结果还需按问题核对证据，取数测试不证明投资判断质量。

## 公开取数入口覆盖表

下面每个入口均对应实际执行用例；参数、来源、逐字段检查及限制见 JSON。带本地复用的入口使用真实已取得材料验证复用分支，未人为断网伪造上游失败。

| 公开入口 | 用例数 | 最终状态计数 |
|---|---:|---|
| `market.security_quote` | 7 | passed=4, degraded=1, unavailable=2 |
| `market.security_bars` | 50 | passed=33, unavailable=13, degraded=4 |
| `fundamentals.stock_info` | 4 | degraded=3, passed=1 |
| `fundamentals.financial_report` | 39 | passed=39 |
| `fundamentals.stock_materials` | 10 | degraded=8, unsupported=2 |
| `valuation.valuation_snapshot` | 6 | degraded=3, passed=3 |
| `valuation.valuation_history` | 4 | passed=2, unsupported=2 |
| `research.stock_reports` | 4 | degraded=1, passed=1, unsupported=2 |
| `research.download_pdf` | 8 | passed=8 |
| `research.list_local_reports` | 2 | passed=2 |
| `research.industry_reports` | 1 | passed=1 |
| `research.broker_reports` | 3 | passed=3 |
| `research.report_page_detail` | 6 | passed=6 |
| `research.report_abstract` | 2 | passed=2 |
| `research.eps_forecast` | 4 | passed=2, unsupported=2 |
| `research.consensus_forecast` | 4 | degraded=2, unsupported=2 |
| `research.consensus_revisions` | 4 | degraded=1, passed=1, unsupported=2 |
| `research.local_report_search` | 2 | passed=1, degraded=1 |
| `announcements.stock_announcements` | 4 | degraded=2, unsupported=2 |
| `announcements.periodic_reports` | 20 | degraded=15, passed=5 |
| `announcements.download_announcement_pdf` | 14 | degraded=4, passed=10 |
| `announcements.irm` | 4 | unsupported=3, degraded=1 |
| `announcements.lockup_expiry` | 4 | degraded=1, passed=1, unsupported=2 |
| `capital.dragon_tiger_board` | 1 | passed=1 |
| `capital.daily_dragon_tiger` | 1 | passed=1 |
| `capital.southbound_daily` | 2 | degraded=2 |
| `capital.northbound_daily` | 2 | degraded=2 |
| `capital.mutual_connect_daily` | 6 | degraded=6 |
| `capital.concept_blocks` | 1 | degraded=1 |
| `capital.stock_fund_flow_120d` | 2 | passed=2 |
| `capital.margin_trading` | 3 | degraded=3 |
| `capital.block_trade` | 2 | passed=2 |
| `capital.holder_num_change` | 1 | degraded=1 |
| `capital.dividend_history` | 4 | degraded=2, passed=2 |
| `capital.share_repurchases` | 1 | degraded=1 |
| `capital.shareholder_changes` | 3 | degraded=2, unavailable=1 |
| `capital.pledge_status` | 1 | passed=1 |
| `capital.corporate_actions` | 1 | degraded=1 |
| `capital.ownership_filings` | 2 | passed=2 |
| `capital.industry_comparison` | 1 | degraded=1 |
| `macro.lpr_history` | 1 | passed=1 |
| `macro.rates_snapshot` | 1 | passed=1 |
| `macro.bond_yields_cn_us` | 1 | passed=1 |
| `macro.cn_macro_series` | 20 | passed=15, degraded=3, unverified=2 |
| `macro.us_macro_series` | 14 | passed=12, degraded=2 |
| `macro.macro_series` | 2 | passed=2 |
| `macro.fx_usdcny` | 1 | passed=1 |
| `macro.commodities_spot` | 1 | passed=1 |
| `macro.index_board` | 1 | passed=1 |
| `macro.macro_snapshot` | 1 | degraded=1 |
| `macro.economic_calendar` | 1 | passed=1 |
| `macro.macro_surprises` | 16 | unavailable=6, degraded=10 |
| `feeds.stock_news` | 4 | passed=2, degraded=2 |
| `feeds.telegraph` | 1 | passed=1 |
| `feeds.global_news` | 1 | passed=1 |
| `events.trade_calendar` | 3 | passed=3 |
| `events.suspensions` | 5 | degraded=4, unsupported=1 |
| `events.earnings_calendar` | 4 | passed=3, unsupported=1 |
| `events.performance_updates` | 4 | passed=4 |
| `events.company_events` | 2 | degraded=1, passed=1 |
| `breadth.market_breadth` | 3 | degraded=1, unsupported=1, unavailable=1 |
| `breadth.index_constituents` | 2 | passed=2 |
