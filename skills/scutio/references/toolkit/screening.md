# 对明确样本做筛选

使用 `scripts/screen_records.py` 对已获得的 records 做确定性筛选，不负责建立全市场数据库，也不生成机会分数。

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
