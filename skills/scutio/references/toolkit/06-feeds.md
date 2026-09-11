# 新闻与快讯（`feeds`）

本模块提供媒体检索与中文快讯；正式披露见 [公告与原文](07-announcements.md)。新闻只是线索，关键经营事实应核对公司披露。

| 入口 | 覆盖 | 说明 |
|---|---|---|
| `stock_news(code, page_size=20, order='time', name=None, drop_noise=True)` | A 股为主，港美覆盖不保证 | 个股新闻，默认最新在前；可按 relevance 排序 |
| `telegraph(page_size=50)` | 跨市场中文流 | 财联社电报 |
| `global_news(page_size=50)` | 跨市场中文流 | 全球财经快讯 |

```python
from scutio_data.feeds import stock_news, telegraph, global_news

env = stock_news("600519", page_size=10, name="贵州茅台")
items = env["items"] if env["ok"] else []
```

返回 `ok/error/source/items`。个股新闻条目含 `title/content/time/source/url/relevance`；保留 URL 方便核实。`name` 可辅助识别相关稿件；`drop_noise` 过滤缺少目标身份的榜单式标题。

`code` 必须是证券代码，公司名放在可选 `name` 中，不能替代代码。尚未确认上市主体与代码时，先用宿主搜索核实，不给公司名猜配证券。

三条路径均经 AKShare 获取，源窗口分别为 10/20/200 条。请求超过窗口会披露 `partial`，不保证历史分页或全量覆盖。电报组合源日期与北京时间，按最新在前返回。

检查 `fetched_raw/after_filter/filtered_out/returned/truncated/empty_reason`：合法空结果可能是上游空或全部被过滤，不能直接说“今天没有新闻”；`ok=False` 表示取数失败。新闻相关分不代表事实可信度。
