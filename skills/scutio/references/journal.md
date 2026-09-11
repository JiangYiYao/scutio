# 研究与决策记录

只在用户明确要求记录、更新、复盘研究判断或个人决定，或要求结合既有记录讨论时读取。研究记录不要求交易决定、股票代码、持仓或退出条件。

## 对话方式

用户用一句自然语言即可开始。先保存 `DRAFT`，复述已知事实；只有缺少影响后续理解的关键内容时才追问一项。已知内容不重复询问，可公开查询的事实不要求用户手填。用户说“先这样记”时保留当前草稿，不强迫补齐。

区分四类来源：

- `user_explicit`：用户明确陈述；
- `conversation_context`：当前上下文已有事实；
- `external_source`：带来源与时点的公开事实；
- `assistant_inference`：AI 推断，默认未确认。

程序只保存和校验结构，不判断“证据够不够”“目标是否合理”或“应该买卖”。退出条件缺失、目标变化或杠杆等问题可以在对话中指出，但不能作为拒绝保存或激活的代码门禁。不得自动套用 10% 目标区、50%/100% 浮盈等通用阈值。

## 本地存储

默认根目录为 `$SCUTIO_HOME/journal`；未设置时是 `~/.scutio/journal`，也可通过 `--root` 指定其他记录根。内容是本地明文。每条记录包含：

```text
<decision_id>/
|-- record.json     # 当前投影与追加事件；唯一事实源
`-- memo.md         # 可由 record.json 重新生成的人类可读投影
```

状态只有 `DRAFT | ACTIVE | CLOSED`。修订是事件；待复核事项记录在 details 中，不触发自动执行或状态变更。

证券代码沿用取数层格式，支持 `usBRK.B` 等类别股代码；证券代码与记录 ID 分别校验，自动生成的 `decision_id` 把点转换为下划线，手工 ID 仍只允许字母、数字、下划线和连字符。记录保留传入的证券代码，不自动改写既有记录。

研究记录和决策记录统一放在 `journal/<decision_id>/`，通过 `record_kind` 区分；研究记录不要求证券代码或交易决定。目录 ID 默认包含日期，也可由调用者指定。记录使用 schema 2.1；创建事件保存初始依据快照，追加事件保留每次变化。原话、初始快照与历史事件不能通过 patch 改写；当时依据缺失时应说明缺口，不从当前认识反向补造。

## 脚本

使用当前 skill 的 `scripts/journal.py`。脚本接受 JSON 输入文件，避免把用户原话直接拼进 shell 命令。

先解析运行时，不假设固定安装目录：

```bash
SCUTIO_SKILL_DIR="<当前 SKILL.md 所在目录的绝对路径>"
SCUTIO_HOME="${SCUTIO_HOME:-$HOME/.scutio}"
JOURNAL_PYTHON="${SCUTIO_PYTHON:-$SCUTIO_HOME/.venv/bin/python}"
```

解释器不存在时报告安装缺口，不改系统包。

创建：

```bash
"$JOURNAL_PYTHON" "$SCUTIO_SKILL_DIR/scripts/journal.py" \
  --root "$SCUTIO_HOME/journal" create --input /path/to/create.json
```

最小创建输入：

```json
{
  "instrument": {"code": "hk09992", "name": "泡泡玛特", "market": "HK"},
  "raw_user_note": "用户原话",
  "as_of": "2026-09-03",
  "details": {}
}
```

只记录研究判断时使用以下结构，`instrument` 可省略；有明确证券关联时也可提供真实 instrument：

```json
{
  "record_kind": "research",
  "subject": "制造业成本改善的持续性",
  "raw_user_note": "先保存这次研究，之后看调价有没有抵消成本改善。",
  "details": {
    "question": "成本改善能否保留？",
    "current_view": {"text": "目前证据不足以判断持续时间", "source": "assistant_inference", "confirmed": false},
    "next_check": "采购安排与产品调价"
  }
}
```

字段按已有内容填写，不要求复制上述完整模板。AI 提出的解释与用户明确决定分别标记，不能把“帮我保存”解释为同意所有推断。更新可以使用 `research_reviewed` 等事件名，在 note 中说明旧判断、出现的新信息与变化原因；details 保存当前认识，source_reports 引用研究材料。

`next_check`、`next_review` 保存用户希望下次检查的事项或节点，仅作为记录内容。用户再次要求复核时，通过 show 读取记录后继续研究。

追加或修订：

```bash
"$JOURNAL_PYTHON" "$SCUTIO_SKILL_DIR/scripts/journal.py" \
  --root "$SCUTIO_HOME/journal" append \
  --record-dir /absolute/record/dir --event /path/to/event.json
```

事件输入：

```json
{
  "type": "decision_updated",
  "note": "用户确认的新信息",
  "source": "user_explicit",
  "confirmed": true,
  "patch": {"state": "ACTIVE", "details": {"next_review": "下一财报"}}
}
```

`patch` 更新当前投影，完整事件同时追加到 `record.json.events`，二者一次原子写入。同一记录的创建、追加及 memo 重建由实际记录根下 `.locks/` 的跨进程锁串行执行；不同 `SCUTIO_HOME` 访问同一记录根时也共用这些锁。锁保留在记录根是为保证共同写入位置，不属于记录正文，不要在任务运行时删除。等待超过 30 秒返回忙碌错误，不静默覆盖并发更新。`decision_id`、创建时间、事件历史和标的代码不可通过 patch 改写。

查看与列出：

```bash
"$JOURNAL_PYTHON" "$SCUTIO_SKILL_DIR/scripts/journal.py" \
  --root "$SCUTIO_HOME/journal" show --record-dir /absolute/record/dir
"$JOURNAL_PYTHON" "$SCUTIO_SKILL_DIR/scripts/journal.py" \
  --root "$SCUTIO_HOME/journal" list
```

若提示存在旧目录，先停止访问该记录根的任务，再按 [本地存储维护](toolkit/01-runtime.md#本地存储维护) 迁移。自定义记录根需向 `local_storage.py migrate` 传入 `--journal-root <原来的根目录>`；仅预览后加 `--apply` 执行，记录正文与历史保持不变。

首次写入前告诉用户实际目录和明文存储。当前环境无权写入时申请权限后继续同一路径，不改写 `SCUTIO_HOME`。
