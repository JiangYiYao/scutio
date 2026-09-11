# 降级、源优先级与跨域 FAQ

**范围**：排障与维护向——主门面已封装的 fallback、健康冷却、覆盖缺口、跨域易混点。
**市场**：行情降级链覆盖 **A / 港 / 美**；多数监管/资金语义 **仅 A**（见下表与 index.md §2 速览）。
**日常取数**：直接调能力门面，读 `ok` / `source` / `error`；**不要**在业务里手写「源 A 失败再调源 B」。

---

## 门面内置降级

| 模式 | 入口 | 何时用 |
|------|------|--------|
| **路径内嵌** | `security_quote` / `security_bars`；部分 capital | 日常；调主 API 即可 |

---

## 行情默认链（`sources=None`）

按**每只票的市场**选链（混合批次按票分支）：

| 市场 | `security_quote` | `security_bars` |
|------|------------------|-----------------|
| A（免费） | tencent → sina | akshare_sina → akshare_tencent → akshare_eastmoney |
| 港 | tencent → sina → eastmoney | akshare_eastmoney → akshare_sina |
| 美 | tencent → sina → eastmoney | akshare_eastmoney → akshare_sina |

- 配置可用 Key 且为已确认的 A 股时，报价/日线优先 `hithink`；指数、ETF、港美及周月线跳过该能力。
- AKShare 是新浪/东财等源站的适配库，不是独立数据源。指数走 AKShare 腾讯/东财；周/月线走 AKShare 东财。
- `SCUTIO_DATA_MODE=public` 禁止任何有 Key 源请求，包括显式单源调试。配置和状态见 [可选数据服务](12-data-sources.md)。
- 默认 **不复权**（`adjust='none'`）。A 股前复权日线免费优先 AKShare 东财 → 新浪；后复权走 AKShare 新浪 → 东财。腾讯适配仅接受不复权日线。各源复权因子不同，读 `adjustment_basis`，不跨源拼接。
- 可用 `sources=('akshare_eastmoney',)` 等覆盖。
- 常量：`market.QUOTE_FALLBACK_BY_MARKET` / `BARS_FALLBACK_BY_MARKET`。

---

## 来源顺序与健康冷却

各门面使用固定来源顺序；`industry_comparison` 先东财、后新浪，`stock_info` 先完整档案、后可用的报价子集。降级结果保留 `partial` / `data_quality` 与缺失字段，不会因上次成功而优先返回较少数据。

请求层共用提供方、凭据及接口范围的失败冷却，遇到暂时不可用的来源可跳过该次尝试并由门面继续既有降级链。调度、预算与配置统一见 [运行时](01-runtime.md#批量调用)。自检也使用相同执行状态。

---

## 降级阶梯（排障顺序）

1. 检查代码格式与参数（港美须显式前缀）。
2. 东财 403 / 429 / 连接重置 → 停、退避；可 `SCUTIO_TRUST_ENV=0`。
3. 调大间隔或换网络。
4. 按已登记覆盖缺口选择公开原文，或用显式 `sources=` 排障。
5. 输出中写明源、时点、缺口 —— **不伪造**。

保留的直接东财适配器由 `em_get` 协调最多一次额外尝试，认证/权限错误不重试，限流等待遵守 `Retry-After`；HTTPAdapter 自身不重试。与 AKShare 共用[请求预算策略](12-data-sources.md#请求控制)，退避超过剩余时间时直接结束。每一次真实尝试都先经过共享提供方配额，并通过状态文件约束同机多进程。坏环境代理使用
`trust_env=False` 的直连 Session，确保 `ALL_PROXY` 不会泄漏到恢复请求。

---

## 跨域易混（索引）

| 易混 | 正确理解 | 详文 |
|------|----------|------|
| 港股通 vs 北向 | 港股通=**南向**；沪/深股通=**北向** | [`05-capital.md`](05-capital.md) |
| `000001` | 无前缀=平安银行；上证 `sh000001` | [`02-market.md`](02-market.md) |
| 空列表 | `ok=True, items=[]` 合法空；`ok=False` 才是失败 | index.md §3 |
| 港美三表 | 用 `financial_report('hk…'/'us…')`；深度尽调仍补 IR/SEC | [`03-fundamentals.md`](03-fundamentals.md) |
| 估值快照 vs 一致预期 | 快照=Financial API 或报价侧指标；一致预期走 `consensus_forecast`，原表才用 `eps_forecast`（04） | [`03`](03-fundamentals.md) / [`04`](04-research.md) |
| A 股历史估值 | `valuation_history` 东财单请求 → 百度 PE/PB 备用；主源成功不并调备用 | [`03`](03-fundamentals.md) |
| 预约披露日历 | AKShare 东财；失败明确缺口，沪深/京市分段 | [`02`](02-market.md) |
| A 股停复牌 | AKShare 东财 → AKShare 百度财经日历 | [`02`](02-market.md) |
| A 股市场宽度 | 乐咕全市场聚合（单请求）→ 新浪全量分页 | [`02`](02-market.md) |
| 中证指数成分 | 中证官网成分/权重 → 新浪成分（无权重，partial） | [`02`](02-market.md) |
| 标准化一致预期 | AKShare 同花顺原表 | [`04`](04-research.md) |
| 宏观惊喜 | 金十 actual/forecast → 命名序列 actual-only（surprise=None） | [`10`](10-macro.md) |
| 研报 PDF | HTTP 200 也可能是风控页；须 `%PDF` | [`04-research.md`](04-research.md) |
| 巨潮无公告 | 检查查询窗口、股票身份与公告类型 | [`07-announcements.md`](07-announcements.md) |

---

## 其它短答

- 腾讯乱码：GBK，不是「无行情」。
- `industry_comparison` 的 `bottom` 来自 AKShare 同一全行业表；降级新浪时**分类口径不同**。
- 通路自检在 **`tests/self_check.py`**（运维），不在 `scutio_data`；覆盖缺口见同文件 `COVERAGE_GAPS`。


主能力已由 AKShare 覆盖后不再保留第二套直接请求备用。已确认保留的字段或时效补充能力见数据源状态 direct_adapters；retained 表示保留该直接适配，不能声称已迁入标准来源。失败按来源/日期/字段记录，不把缺失补零。
