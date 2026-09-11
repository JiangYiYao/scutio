# 公告、定期报告原文、互动易与解禁（`announcements`）

**范围**：巨潮公告、**A/港/美定期报告原文**、互动易、限售解禁。
**市场**：

| 能力 | A | 港 | 美 |
|------|---|----|----|
| `stock_announcements` | ✓ | — | — |
| `periodic_reports` | ✓ 巨潮 | ✓ AKShare 巨潮 | ✓ SEC EDGAR |
| `download_announcement_pdf` | ✓ | ✓ | ✓（常为 HTML） |
| `irm`（仅深市） / `lockup_expiry` | ✓ | — | — |

**门面**：`stock_announcements` / `periodic_reports` / `download_announcement_pdf` / `extract_filing_text` / `irm` / `lockup_expiry`。
**信封**：列表 `result_list`；下载 `result_ok` / `result_err`。

与 **三表数字** 的分工：`financial_report`（03）给结构化科目；本模块给 **披露原文**（PDF/HTML）+ **旁路 txt**。
**有三表数字 ≠ 本地已下载年报/10-K**——要原文必须 `periodic_reports` → `download_announcement_pdf`（或失败后按 `fallback_hint` 自搜）。

---

## 门面

| 函数 | A | 港 | 美 | 说明 |
|------|---|----|----|------|
| `stock_announcements(code, page_size=30, page_num=1)` | ✓ | — | — | AKShare 巨潮近一年列表 |
| `periodic_reports(code, kind='annual', page_size=20)` | ✓ | ✓ | ✓ | 定期报告列表 |
| `download_announcement_pdf(item, extract_text=True, timeout=None, single_attempt=False)` | ✓ | ✓ | ✓ | 下载原文 + 默认写 `.txt`；可要求单次尝试 |
| `extract_filing_text(path)` | ✓ | ✓ | ✓ | 本地 PDF/HTML → 文本 |
| `irm`（仅深市） | ✓ | — | — | 互动易 |
| `lockup_expiry(code, trade_date, forward_days=90)` | ✓ | — | — | 按基准日分隔历史与未来解禁 |

```python
from scutio_data.announcements import periodic_reports, download_announcement_pdf

# A / 港 / 美
for code in ("600519", "hk00700", "usAAPL"):
    env = periodic_reports(code, kind="annual", page_size=3)
    item = env["items"][0]
    dl = download_announcement_pdf(item)
    # dl["path"]      → PDF 或 HTML
    # dl["text_path"] → 同目录 .txt（搜索/阅读优先用这个）
```

默认落盘（**按市场 + 个股**）：

```text
$SCUTIO_HOME/cache/documents/filings/{a|hk|us}/{code}/日期_…标题_身份摘要.pdf|.html|.txt
# 例：filings/a/600519/…  filings/hk/00700/…  filings/us/AAPL/…
```

---

## 解读

### 列表字段

| 字段 | 含义 |
|------|------|
| `title` / `date` / `kind` | 标题、披露日、`annual`/`semi`/`q1`/`q3` |
| `market` | `a` / `hk` / `us` |
| `pdf_url` / `file_url` | 附件或原文 URL（美股常只有 `file_url` 指向 HTML） |
| `file_format` | `pdf` / `html` |
| `announcement_id` | A=巨潮 id；港=`art_code`；美=accession |

### `kind` 映射

| kind | A（巨潮） | 港（MVP） | 美（SEC form） |
|------|-----------|-----------|----------------|
| `annual` | 年报 | 年報栏目 | 10-K / 20-F |
| `semi` | 半年报 | 中期/半年度報告 | 按实际报告截止日推断第二财季的 10-Q |
| `q1` / `q3` | 一季报/三季报 | 按标题识别第一/第三季度；仍需核对报告覆盖期 | 按实际报告截止日推断第一/第三财季的 10-Q |
| `all` | 四类合并；有完整中文报告时排除摘要/英文版 | 年报+中期+季度业绩等 | 10-K/Q + 20-F（**仅 SEC submissions.recent ~1000 条内**） |

美股 `date` 是提交日，`report_date` 是报告截止日。10-Q 的 `kind_basis=annual_report_date_interval` 表示按前一年度实际截止日推断财季，兼容 52/53 周财年；仍需看原文确认，不能按公历月份理解。缺少年度锚点或日期异常时，`all` 保留 `kind=quarter`；指定财季会排除无法判断的记录，并用 `partial/unclassified_quarters` 说明缺口。

### 下载与 txt

| 字段 | 含义 |
|------|------|
| `path` | 原文路径（`.pdf` 或 `.html`） |
| `text_path` | **旁路纯文本**（默认生成；Agent/搜索优先读这个） |
| `file_format` | `pdf` / `html` |
| `cached` | 命中已有合法原文 |
| `bytes` | 原文大小 |
| `text_error` / `partial` | 文本为空或识别到字体映射乱码时说明解析缺口；原文下载成功不代表正文可读 |

- 美股 10-K 主体多为 **iXBRL HTML**，不是单一 PDF —— 正常。
- 港股 PDF 从巨潮官方详情解析并验证身份；下载后用 `pypdf` 抽取文本。
- 读取 `text_path` 前先检查 `text_error`。已识别的编码乱码会尝试本机现有 `pdftotext`；仍不可读时返回 `partial=True` 和 `suspect_pdf_text_encoding`，保留原 PDF，改读原文或查找官方其它语言版本。质量检查只能识别部分异常，正文仍需人工核对。
- `extract_text=False` 可只下原文、不写 txt。
- 文件名附加市场、证券、文档 ID 与原文链接的身份摘要，同名公告和直接传入的不同 URL 不共用缓存。`filename` 只指定可读基名，仍附摘要和原文格式扩展名；携带正文的条目还按正文区分，旁路 `.txt` 与对应原文同名。
- `single_attempt=True` 时不切换第二种下载传输；`timeout` 控制该次传输。失败保留具体缺口，是否补查取决于原文对当前问题的重要性。

