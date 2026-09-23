# 按特征筛选

用户指定条件时，按条件筛选即可；用户寻找投资机会时，先按[发现](../capabilities/discover.md)说明策略，再由 AI 将可比较的部分映射到字段、报告期和条件，无需用户先给阈值。例如当期盈利改善可用同期增长定位候选，但“持续改善”还需跨期材料核实，低 PE 也不能代替现金回报。筛选按所声明范围执行后再选重点研究对象。经营机制、前景与价格判断继续由研究完成；不能给成长或转型研究默认套上低 PE、已盈利等门槛，也不为调用工具把目录外的特征换成近似指标。

## 取得市场数据并筛选

```python
from scutio_data.screening import feature_catalog, screen_market

catalog = feature_catalog()  # 纯本地：支持字段、单位、依赖、计算口径与条件操作符。
result = screen_market(
    {"mcap_yi": {"min": 100, "max": 1000}, "revenue_growth_pct": {"gt": 10}},
    market="a", report_date="2026-06-30",
    fields=["name", "pe_ttm", "roe"],
    sort_by="revenue_growth_pct", descending=True, limit=20,
)
```

这是调用示例，不是推荐的选股策略。市值单位为亿元人民币，百分数字段 `10` 表示 10%。第一版在线筛选支持 A 股；港美全市场名单与特征尚未接通，不得把 A 股结果外推。未给 `codes` 或 `index_code` 时从沪深北交易所名册起步；`codes=["sh600519", "sz000001"]` 使用指定样本；`index_code="000300"` 使用当前指数成分，两者不能同时传。名册公开入口是 `universe.stock_universe(market="a")`。

`screen_market(filters, *, market="a", codes=None, index_code=None, report_date=None, fields=None, sort_by=None, descending=True, limit=50, max_workers=4, quote_max_age_days=7, on_progress=None, cancel_event=None)` 返回结果信封。条件或字段不支持时在取数前失败，不能用近似字段默默替换。

字段以 `feature_catalog()["items"]` 为唯一完整目录；目录之外的特征须先取得材料并计算，再用下方本地筛选。目录可扩展，不代表源能提供所有投资特征。

| 字段方向 | 当前例子与口径 |
|---|---|
| 证券范围 | `symbol/name/exchange/board/listed_date/listing_age_days`；指定代码不自动拥有名册中的名称或上市日期 |
| 行情与估值 | `price/change_pct/mcap_yi/float_mcap_yi/pe_ttm/pb/turnover_pct/amount/volume/vol_ratio/amplitude_pct`；`earnings_yield_pct=100/pe_ttm` |
| 报告期财务 | `revenue/net_profit/roe/gross_margin/eps/operating_cashflow_per_share/net_assets_per_share/industry`；净利润是归母净利润，ROE 是加权口径 |
| 派生财务 | `net_margin_pct` 为归母净利润/营业总收入；`revenue_growth_pct/net_profit_growth_pct` 用两年同一报告期计算，上一年基数必须为正 |
| 来源披露同比 | `revenue_yoy/net_profit_yoy` 保留来源数值，基数处理未获独立确认；不能与上述正基数计算视作同一指标 |

财务字段必须显式提供季末 `report_date`；一季、半年、前三季为累计值，年末为年度值，不是 TTM 或独立单季度。数据来自[财务横截面](03-fundamentals.md)，不会逐家公司拉三张报表。尚未披露、日期异常、身份冲突或缺少可比基数时保持未知，不补零、不自动改取上一期。资本开支、订单、业务转型等未进入目录的特征，仍需按候选获取财报/公告并核实；文本包含主题词不能代替经营判断。

## 覆盖、时点与执行

沪深北名册独立于报价取得，停牌与 ST 不因无成交自动消失。名册排除 B 股、基金、指数、退市证券和 CDR，并披露排除范围。AKShare 未暴露沪、北名单的完整分页验证信息，目前 `universe.is_complete=None` 表示完整性未知；深交所整份导出有单独来源完整性记录。一次成功获取或较大的股票数量不能把未知升级为完整。

`input_count` 按完整证券身份去重，`matched_count/excluded_count/missing_count` 分别是符合、不符合、尚不能判断，总和等于输入证券数。条件之间是 AND；任一硬条件已明确不符合时，可以停止补取其余字段。未知不能当作不符合。`limit` 只影响展示数，不能提前截断候选再计算条件。

`coverage` 区分名单完整性、条件可判断数量，以及各请求字段在尚未排除对象中的可用/缺失数和原因。筛选已经命中但排序或展示字段缺失时，`matched_count` 仍保留，`partial=True`、`requested_fields_complete=False`，不能声称排序覆盖完整。`missing_rows` 描述影响筛选判断的缺口，候选的 `field_status` 描述字段缺口。

每个候选保留 `field_sources`：来源、行情时点或报告期、抓取时间、单位及派生计算的输入证据。补字段不改写其他字段的来源。行情默认只使用带源时点且距本次日期不超过 `quote_max_age_days=7` 的值，长假或其他口径需要调用者明确调整；抓取时间不能替代行情时间。Financial API 快照缺少可核验行情时间和估值字段时，按所需字段批量补取免费报价，不把来源的响应时间当作行情时间。

当前名册和财务修订数据不支持历史时点选股，结果标记 `point_in_time_verified=False`。财务列表的“最新公告日期”实际是来源更新日期，原始披露时间未获确认。

执行复用[批量调用](01-runtime.md#批量调用)：报价按最多 100 只分批，每批遵守原有源时限、限流和回退；没有默认全任务总时限。`max_workers` 默认 4，源配额仍有效。`on_progress` 只报告阶段和完成进度；等 `run_state="finished"` 后才是本轮执行结束，`interrupted` 保留已完成结果及未完成缺口。不要把进度回调或一批成功当成全市场任务完成。上游请求继续使用既有缓存，不新增用户研究记忆或后台监控。

## 筛选已有材料

同一规则实现也供本地记录使用：

```python
from scutio_data.screening import screen_records

result = screen_records(
    [{"name": "示例甲", "pe_ttm": 15}, {"name": "示例乙", "pe_ttm": None}],
    {"pe_ttm": {"gt": 0, "max": 20}},
    universe="用户提供的两行示例，非全市场", as_of="2026-09-01",
    sort_by="pe_ttm", descending=False,
)
```

这里不联网、不限制为内置目录字段，也不自动对齐币种或报告期。调用方须提供可比较的材料、字段和单位。纯本地输出按输入行计数，不自动把多行资料当作独立证券。

条件支持相等标量，或 `min/max/gt/lt/eq/contains/in` 对象。`min/max` 包含边界，`gt/lt` 不包含边界，`contains` 不区分大小写，`in` 接受非空标量数组。数值阈值用有限 JSON 数字，布尔值与不可解析数值不参加数值比较；百分号与逗号只做格式清理，不换算单位。缺失排序值放在末尾，不指定排序则保留输入顺序。

已有 JSON 输入也可运行唯一的命令行包装：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_SKILL_DIR/scripts/screen_records.py" --input /path/to/sample.json
```

输入对象包含 `records/filters/universe/as_of` 及可选排序、展示参数，与 `screen_records` 参数一致。[研报与价格变化](04-research.md)等计算结果可作为输入，须保留不可比原因和覆盖状态。条件命中、排序或一次全市场执行，均不证明策略有效或存在超额收益。
