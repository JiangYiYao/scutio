# 对明确样本做筛选

使用 `scripts/screen_records.py` 对已获得的 records 做确定性筛选，不负责建立全市场数据库，也不生成机会分数。

用户只要求筛选时按给定条件完成即可。用户要求寻找投资机会时，筛选是 [发现](../capabilities/discover.md) 的可选输入，命中后仍需核实经济机制与价格条件；研究对象范围及机会取舍由发现负责。

输入 JSON 必须写清 `universe`（实际样本来源与范围）、`as_of`（这些数据的时点）、`records` 和 `filters`。字段使用输入中真实名称与单位；允许相等标量或 `min / max / eq / contains` 条件。数值阈值用 JSON 数字，缺失或不可解析的必需数值不作为命中。

虚构输入示例：

```json
{
  "universe": "用户提供的两行示例，非全市场",
  "as_of": "2026-09-01",
  "records": [{"name": "示例甲", "pe_ttm": 15}, {"name": "示例乙", "pe_ttm": null}],
  "filters": {"pe_ttm": {"min": 0, "max": 20}},
  "sort_by": "pe_ttm",
  "descending": false
}
```

```bash
"$SCUTIO_PYTHON" "$SCUTIO_SKILL_DIR/scripts/screen_records.py" --input /path/to/sample.json
```

输出包括输入行数、匹配总数、展示数、条件不符数和缺少筛选字段的行数/位置。数量按输入行统计，来源可能含重复，不能自动称为独立股票数；没有查询过的公司也不计为不符合。

`sort_by` 只按指定数值字段排序，缺少排序值排在末尾；不指定则保留输入顺序。可用 `limit` 限制展示数，但必须保留匹配总数。解释结果时同时说明范围、条件和缺失项，不把样本排序转写成投资优先级。不同报告期、币种或口径的记录需要调用方先对齐，不可直接比较。

量化计算结果也可作为输入，例如 [研报与价格变化](04-research.md) 提供的 `forecast_price_changes`。保留原计算的不可比原因和覆盖状态；缺失、冲突或未计算的记录不能填成零再参与筛选。一次命中只说明所选样本满足条件，不能证明策略有效、历史当时可交易或存在超额收益。
