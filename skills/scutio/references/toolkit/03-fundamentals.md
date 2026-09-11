# 基本面、个股资料与估值（`fundamentals` + `valuation`）

**范围**：轻量档案、三表、个股资料长文；报价侧估值快照、A 股历史估值与纯公式。
**市场总览**：**档案 / 三表 / 估值快照 A✓ 港✓ 美✓**（港美三表须显式代码）；**历史估值与资料长文仅 A**。
**代码模块**：档案/三表/长文在 `fundamentals`；估值快照与公式在 `valuation`（薄模块，文档合在本页）。  
**一致预期不在本页** → `research.consensus_forecast`（标准表）/ `eps_forecast`（同花顺原表；仅 A，见 [`04-research.md`](04-research.md)）。

---

## 门面

### 档案 / 三表 / 资料长文（`fundamentals`）

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `stock_info(code)` | ✓ | ✓ | ✓ | **结构化轻量档案**：名、行业、股本、市值、上市日、价 |
| `financial_report(code, report_type='lrb', num=8, period='annual')` | ✓ | ✓ | ✓ | **三表** `result_list`；`items` 为宽表行（科目×报告期） |
| `stock_materials(code, name=None)` | ✓ | — | — | **资料长文信封**：目录在 `items`，正文在 `text` |

```python
from scutio_data.fundamentals import stock_info, financial_report, stock_materials
```

| 别混 | |
|------|--|
| `stock_info` | 卡片字段，可直接引用数字 |
| `stock_materials` | 叙述性文本，不是档案字段表 |
| `financial_report` | 可计算的报表科目 |

- 港/美代码须显式：`hk00700` / `usAAPL`。
- 本页公司档案、三表、资料长文与历史估值只接受公司证券；A 股指数、ETF 或交易所不匹配的代码在取数前返回 `unsupported_asset`。指数和基金的报价、K 线使用行情入口。
- `stock_materials` 对港美返回 `ok=False, error_code="unsupported_market"`，不会抛裸异常。

### 估值（`valuation`）

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `valuation_snapshot(code, quote_env=None)` | ✓ | ✓ | ✓ | 现价/市值/PE-TTM/PB 等；已有报价时传 `quote_env` 复用 |
| `valuation_history(code, include_series=True)` | ✓ | — | — | PE/PB/PS/PCF 历史序列及 3年/5年/可得全期分位；东财失败才回退百度 |
| `forward_pe` / `calc_peg` / `pe_digestion` | ✓* | ✓* | ✓* | *纯公式；EPS/CAGR 由调用方自备 |

```python
from scutio_data.valuation import valuation_snapshot, valuation_history, forward_pe, calc_peg
from scutio_data.research import consensus_forecast  # 标准化一致预期
```

`valuation_snapshot` **不是**完整估值模型，也**不是**一致预期表。
调用方已执行 `security_quote` 时必须把信封传给 `quote_env`，不要再发一次报价请求。

`valuation_history` 默认在 `items` 返回按旧到新排列的原始日序列，并在 `windows` 给出 3年、5年和数据源可得全期摘要。东财单次最多取 5000 条日记录；只需摘要时传 `include_series=False`，只保留摘要。分位仅统计有限正值；当前值必须与同一历史源的序列比较，不能把另一报价源的 PE/PB 混入分位。

---

## 解读

### 三表 `report_type`

| 正式码 | 别名（模块会映射） | 含义 |
|--------|-------------------|------|
| `lrb` | `利润表` / `profit` / `综合损益表` | 利润表 |
| `llb` | `现金流量表` / `cash` | 现金流量表 |
| `fzb` | `zcfzb` / `资产负债表` / `balance` | 资产负债表 |

返回统一为 **每期一条** 宽表，至少含 `报告期`。  
`fzb` 是资产负债表正式码；`zcfzb` 等别名会映射到 `fzb`。

A 股完整报表使用 AKShare 东财原生字段 ID。每行保留全部源字段，`_line_items` 提供 `field_id`、`key`、`value`、`yoy`、`yoy_unit`，空值保留为 `None`。例如银行净利息收入、利息收入与财务费用下的利息收入各自保留，不合并同名科目，也不生成源表没有的中文标题或层级。

未知 `report_type` / `period` 会返回 `ok=False`，不会静默换成利润表或年报。

### `period`

| 值 | A | 港 | 美 |
|----|---|----|----|
| `annual`（默认） | 年报 | 年报 | 年报 |
| `all` / `报告期` | 全部报告期 | 全部报告期 | 按年报处理 |
| `quarter` | — | — | 单季 |
| `cumulative` | — | — | 累计季报 |

美股资产负债表是时点表：`period=quarter` 会按实际 `REPORT_DATE` 取最近披露点，并纳入上游标为 Q6/Q9/FY 的半年、九个月和年末时点；不会退化成历年 Q1 对比。

`num` 按所选报告类型计数；A 股 `annual, num=8` 请求最近 8 个年报。信封含 `requested_count`、`returned_count`；上游可得期数不足时 `partial=True` 并给出 `warning`。上游错误响应返回 `ok=False`，不能视为成功的空报表。

### 资料长文 `stock_materials`

- `stock_materials(code)` → `result_list`，分类目录在 `items`。
- `stock_materials(code, "公司概况")` → `result_ok`，正文在 `text`；匹配失败返回 `ok=False`。
- 长文输出时优先最新段并注明截断。

### `stock_info`

- 轻量档案；失败时内部可能换备用路径（看 `source` 即可）。
- 腾讯档案备胎同样标记 `data_quality=partial_fallback`；缺失股本、市值、PE/PB 等返回 `None`，不是 `0`。
- 完整三表用 `financial_report`，不要从档案里硬凑科目。

