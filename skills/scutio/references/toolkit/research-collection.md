# 按问题采集材料

少量数据直接调用领域接口即可。需要一组材料时，统一使用当前技能的 `scripts/collectors/collect_research_base.py`，传入问题与明确的模块。一次调用只采集所选材料，记录各自的源时点和覆盖缺口。

```bash
"$SCUTIO_PYTHON" "$SCUTIO_SKILL_DIR/scripts/collectors/collect_research_base.py" hk00700 \
  --question "利润改善是否伴随现金回收？" \
  --modules income,cashflow --period annual -o /path/to/evidence.json
```

`SCUTIO_SKILL_DIR` 是当前 `SKILL.md` 所在目录。运行时见 [01-runtime](01-runtime.md)。`--depth light|full` 仅控制条数，默认 light；不会添加模块。`--period annual|all|quarter|cumulative` 遵循各市场财务接口范围；A/港股季度材料使用 all 后核对报告期，不把累计口径当单季。

## 模块

| 模块 | 内容与参数 |
|---|---|
| profile / quote | 公司档案 / 报价 |
| bars | 日线、不复权；light 80 条，full 160 条。其他周期/复权直接调用行情接口 |
| income / cashflow / balance | 对应报表；light 4 期，full 8 期 |
| filings | 定期报告列表；`--filing-kind annual|semi|q1|q3|all`，条数为 4/8 |
| reports / consensus / revisions | A 股研报列表 / 一致预期 / 同机构同财年 EPS 修订 |
| news | 相关新闻，正文与事实仍需核查；条数为 4/8 |
| valuation / valuation_history | 独立估值指标与报价 / A 股历史估值 |
| fund_flow / corporate_actions | A 股日频资金 / 公司行动 |
| breadth | 对应市场宽度；不支持的市场返回明确缺口 |
| peers | `--peers` 指定映射表，批量获取这些标的的报价 |
| macro:<series> | 命名宏观序列，如 `macro:cpi_yoy`；可选名见 `macro.list_macro_series()`，条数为 12/36 |
| rates / bonds / fx / commodities / calendar | 利率 / 中美国债 / 美元兑人民币 / 商品 / 当日经济日历 |

单一接口的更细选项仍使用公开领域 API，不必把所有参数重复加到采集 CLI。默认不会下载原文或计算财务同比；需要这些操作时，使用已取得的材料，核对单位、期间和口径后处理。

## 宏观与同行

纯宏观请求省略股票代码，只获取选择的序列：

```bash
"$SCUTIO_PYTHON" "$SCUTIO_SKILL_DIR/scripts/collectors/collect_research_base.py" \
  --question "通胀与汇率如何变化？" --modules macro:cpi_yoy,fx \
  -o /path/to/macro-evidence.json
```

同行映射用 JSON 数组，记录关系的依据；不自动寻找或猜测同行：

```json
[
  {"code":"hk00700", "relation":"同业", "basis":"已有材料指出的共同业务", "source_ref":"对应材料出处"}
]
```

```bash
"$SCUTIO_PYTHON" "$SCUTIO_SKILL_DIR/scripts/collectors/collect_research_base.py" hk09988 \
  --question "比较已核实的同业报价" --modules quote,peers \
  --peers /path/to/peers.json -o /path/to/comparison.json
```

映射需要完整代码、关系和依据，拒绝重复及目标自身。报价保留每个标的的源时点，不把不同交易时段的数值直接计算为同步收益。映射依据来自输入，不表示程序核实了商业关系。

## 结果与复用

结果只有一套结构：`modules` 存放各领域接口结果，`observations` 记录请求参数、采集时间及是否复用；`errors` 描述失败模块。`ok` 表示至少一个所选模块成功，`partial` 表示仍有失败、过期或部分结果。成功空列表与接口失败分开。没有报价不影响其他研究材料返回。

同次选择 `quote,valuation` 或 `reports,revisions` 时，先取得基础材料，再传给派生接口；基础材料失败也传递失败信封，不另行重复请求。只选派生模块时，可以复用已有的基础材料；没有可复用材料时由领域接口完成必要取数。

适用的旧材料可以通过 `--reuse /path/to/evidence.json` 显式复用。脚本验证采集 schema、证券身份、配置身份、模块参数及原采集时间；仅复用同口径成功模块。基础材料刷新后会重新生成依赖它的派生结果。材料保留原始时点；复用适用性仍由当前问题决定，不以本次执行时间称其为最新。

采集结果默认返回到当前对话的工具输出；只有指定 `-o` 时写入用户选择的文件，不约定默认研究产物目录。研究报告同样按用户要求导出。需要明确保存个人研究判断时使用 [记录功能](../journal.md)，不自动将日常讨论转为本地记忆。
