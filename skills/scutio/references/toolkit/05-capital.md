# 资金与互联互通（`capital`）

**范围**：北向/南向、个股资金、行业对比、概念板块、两融/大宗/股东/分红与龙虎榜。
**市场总览**：

| 子域 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| 互联互通资金 | ✓ | ~ | — | 北向=外资买 **A**；南向=内地买 **港**（资金流，不是港股个股池） |
| 个股资金/两融/板块/龙虎榜 | ✓ | — | — | A 股结构化字段；勿对 `hk*`/`us*` 当主路径 |

**门面**：`stock_fund_flow_120d` / `corporate_actions` / `dragon_tiger_board` 等。
**信封**：列表类多为 `result_list`（`items`）；组合类为 `result_ok`。

---

## 门面

### 互联互通

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `northbound_daily` | ✓ | — | — | 北向日频 |
| `southbound_daily` / `mutual_connect_daily` | ✓ | ~ | — | 南向/互联日频（南向语境关联港股） |

### 个股与结构（A）

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `stock_fund_flow_120d` | ✓ | — | — | 最多 120 条日频资金记录 |
| `industry_comparison(top_n=20)` | ✓ | — | — | A 股行业涨跌榜 |
| `concept_blocks(code)` | ✓ | — | — | 概念板块（A 码） |
| `margin_trading` / `block_trade` / `holder_num_change` / `dividend_history` | ✓ | — | — | 两融、大宗、股东、分红 |
| `share_repurchases` / `shareholder_changes` / `pledge_status` | ✓ | — | — | 回购、增减持、质押结构化记录 |
| `corporate_actions(code, preloaded=None)` | ✓ | — | — | 上述三项 + 分红组合；已取腿可复用 |
| `ownership_filings(code, page_size=50)` | — | — | ✓ | SEC 3/4/5、13D/G、144 及修订申报 |

`stock_fund_flow_120d` 通过 AKShare 获取日级资金流；失败返回明确缺口，金额单位为元。

`ownership_filings("usAAPL", page_size=50)` 返回 `items`，每行保留 `form/date/accession/file_url/ticker/cik`。13D/G 同时识别 `SCHEDULE 13D`、`SCHEDULE 13G`、历史 `SC 13D`、`SC 13G` 及各自 `/A` 修订表单；输出保留原始表单名。查询范围为 `SEC submissions.recent`，筛选后取最多 `page_size` 条，不代表全部历史。

---

## 解读

### 北向 vs 港股通（必读）

| 名称 | 方向 | 推荐 API |
|------|------|----------|
| 沪股通 / 深股通 | **北向**（外资买 A） | `northbound_daily` |
| 港股通 | **南向**（内地买港股） | `southbound_daily` |

- 日频 `NET_DEAL_AMT` 等可能为 `None` —— 保留字段，**勿当 0**；权威以交易所 / HKEX 为准。
- 本地 CSV 缓存是运行积累，不是上游历史全量。

### 单位

- 输出中部分金额字段已换算（如龙虎相关万字段）；日资金流为**元**，展示万元/亿元由调用方换算。
- 大宗 `premium_pct`：相对收盘价；收盘价缺失时为 `None`，不是 0。
- 分红 `bonus_rmb` 为每 10 股税前现金（元），`dividend_per_share` 为每股税前现金；`bonus_ratio`、`transfer_ratio` 为每 10 股送股、转增股数。

### 分红完整性

`dividend_history` 免费路径优先 AKShare 巨潮实施事件，覆盖常规与特别分红，按除息日合并同日分配，重复事件只计一次。`components` 保留分配方案及登记、支付日期，不另加常规分红汇总。

巨潮失败或方案解析不完整时，使用 AKShare 东财常规分红并明确 `partial=True`、`special_dividend_coverage.available=False`；不能据此断言没有特别分红。巨潮成功时 `after=None` 表示没有本地设置的近期窗口截断，不保证上游具有公司全部历史。`corporate_actions` 保留各分项完整性状态。


---

## 陷阱

- 股东户数、分红表 **不是** 实时资金信号。
- SEC `ownership_filings` 是申报列表，不是按个股聚合后的 13F 全机构持仓表。
- 回购、增减持、质押均通过 AKShare 获取；巨潮公告可作证据，但不冒充同构数值 backup。
- 回购完成记录可能把原计划金额上下限覆盖成实际花费；识别到此情况时返回 `plan_amount_low/high=None`、`plan_amount_available=False` 和 `partial=True`，实际回购金额仍保留。原授权范围需看方案公告。
- 只关心回购、增减持、质押或分红时直接取对应项；需要完整组合时再用 `corporate_actions`，已有腿传 `preloaded`，避免两边重复调用。完整组合可能包含全市场分页和多个质押统计日期，冷调用仍可能分钟级；并发不会减少所要求的历史覆盖。
- 两融、成交、股东、分红字段上游缺失时为 `None`；只有上游明确给零才是 0。
- `industry_comparison` 从 AKShare 全行业表取涨跌两端；切换新浪时**行业分类口径不同**，`constituent_count` 是公司家数，涨跌家数缺失为 `None`。