### `valuation_snapshot` 字段

成功时常见：

| 字段 | 含义 |
|------|------|
| `source` / `quote_source` | 实际报价源 |
| `name` / `price` / `mcap_yi` / `pe_ttm` / `pb` | 行情侧估值相关 |
| `change_pct` / `last_close` / `currency` | 有则填 |

**没有** `eps_cur` / `pe_fwd` / `peg` / `eps_error`。  
要前向 PE：先 `consensus_forecast`，再 `forward_pe(price, eps)`。

---

## 陷阱

- 报价 PE/PB 与三表报告期 **不是同一时点**。
- 不同来源的 PB 可能因净资产口径或更新时点不同而冲突；保留来源，不混算历史分位。
- 空同比：**不要**造空键或当 0。
- 港美三表通过 AKShare 东财取数，保留其科目字段；分部/调整项/附注仍靠 IR / 年报。美股资产负债表 `quarter` 合并单季、累计季和年度的时点，避免遗漏半年、前三季度及年末。
- 估值报价失败：`ok=False`，不抛异常。
- 公式 EPS≤0 或 CAGR 非正：返回 `inf`，避免误导性结论。
- 默认 `target_pe=30`（消化年数）是成长股常用锚点，**不是**全行业结论；PEG 仅筛选提示，不是评级。
- `calc_peg` / `pe_digestion` 的 `cagr` 输入小数：15% 写 `0.15`。例如 `calc_peg(20, 0.15)` 约为 `1.33`，`pe_digestion(60, 0.20, target_pe=30)` 约为 `3.80` 年。

---

## 边界

- 一致预期：`research.consensus_forecast`（A；AKShare 同花顺）；估值快照不含预期。
- **年报/季报/10-K 原文**（叙事与附注）→ [`07-announcements.md`](07-announcements.md) `periodic_reports` + `download_announcement_pdf`，不是本页三表。  
  **`financial_report` 有数字 ≠ 本地已有年报 PDF/HTML**；要原文必须再调 07（或按 `fallback_hint` 自搜）。
- 财务快照 **不是**公开门面，仅作 `stock_info` 内部降级。
- 动态源优先级见 [`11-fallback.md`](11-fallback.md)。

### 示例

```python
from scutio_data.core import envelope_items

info = stock_info("hk00700")                         # 档案卡片信封
hk_pl = envelope_items(financial_report("hk00700", "lrb", num=4))  # 三表行
us_bs = envelope_items(financial_report("usTSLA", "fzb"))
cats = envelope_items(stock_materials("600519"))      # 资料目录（仅 A）
text = stock_materials("600519", "公司概况").get("text")  # 资料正文
snap = valuation_snapshot("600519")                  # 报价侧 PE/PB 信封
history = valuation_history("600519", include_series=False)  # 紧凑历史分位
```


## 可选 Financial API

`financial_report(..., detail='full')` 默认保留完整源科目（AKShare 东财），不会因配置 Key 而缩减字段。
`detail='summary'` 在 A 股且有 Key 时请求 Financial API 摘要，返回 `field_schema='hithink'`、金额单位及累计口径。无 Key 或摘要失败时返回 AKShare 东财完整报表，显式标记 `detail='full', requested_detail='summary', field_schema='source_native'`；调用方必须读字段视图，不能按同名英文猜测跨源科目等价。银行现金分配科目尤其不可直接替换。`total_debt` 是源总负债（对应 `TOTAL_LIABILITIES`），不是有息债务；`holder_equity_total` 是含少数股东权益的总权益，不是归母权益。报告期以业务日期为准，源时间戳以 UTC 显示为前日 16:00 时需按源东八区期末理解。

港美完整表保留源科目名，源未提供币种时不猜填，并标记 `partial=True, missing_fields=['currency']` 与 `warning`；金额比较前需查明报表币种，不能从上市市场推断。跨市场金额、现金流支出符号及收入分类需查原文：例如腾讯「营运收入」与「营业额」不同，Apple 购买固定资产为负现金流，不能与 A 股支付项目直接比较符号。

`valuation_snapshot(code, quote_env=...)` 复用传入报价，独立请求估值；`pe_mrq` 不是静态 PE，`pb` 的 Financial API 别名基准为 MRQ。价格和指标来源在 `field_sources` 中分别记录，源时间未知保持空。未提供市值等字段时返回 `missing_fields`。

`stock_materials` 支持巨潮的「公司概况」「主营业务」「经营范围」。其他分类明确返回不支持；公司简介不能替代最新年报原文。

完整三表由 AKShare 东财提供。A 股为源原生字段 ID（例如 INTEREST_NI、INTEREST_INCOME、FE_INTEREST_INCOME）而非新浪中文展示标题；完整行及 `_line_items` 保留所有科目、None 和同比（百分数），不得假设银行与普通企业字段完全相同。

### 估值复用的身份与时点

`valuation_snapshot(quote_env=...)` 只接受完整证券身份一致的报价；纯代码键也必须由行内 `symbol` 或 `exchange/code` 确认身份，不按代码后缀匹配。复用报价保留 `retrieved_at/data_as_of/time`；`computed_at` 单独表示本次派生时间，未知源时点保持空值。`input_quote_retrieved_at` 指向实际复用行的获取时间。混合 Financial API 指标或免费源补充 PE/PB 时，`field_timestamps` 分别记录价格、估值指标的原始时间，不能把新指标时间当作旧价格的时间。