需要原文时，用 `periodic_reports` 定位，再 `download_announcement_pdf` 并读 **`text_path`**（或 `path`）。已有报表、链接或原文时从所需环节开始，不要求先取三表。

---

## 陷阱

### 巨潮 orgId（A）

- 证券组织映射由 AKShare 维护，Scutio 校验返回的股票代码与公告详情链接身份。
- `kind='all'` 分别查询四类报告；单类失败时保留其它成功结果并返回 `partial=True`、`errors`。存在完整中文报告时不返回摘要、英文版或取消类变体；只有变体可用时仍如实返回。

### 港美挂了 → Agent 自行检索（必读）

列表空、`ok=False`、或 `download_announcement_pdf` 失败时：

1. **禁止**编造年报/10-K 内容或假装「已下载」。
2. 原文是当前问题的关键证据时，走公开站检索并读取（浏览器/web_fetch 均可），标注 **URL + 时点**；仍不可得则说明缺口，不阻断其它可回答部分。
3. 信封字段 **`fallback_hint` / `note`** 会写明入口，照做即可。

| 市场 | 自行检索入口 |
|------|----------------|
| **美** | [SEC Company Search](https://www.sec.gov/edgar/searchedgar/companysearch.html) · [EDGAR full-text](https://efts.sec.gov/LATEST/search-index) · 公司 IR（查 10-K / 10-Q / 20-F） |
| **港** | [HKEXnews Title Search](https://www1.hkexnews.hk/search/titlesearch.xhtml)（股份代号）· 公司 IR（年报/中期报告 PDF） |
| **A** | [巨潮](https://www.cninfo.com.cn/) · 上交所/深交所公告 |

### 港股覆盖

- 通过 AKShare 巨潮在近 20 年窗口查询，以股票代码为关键词，再精确校验证券代码和链接。
- 按标题分类年报、中期和季度报告；关键词检索与窗口均有限，返回 `partial=True`，不能声称完整披露历史。
- 空列表或失败按上表查 HKEXnews / 公司 IR。PDF 由官方详情校验公告 ID 和五位港股代码后解析。

### 美股 SEC

- 列表仅扫 **`submissions.recent`（约近端 1000 条提交）**；8-K 极多的公司，更早 10-K 可能不在列表——信封 `coverage=sec_submissions_recent`，必要时按 `fallback_hint` 自搜。
- `periodic_reports(kind='all')` 不含 6-K：6-K 是外国发行人的通用当前报告，既可能附财务报表，也可能只是融资或其它事件，不能仅凭 form 类型当作定期报告。
- UA：环境变量 **`SCUTIO_SEC_UA`**（建议 `AppName contact@real-email`）；默认占位串，生产请改真实联系方式。
- 注意限速，勿批量狂拉。
- ticker→CIK 依赖 SEC 公开映射；找不到 CIK → `ok=False` → **SEC 网页按 ticker 搜**。
- HTML 抽 txt 会含 XBRL 噪音，仍可用于检索关键词。

### 下载

- `ok=False`（HTTP / 非 PDF / 无链接）—— **禁止**说成「无年报」；港美应 **自行检索下载**。
- 港股 PDF 下载失败时保留原文缺口，转查 HKEXnews/IR。

### 互动易 / 解禁

- 未回复 `answer` 可为 `None`。
- 解禁 `ok=True` 空列表合法；`ok=False` 禁止说「无解禁」。

---

## 边界

- 卖方研报 PDF → [`04-research.md`](04-research.md) `download_pdf`（`cache/documents/reports/{code}/`，与 `filings` 分目录）。
- 三表数字 → [`03-fundamentals.md`](03-fundamentals.md)（**数字成功不代表本模块已有原文文件**）。
- 新闻 / 快讯 → [`06-feeds.md`](06-feeds.md)（模块名 **`feeds`**，不是 `news`）。

### 互动易

`irm` 仅覆盖深交所互动易，通过 AKShare 获取；沪市、北交所返回 `unsupported_exchange`，不误用深市接口。校验股票身份后按提问时间倒序、本地分页。AKShare 当前最多抓取 10000 条记录，达到边界时返回部分覆盖；回答缺失保留 `None`，不把问答更新时间当成回答时间。A/港股披露列表走 AKShare；美股保留 SEC 官方查询，覆盖范围如下。

A 股普通公告查询近 365 天，定期报告查询近 20×365 天，`query_window` 显示精确边界；两者均本地排序、去重、分页，不能当作全部历史。公告列表 `pdf_url=None` 且 `file_resolution=cninfo_detail`，将整条 item 传给 `download_announcement_pdf`，会通过官方详情接口校验股票与公告 ID 后取得 PDF。附件路径不从公告日期猜测。

`lockup_expiry` 必须传 `trade_date="YYYY-MM-DD"` 作为查询基准日，例如 `lockup_expiry("600519", trade_date="2026-09-11", forward_days=365)`。通过 AKShare 解禁队列，历史只含 `trade_date` 之前，未来包含当日至指定天数。`shares/able_shares` 为股，`ratio` 为流通股比例（0–1）；返回达到源端 500 条上限时标记可能截断。

港股定期报告走 AKShare 巨潮：空 symbol + 股票代码关键词查询，再精确校验证券代码/链接；近 20 年有限窗口，按标题分类，partial=True。PDF 用官方详情验证公告 ID 和五位港股代码后解析；不再请求东财港股公告列表。
