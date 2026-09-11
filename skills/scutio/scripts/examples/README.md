# scutio_data 可运行示例

示例通过公开领域入口取数，展示返回信封、覆盖范围和失败信息。数据源由配置和市场决定；有 Key 的能力匹配请求优先 Financial API，无 Key 时使用免费路径。配置方法见 [数据服务与配置](../../references/toolkit/12-data-sources.md)。

## 运行

在仓库根目录执行（Python 3.11+，先安装 `skills/scutio/requirements.txt`）：

```bash
export SCUTIO_PYTHON="${SCUTIO_PYTHON:-$HOME/.scutio/.venv/bin/python}"
"$SCUTIO_PYTHON" skills/scutio/scripts/examples/01_quote_and_bars.py
"$SCUTIO_PYTHON" skills/scutio/scripts/examples/04_dragon_tiger.py 2026-09-08
```

安装后的 skill 可直接把脚本路径换成 `$SCUTIO_TOOLKIT_SCRIPTS/examples/01_quote_and_bars.py`。脚本会从自身位置找到数据包，无需额外设置 `PYTHONPATH` 或 `SCUTIO_SKILLS_DIR`。依赖含固定版本 AKShare；不要仅安装 requests/pandas 后运行。

示例会访问网络，可能写入取数缓存。需要强制只用免费数据时设置 `SCUTIO_DATA_MODE=public`。不要在脚本中写 Key。

## 清单

| 脚本 | 演示 |
|------|------|
| `01_quote_and_bars.py` | `security_quote` / `security_bars`，区分指数与同号股票 |
| `02_valuation_snapshot.py` | 报价侧估值快照与公式；一致预期见 research |
| `03_reports_local.py` | 主题研报检索、解包与去重 |
| `04_dragon_tiger.py` | 指定日期的龙虎榜记录、覆盖与失败信息 |
| `06_macro_snapshot.py` | 宏观快照与 CPI 序列，披露 partial/errors |
| `07_hk_us_quote_bars.py` | 港美报价、日线、资料，使用显式市场代码 |

先检查 `ok`，再读取 `quotes`、`bars`、`items` 或 `contracts`。`ok=True` 仍可能 `partial=True`；读取 `warning/errors`、覆盖元信息和源数据日期后再使用。`None` 不等于零，合法空结果不等于请求失败。示例退出码仅表示演示请求是否取得所需结果，不保证字段完整或数据实时。

自定义多个独立请求时，直接复用 [运行时批量调用](../../references/toolkit/01-runtime.md#批量调用)。

接口细节见 [调用索引](../../references/toolkit/index.md)。开发者离线验证见仓库 `tests/README.md`；示例回归使用 mock，不需要 Key 或真实网络。