---

## 边界

- 港美**个股**行情/档案：`security_*` / `stock_info`，不是本模块资金表。
- 解禁 → [`07-announcements.md`](07-announcements.md)（仅 A）。


`dividend_history(..., sources=None)` 对有 Key 的 A 股优先 Financial API，失败时回退东财常规历史与特别分红校验。`bonus_rmb` 始终为每 10 股、`dividend_per_share` 为每股税前现金；不把两源同一事件相加。Financial API 不含登记/支付日及送股/转增拆分，相应字段为 `None`，带 `partial` 和覆盖说明。业务 `3002` 是数据未准备，不能解释成没有分红。

### AKShare 数据范围

- 日资金流最多取 120 条，实际不足通过 `available_count/partial` 标记。按日期升序返回；已删除新浪日级备用。
- 股东户数取历史明细，`avg_total_shares` 是户均总持股；原户均流通持股字段 `avg_shares=None`，不互相替代。
- 沪深港通日频撤销 AKShare 的缩放，保持原东财字段数值口径（`amount_basis=eastmoney_raw_fields`）；成交总额由买入加卖出得到，`quota_text/deal_num` 缺失显式标注。不要从南向合计的 AKShare 列名推断指数名称。
- 互联互通成交、净买额及累计净买额的 `units` 为南向 `million_hkd`（百万港元）、北向 `million_cny`（百万元人民币），不沿用个股资金流的元单位。`coverage.latest_trade_fields` 标明最新日期哪些成交字段存在；北向买卖额缺失时不能推断净流入或流出。`fund_inflow` 的币种未验证，保留 `upstream_unspecified`，不得与成交净买额混合。
- 回购首个全市场请求使用批量操作的来源预算（见 [请求控制](12-data-sources.md#请求控制)），成功快照缓存 1 小时供不同股票复用；`snapshot_retrieved_at` 保留实际取数时间，报价字段也受该快照时点约束。过期刷新失败会返回失败，不把旧快照当新数据。

### 两融、大宗、质押历史与增减持

`margin_trading`、`block_trade`、`pledge_status` 可传 `end_date="YYYY-MM-DD"`、`lookback_days=N`。默认截止昨日，分别在最近 90/365/730 天寻找最多 `page_size` 条。检查 `coverage.lookback_window/queried_partitions/failed_partitions/requested_count_met`；没有找到足够记录不等于没有更早活动。查询使用共享的[批量预算](12-data-sources.md#请求控制)，日期快照缓存 1 小时，`snapshot_time_range` 保留原取数时点。

两融按真实交易日取沪深京市场表：沪市 `rqye/rzrqye` 缺失，深北 `rzche/rqchl` 缺失，均为 `None`。`rqyl` 是融券余量（股），不能当作融券余额（元）。深北接口不返回日期，`date_basis=requested_date` 明确该限制。质押只查询源已发布的统计日期，单位不变。大宗行数达到 5000 时拆分日期段，同形交易行保留，不能随意去重。

增减持按增持、减持分别缓存，两个方向共用[批量操作预算](12-data-sources.md#请求控制)。检查 `coverage.completed_directions/missing_directions`；一类失败时不推断该方向没有事件。`trade_average_price=None`，`signed_shares_changed_wan` 由方向与绝对数量派生。记录可能是较早的历史事件，使用事件日/公告日描述，不把缓存取数日期当成发生日期。

## 龙虎榜（仅 A 股，按需）

`dragon_tiger_board(code, trade_date, look_back=30)` 必须传查询截止日 `trade_date="YYYY-MM-DD"`，回看天数由 `look_back` 指定。

```python
from scutio_data.capital import dragon_tiger_board, daily_dragon_tiger

board = dragon_tiger_board("600519", trade_date="2026-09-08", look_back=30)
daily = daily_dragon_tiger("2026-09-08")
```

个股返回 `records`、`seats`、`institution`，全市场返回 `stocks`。同一股票多个上榜原因有不同统计窗口，不相加、不压成一条。保留源席位名、机构标记与上榜日；`net_buy_wan` 等金额单位为万元。无上榜记录是合法空结果，席位失败或缺失必须读取 `partial/errors/coverage`。

龙虎榜、两融、大宗、质押、增减持、解禁按具体问题单独取数；常规快照不自动收集龙虎榜和两融。日频资金、股东与公司行动可解释交易结构或风险，不能单独证明经营变化。
