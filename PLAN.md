# PLAN v3.0 — AstrBot 微信长期记忆：受控 Dreaming / 增量画像 / 低成本上下文

> 仓库：[`abincn/astrbot_plugin_wechat_memory`](https://github.com/abincn/astrbot_plugin_wechat_memory)  
> 制定日期：2026-11-03  
> 状态：**源码调研已完成；本文件是 v0.2.0 开发计划，不代表已经实现或部署。**  
> 基线：现有 v0.1.0（`8a716750c5e7559419a1e15a25af0bd8be41d78e`）。

## 0. 目标、边界和原则

### 产品目标

做一个**独立安装的 AstrBot 原生插件**，从 `abincn/astrbot_plugin_wechat_hook` 接收包括未 @机器人的普通微信群消息；将原始消息与长期记忆分开管理；利用受控的后台 Dreaming 将“新聊天 + 已有画像”综合成**少量有价值、可追溯的增量修订**；回答时按需检索，而不持续灌入大量 Token。

**核心定义：Dreaming = 定时/按需执行 Memory Evolution 的调度方式，而不是新的记忆类型。**

运行链：`PC WeChat Hook → astrbot_plugin_wechat_hook → AstrBot → astrbot_plugin_wechat_memory`。不依赖 Hermes、`astrbot_plugin_wechat_agents`、OneBot，不改动现有 Hook 的发送流程和 AstrBot 人格配置。尽量只用 Python 标准库、SQLite 和 AstrBot API。

### 不变规则

1. 原始聊天保留其证据价值；**完成提取不等于可以删除原文**。
2. 每天新增信息不等于“完整的新画像”；旧画像未提及的内容**不自动失效**。
3. 画像内容采用**精简自然语言**，身份/作用域/状态/时间/来源采用**结构化数据库字段**；LLM 只提交**结构化变更提案（JSON）**。
4. **生成提案 ≠ 获准写库**。权限、引用 ID、版本、容量、敏感性和来源由本地代码校验；冲突/删除采取更严格的审查。
5. 原始群数据和群内成员画像不跨群传播；本人创建或逐条批准的全局画像才可在授权目的群**仅为本人提问使用**。
6. 可用上下文长度（500K/1M）是能力上限，**不是默认输入预算**。低成本、少打扰、零变更均为正常结果。
7. 后台失败不能影响微信消息的正常处理；可审计、可恢复、幂等，不因单条超长内容堵住全群。
8. **未经明确要求，禁止编写任何测试代码（包括临时、内联测试）**；阶段验收采用源码静态检查、人工检查与真实环境手工验收。最小实现、最小修复、最小依赖，按功能提炼模块；本次只修改 PLAN。

---

## 1. 四个开源项目源码调研及取舍

以下引用固定在**2026-11-03 的仓库版本**，避免将随时变化的 `main`/`develop` 误当成确定实现。均不是 OpenAI 官方 ChatGPT Dreaming 源码。

| 项目 / 版本 | 源码证据与实际机制 | 借鉴 | 明确不照搬 |
|---|---|---|---|
| [openJiuwen Agent Memory](https://github.com/openJiuwen-ai/agent-memory/tree/48be7b315049e384eafe4d4a75ee2526396a1652) (`mem2.0`) | [`dynamic_evolver.py`](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/jiuwen_memory/construction/evolver_impl/dynamic_evolver.py) 执行 extract → consolidate（仅判断）→ reflect（默认 no-op）→ persist；[`dreaming.py`](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/jiuwen_memory/api/memory_api_impl/dreaming.py) 与 [`F04 设计`](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/docs/features/F04-dreaming.md) 将定时和演进模式解耦，按 scope 执行并在各次任务复查授权；[`F06 中期记忆`](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/docs/features/control/F06-middle-term-memory.md) 强调原文非破坏性归档。 | **调度与演进分层、决策与写入分离、作用域隔离、非破坏式归档**。 | 不移植其复杂的多后端/多注册表/分布式调度、逐条候选多轮 LLM 去重；小型 AstrBot 插件不需要 Redis/ES。它的 `reflect` 默认是 no-op，不能宣传成已具备复杂“反思”能力。 |
| [JiuwenSwarm](https://github.com/openJiuwen-ai/jiuwenswarm/tree/341dfdddd4a6dd2b7f8f8fde5a27eabebe05a706) (`develop`) | [`sweeper.py`](https://github.com/openJiuwen-ai/jiuwenswarm/blob/341dfdddd4a6dd2b7f8f8fde5a27eabebe05a706/jiuwenswarm/agents/harness/common/memory/dreaming/sweeper.py) 有增量扫描、checkpoint、输入压缩、LLM 提取、promotion；限量最多 10 个 session/sweep、30K token/session、最多 5 条/session；[`Memory.md`](https://github.com/openJiuwen-ai/jiuwenswarm/blob/341dfdddd4a6dd2b7f8f8fde5a27eabebe05a706/docs/en/Memory.md) 区分日常笔记、用户画像和 Dreaming 文件。 | **增量水位、失败重试、闲时调度、单轮成本限制**。 | 微信群的消息流不等于 Agent session；不能按“至少四轮对话”“至少三个新 session”决定是否处理。其 checkpoint 在提取成功后、逐条 promotion 前更新的写序也不宜原样复用：落库失败不能导致永久漏处理。 |
| [OpenDream](https://github.com/pylit-ai/opendream/tree/1c3035464e54f59ecc11947f71336d0d2d7bb1c7) (`main`, Alpha) | [`consolidator.py`](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/opendream/consolidator.py) 保留来源事件、supersedes/conflicts、记忆生命周期；[`reconciliation.py`](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/opendream/reconciliation.py) 发现缺失证据和失效引用；[`retriever.py`](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/opendream/retriever.py) 追踪已选择与排除的记忆；[`ADR-008`](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/docs/adr/ADR-008-semantic-verifier-promotion-policy.md) 要求模型提案先过确定性验证，再按需进行语义验证。 | **提案→校验→发布、来源可追溯、检索审计、记忆伤害（memory-hurt）治理、可回滚**。 | 不移植其工作区 CLI、前端和多 Agent 事件总线；Alpha 项目自身提示缺乏广泛真实场景基准。以代码验证为准，不把所有文档能力认作已经生产成熟。 |
| [Hermes Dreaming](https://github.com/alejandroiglesias/hermes-dreaming/tree/a6bf5f6ca8a1295c671d439abb0cfa495a979a22) (`main`) | [`orchestration.py`](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/orchestration.py) 以提示词驱动 Light/Deep/REM；[`apply_memory_op.py`](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/tools/apply_memory_op.py) 处理 add/replace/remove、dry-run、备份、幂等、写入上限；[`scoring.py`](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/scoring.py) 设置信心阈值；允许**零变更**。 | **少量高价值变更、可预览、不可盲目覆盖、有限成本、每个字符都要有未来价值**。 | 不移植 Hermes 文件长度限制、session 生命周期、代理工具链和固定的 0.88/0.80 等分数阈值；LLM 自评置信度未校准，不能作为可靠安全证明。“三阶段”是逻辑步骤，**不要求三次 LLM 调用**。 |

**综合判断**：采用 openJiuwen 的分层、JiuwenSwarm 的调度、OpenDream 的证据/审核、Hermes 的少量高质量修改；保持本插件单进程 SQLite 最小形态。第一版 Dreaming 可以**单次 LLM 生成候选 Patch + 规则校验落库**，在复杂冲突时才可选二次 LLM/人工复核。

### 参考代码使用方式

优先参考思想和接口形状，自主实现最小代码；不大规模复制其他仓库源码。若以后移植具体实现，核对其许可证、NOTICE、文件头版权与传递性依赖要求；调研不等于引入依赖。

---

## 2. 当前 v0.1.0 的真实状态与待修复缺口

已存在：`main.py` 的原生群普通消息事件与 `on_llm_request`、`storage.py` SQLite 幂等队列和 scoped memories、`scheduler.py` 定时提取、`extraction.py` JSON 候选、`retrieval.py` 轻量词面召回、`panel.py`/`views/memory`、用户私聊全局画像及授权。

但不应把它当作新版 Dreaming：

1. `pending` 是处理队列，不是永久历史；`processed_retention_days=30` 会清除已处理项；普通单条超过 1600 字符会被跳过，尚无自动补录路径。
2. `extraction.py` 仅看**新批次消息**，未读取已有群/成员画像作为演进上下文；默认最多 8 个候选。
3. `storage.py` 基于 `fingerprint` 和 `topic_key` 比较；“AutoForm + TriboForm”可能被误标冲突，无法理解同义、补充、时间、否定、撤销。
4. 默认 20 条触发、每批 50 条/6000 字符、每日 12 次调用，并非“低成本夜间批量综合”；只限制调用数，不具有精确 Token/金额配额。
5. `retrieval.py` 最多 6 条 / 2400 字符，未按问题检索**原始群聊历史**，也没有检索决策日志。
6. 全局画像当前仅本人私聊创建，必须保留此安全边界；不得为了 Dreaming 自动将群内画像升级为跨群画像。
7. 尚无真实 AstrBot + Hook + Provider + WebUI 运行验收，不能把语法检查当成产品验证。

新版采用**增量替换，不推倒重写**：尽量复用现有身份、权限、SQLite、后台界面和事件生命周期，逐模块升级。

---

## 3. 目标架构

```text
微信 Hook 普通群消息（不需要 @）
    │
    ▼
AstrBot 原生事件监听 → 身份/白名单/文本元数据校验
    │
    ▼
[Raw History Store] 追加式消息档案、消息引用、时间戳、去重
    │                       └── 原始消息不是待处理队列
    ├─────────────────────────────────┐
    ▼                                 ▼
[Dreaming Scheduler]                 [Retrieval Planner]
增量水位 / 夜间/手动/显式记忆         当前群、提问者、话题、时间
预算、限量、重试                     数据库词法索引 + 来源追溯
    │                                 │
    ▼                                 ▼
[Context Selector]                   精简 Working Snapshot
本轮新增原文 + 已有相关画像           + 按需 Evidence 原文
    │                                 │
    ▼                                 ▼
[LLM Memory Evolver]                 AstrBot on_llm_request
返回 JSON 变更提案                    （本次临时注入、限 Token）
    │
    ▼
[Policy + Validation + Review]
作用域 / 权限 / 来源 / 版本 / 时间 / 不确定性
    │
    ▼
[Atomic Commit + Audit]
Memory Records / Revisions / Sources / Working Snapshots
```

### 三层数据的定义

- **Raw History（证据层）**：可信 Hook/AstrBot 实际接收的原始消息文本、发送者、消息 ID、时间和引用关系；只读检索，不因提取后删除。媒体保留已知元数据，**未理解的媒体不生成事实**。支持显式保留期限和清除政策。
- **Evidence Memory（事实层）**：最小独立自然语言事实/决策、归属、来源、有效期、状态、版本。一个成员可以有多条事实，但**不允许无界累积**。
- **Working Snapshot（当前画像/群快照）**：从有效记忆派生的简明自然语言表示（群公共快照、每成员当前画像），供回答和下次 Dreaming 使用。快照是缓存/投影，**不是唯一真源**；可根据 Memory Records 重建。

### 语义与结构之间的边界

自然语言正文承载复杂事实；固定结构只负责 `id / scope / subject_id / category / status / revision / valid_from / valid_to / source / timestamp` 等元数据。分类使用少量宽类（`role, expertise, preference, project, decision, other`），不把所有职业、工具、技能写成强制的 `work.role/tools.uses` schema。需要做精确查询的少量属性可日后增加受控注册表。

**JSON 只作为 LLM 与程序之间的提案协议**；不把 LLM 输出的“normalized_value”当作不可质疑的事实。

---

## 4. 一次 Dreaming 的完整流程（重点）

### 4.1 选择输入（本地操作，0 LLM Token）

1. 按 `(platform_id, owner_id, group_id)` 定位群，找到上次**成功提交**后的新增消息；按 `message_at` 排序、`event_id` 去重，保留原始引用关系。
2. 找出**当天真正出现且有可处理文本的成员**；只读取这些成员在**本群**的有效画像和相关群公共快照。不要读取所有群、所有成员的全量历史。
3. 基于消息主题挑选相关已有 Evidence Memory，限制每个成员及群快照长度；若只是普通闲聊，允许不请求模型，但**规则筛选可能漏掉语义信息**，必须可配置且支持复查。
4. 按 Token 预算装配上下文：消息、时间、发送人、来源 ID、必要旧画像；只需最少的提示规则和 JSON schema。

### 4.2 模型做“综合与修订”，不是“每日重建”

明确要求模型：
- 不重述现有画像，不为了让画像每天变化而输出变更；**无变化返回 `changes: []`**。
- 新聊天未提及的旧信息保持不变；“正在学习”不能自动升级为“精通”，“也使用 B”不能替代“使用 A”。
- 区分本人陈述、群成员对他人的描述、推测、玩笑、引述、否定、旧时事实；未经支持的第三方画像不自动晋升为确定事实。
- 群内决策和个人画像**一起综合，但分别产出并按权限入库**；不允许模型指定可跨群共享。
- 明确纠正、时效性变化、事实冲突均附消息来源和理由；不确定就建议 `review`。

建议响应协议（只允许定义的操作，不要求模型返回整份画像）：

```json
{
  "schema_version": 1,
  "changes": [
    {
      "op": "add|revise|supersede|review",
      "scope": "group|member",
      "subject_id": "wxid_123",
      "memory_id": "existing-id-for-revise-or-supersede",
      "expected_revision": 2,
      "category": "expertise",
      "content": "本人近期开始学习 TriboForm，尚未确认熟练程度。",
      "source_message_ids": ["real-event-id"],
      "reason": "本人明确陈述正在学习，与既有 AutoForm 使用记录兼容",
      "certainty": "explicit|uncertain",
      "effective_at": null
    }
  ]
}
```

`op` 示例中的竖线表示候选枚举，真实 JSON 应只返回其中一个值；`add` 不需 `memory_id/expected_revision`，`revise/supersede` 则必须提供。输出前后均禁止将群消息中的“忽略以上规则”等内容当成新系统指令。

### 4.3 提案验证（**程序规则**，0 LLM Token）

每个变更先进入候选队列，再做确定性检查：

- `scope`、`subject_id`、`memory_id` 必须落在本次可信 `(platform,owner,room)`，**不接受模型提供其他群或全局权限**；成员画像的证据必须符合主体归属/本人陈述等策略。
- 所有 `source_message_ids` 必须确实存在于本批 Raw History；禁止编造消息来源；应验证消息内容确实出现相关实体或标记待审核，但**关键词命中不能证明语义真实**。
- `revise/supersede` 要求 `expected_revision` 匹配数据库当前值（CAS）；不匹配则重新取数重审，不能静默覆盖并发修改。
- `noop` 是无操作结果；没有新消息、没有可保留事实、达不到质量标准都允许零写入。
- 严格限制每轮新增、改写、删除数量和总字符；不要求沿用 Hermes 的固定 3 条/250 字。
- **模型不拥有 delete 权限**；自动 `supersede` 只对有明确来源和时间变化的旧记录生效，复杂纠正进入 `review`，删除/撤销由本人或授权管理员执行。
- 缺来源、无效 JSON、输出超限、模型失败：**不推进成功水位**，保留消息及提案供恢复，不污染当前画像。

### 4.4 原子提交与快照刷新

单数据库事务内完成：创建/修改 Evidence Memory、写版本、绑定来源、记录审计、推进处理水位。Working Snapshot 在事务提交后重建或标记 dirty 后按需生成（禁止先改快照后写真源）。

**注意**：同一请求的多个 Patch 之间若互相冲突，必须整体校验或拆成独立事务，禁止部分落库后却把全部消息标记成功。允许“成功局部批次”，但每个批次应有独立 run/batch ID、结果和水位。

### 4.5 可选二次 LLM

只有模型提出相互矛盾的复杂关系、现有记录无法判断是否应替换、或者需要核实来源语义时，才可开启**二次小上下文复核**。默认直接进入人工 `review`，省 Token。相似度和模型自评分数只是线索，不是授权/删除依据。

---

## 5. 数据模型、生命周期与 v0.1 迁移

### 5.1 最小表集合

| 表 / 对象 | 核心键和责任 |
|---|---|
| `raw_messages`（新增） | `(platform,owner,room,message_id)` 唯一；`sender_id,message_at,received_at,text,kind,reply_to_id`；来源可追溯，可单独设置保留期限。 |
| `dream_runs`（新增） | `run_id,scope,start_cursor,end_cursor,status,attempts,provider,token_estimate,token_actual,created_at,finished_at,error_class`；重启恢复与成本分析。 |
| `dream_candidates`（新增） | 模型的 Patch 候选、校验状态、原因及证据；可审查、可拒绝、可重放但不重复落库。 |
| `memories`（保留扩展） | 继续保存正文及范围；新增可选 `revision,valid_from,valid_to,confidence_basis`；保持原有 id 不变。 |
| `memory_sources`（保留） | `memory_id → message_id` 与来源群/作者校验，最好附来源消息时间。 |
| `memory_versions`（保留） | 修订前内容、修改动作、actor、run_id、时间；支持回滚。 |
| `working_snapshots`（新增） | `(platform,owner,room,subject_id,snapshot_type)` + `content,revision,source_revision,updated_at`，仅为派生缓存。 |
| `profile_permissions`（保留） | 本人对目的群的显式授权，撤销立即生效。 |
| `memory_usage_daily`（保留扩展） | 按用途细分调用数、估算输入、实际输入/输出、可选价格计算、预算拒绝次数。 |
| `room_options`（保留） | 群白名单、暂停、调度与记忆开关；默认关闭自动跨群。 |

不急于新建关系图、Embedding、Redis、Postgres。原始事件库**不能只复用 `pending`**，否则处理队列清理会误删证据。可在已授权、版本兼容的情况下，通过**只读** Hook Inbox 补录历史缺口，但不能默默改写 Hook 数据库；要单独记录导入来源与游标。

### 5.2 状态与版本

- `candidate`: proposed / validated / review / rejected / applied。
- `memory`: active / review / superseded / archived / deleted；`deleted` 不参与召回，真正删除原始数据遵循用户删除请求和独立清除策略。
- `run`: pending / running / committed / skipped / failed / cancelled。
- `working_snapshot`: 每次相关 Evidence Memory 更新后置 `dirty`，重建成功后才切换到新 `source_revision`。

### 5.3 数据迁移约束（必须先于新功能上线）

1. 使用 SQLite backup API 对旧 `memory.sqlite3` 做一致性备份，记录 schema 版本；不能删除原数据库重建。
2. 采用**可重复执行的增量迁移**（`PRAGMA user_version` 或独立 migrations 表），仅 `CREATE TABLE` / 安全 `ALTER`，保留旧 `memories.id`、`memory_versions`、`profile_permissions`；旧记录缺元数据时标 `legacy`、不伪造证据。
3. v0.1 旧 `pending` 中仍存的文本可经显式迁移转换为 `raw_messages`；已经因 30 天清理消失的消息**不能凭空恢复**，需要以后经过授权从 Hook Inbox 补录。
4. 确保升级中断可重启继续；迁移失败应停用 Dreaming、保留旧读取与旧库，不静默覆盖旧数据。
5. 备份和恢复必须覆盖 SQLite WAL/事务；删除请求和跨群撤权需同时清理或使快照、检索缓存即时失效。

---

## 6. 调度与 Token/金额成本（配置建议，非承诺）

### 6.1 默认行为

| 参数 | v0.2 建议初始默认 | 理由 |
|---|---|---|
| 总插件 `enabled` | `false` | 先人工指定群 |
| `collection_enabled` | 随插件启用，并受群白名单限制 | 纯本地采集，无 LLM Token |
| `dreaming_enabled` | `false`（部署验收后再开） | 避免升级即产生费用 |
| 调度 | 每群按指定时区夜间 1 次；允许手动执行 | 稳定且调用少，不是硬保证仅一次 |
| `min_messages_to_dream` | 可配置，如 10 条有效文本 | 小批次可以跳过；显式记忆请求可覆盖阈值 |
| `max_input_tokens_per_run` | 示例 16,000 | 默认不会把 500K/1M 全部发给模型 |
| `max_output_tokens_per_run` | 示例 1,500 | 只输出 Patch |
| `max_calls_per_group_day` | 示例 2 | 预留过长分批/重试空间 |
| `max_cost_per_group_day` | 可配置，默认未填写即以 Token+调用硬限额控制 | 不同 Provider 单价不同，不能捏造费用 |
| `max_changes_per_run` | 示例 8（可 0） | 防记忆膨胀，优先修订/合并 |
| 每次回复记忆注入 | 按问题动态选择，常规历史预算示例 500～2,000 Token | 不应每次注入全部画像 |
| 运行失败 | 保留水位与待处理消息，退避重试 | 不丢数据、不无限花费 |

这些都是**需要经实际群聊样本验证的初始参数**，并非任意规模都适用的硬编码。不允许因 Token 超限直接截断一条消息后仍假装来源完整；应按消息边界分批并标明批次。大群可跨日清理积压，UI 显示未处理数量与预估成本。

### 6.2 成本核算必须分清三种数字

1. **预估 Token**：请求前用 Provider tokenizer（可用时）或保守字符估算；用于拒绝超预算的调用。
2. **实际 Token**：优先记录 Provider 返回的 prompt/completion/cached 等 usage；缺失时标 `estimated`，不伪装成账单实数。
3. **金额**：管理员配置具体模型的输入、输出、缓存单价后再计算；支持月/群账本。价格随 Provider 变化，不把具体人民币价格写死在插件。

请求前预留预算，响应后按 usage 结算；失败也计入调用数和已发生的费用（若 Provider 实际收费）。后台提取与用户正常问答**分别计量**，并避免重复计算 AstrBot 自己已统计的 Token。默认不借用用户的主聊天模型静默做后台重模型推理。

### 6.3 每天 200 条消息的示例

若平均每条 45 Token，仅当天原文约 9,000 输入 Token；加上当天发言者的**精简旧画像**、群公共快照、规则与 JSON 元数据后，单次调用可能在约 11K～15K 输入 Token 的量级，输出另计。只是示例，不等于必然只调用一次：超预算需要分批，部分消息没有高价值信号则可跳过 Dreaming。每群每天做完未必写入任何记忆。

**长上下文不是省钱策略**。只有当用户明确要求深度历史分析，且该任务需要原始证据时才扩大上下文；默认靠本地检索、精简快照和小额预算。

---

## 7. 按需检索与回答组装

### 默认读取顺序

1. 可信 AstrBot 事件确认 `(platform,owner,room,requesting_user)`；优先使用正常 AstrBot 已提供的当前请求信息，避免重复注入已在上下文中的内容。
2. 仅当前群有效公共记忆；仅当前提问者的群内画像；其本人按目的群授权且政策允许的**全局画像**。
3. 本地词法检索匹配的原始聊天/历史证据，按时间、提问者、主题、引用链过滤，严格不跨来源群。
4. 对结果做优先级排序：显式最新更正、可靠来源、有效时间、相关度；冲突未决的候选不直接成为回答依据。
5. 先预算再组装，以临时上下文附加到当前 LLM 请求，不写入 Soul/系统人格，不加载全部历史。

### 三种按需模式（不是三次额外模型调用）

- `normal`: 只注入有关的短画像/群记忆，通常低于约 2K Token；不相关则 0。
- `history`: 本地检索本群消息原文和来源，通常预算 2K～8K Token。
- `deep`: 用户明确要求长期汇总/逐月对比时，允许较大预算，如 10K～30K Token 或操作者手动加大；受 Provider 实际上下文和费用限制。

第一版继续从**确定性关键词、日期、成员、来源索引**开始；可检测 SQLite FTS5 并使用适合中文的方案，同时保留降级检索。FTS5 原生默认分词不等同中文词法/语义理解，**不能声称已具备高质量中文 BM25 检索**。Embedding/向量+BM25 混合检索是可选后续阶段，需独立度量召回效果与成本。

### 可审计的检索记录

记录命中来源、排除原因、上下文预算和必要的来源消息 ID；默认不在普通日志输出私密原文或令牌。对用户说明回答是引用记忆还是引用原始群消息，不把“旧记忆”当作最新真相。

---

## 8. 权限、隐私与安全边界

| 记忆层 | 写入来源 | 读取范围 | 跨群 |
|---|---|---|---|
| 群公共记忆 | 当前群已核实聊天 | 当前群允许使用的问答 | 禁止 |
| 群内成员画像 | 本人明确陈述或具可靠来源的候选 | 当前群许可的 AI 场景，遵从隐私设置 | 禁止 |
| 本人全局画像 | 本人私聊主动添加/逐条明确批准 | **仅本人**在本人授权的目的群提问时 | 按目标群授权 |
| 原始群消息 | 实际接收的 Hook/AstrBot 事件 | 群内限定历史查询/管理审计 | 禁止直接搬运 |

- 身份只能来自可信事件字段：不能根据昵称、模型推断、转发文本或引用文本替他人授权。
- **自动群画像默认不升级为全局画像**。用户是否允许生成群内画像、保留多久，应有告知、退出和删除途径；管理员查看权限不得被误写成跨群分享许可。
- 涉及个人隐私、联系方式、健康、财务、密钥、位置秘密等，默认不提取为长期画像；简单正则不是可靠的敏感信息检测，治理需要硬性权限和保守回退。
- 普通群聊天可能包含“请忽略系统规则”等恶意注入，所有原文、LLM Patch 都视为**不可信数据**；不允许改变 tools、Soul、管理权限、跨群共享和真实发送开关。
- 任何后台巩固和手工重跑在**执行时重新检查群白名单/账号/当前授权状态**；撤销后后续调用立即拒绝，并使相关快照/缓存失效。
- 删除语义明确区分“停止引用”“撤销跨群授权”“删除画像”“删除原文/来源”；彻底删除需处理衍生快照与引用，不在普通问答中暴露已删除内容。
- 插件默认只读分析，不触发 MCP、工具调用、外部 Web 请求或群自动回复；只允许已有私聊管理命令按现有 Hook 开关发送其确认结果。

---

## 9. 开发实施路径（每阶段单独可交付）

### M0 — 基线审计、迁移和数据安全（最高优先级）

**文件**：`storage.py`, 新增 `migration.py`，必要时改 `README.md`、`_conf_schema.json`。

- [ ] 备份现有 SQLite；识别旧 Schema 与已处理队列；建立增量 `schema_version`。
- [ ] 建 `raw_messages`、`dream_runs`、`dream_candidates` 等最小表，保留旧表；使旧数据继续可读。
- [ ] 明确原始历史存储边界、已处理队列清理规则与 1600 字符超限处理（记录不可采集原因；不无声丢弃）。
- [ ] 保存原始事件 `message_id`/时间/作者/引用；为未来只读 Hook Inbox 补录预留协议。

**完成标准**：升级前后旧记忆 ID、授权、版本均保留；关闭 Dreaming 时普通群消息仍稳定归档；失败不改成功水位。

### M1 — Memory Evolution 引擎（替换旧“每天新增候选”思路）

**文件**：重构 `extraction.py`；新增 `evolution.py`, `validation.py`，调整 `storage.py`。

- [ ] `ContextSelector` 为每批选择当前群消息 + 当天涉及成员的相关旧画像/群快照。
- [ ] 提取器只输出有证据的 JSON Patch（支持零变更），实现 schema 和来源检查。
- [ ] 将 `propose → validate → commit` 分层；实现 `add/revise/supersede/review`，明确自动删除禁止。
- [ ] 数据库 CAS revision、事务性审计、来源绑定、幂等 `run_id + op_index`。
- [ ] 同一批包含群公共事实和个人画像，只在必要时增加一次独立语义复核。

**完成标准**：张三“开始学习 TriboForm”不会抹掉“使用 AutoForm”；第二天重复提及不会无限增加记录；当天无相关事实返回零修改。

### M2 — Working Snapshot 与历史证据分离

**文件**：新增 `snapshots.py`，调整 `storage.py`、`retrieval.py`。

- [ ] Evidence Memory 是真源；快照是可重建的精简自然语言投影。
- [ ] 快照局部更新，不将每人整份画像每天覆盖；版本和最后有效证据可追溯。
- [ ] 旧记忆时效状态与来源时间分开；失效/新旧冲突不直接删除证据。

**完成标准**：模型可以读取一份精简当前画像，管理员仍能看到为什么这样描述及历史版本。

### M3 — 成本受控 Dreaming Scheduler

**文件**：重构 `scheduler.py`、配置 Schema、usage 数据。

- [ ] 按群独立夜间/手动/显式记忆执行，准确记录 last **committed** cursor；无新增消息无需调用。
- [ ] 配置输入/输出 Token 及每日调用/费用限额；支持模型 usage 为空时以估算方式明确展示。
- [ ] 同一群单任务串行；运行中配置关闭/撤权立即阻止后续提交；超时和失败退避。
- [ ] 对过长消息按完整消息边界分批；过载时保留 backlog，不挤占正常聊天回复。

**完成标准**：单轮失败不丢历史、不盲目重试重复写库，配额触顶后不偷偷调用模型，手工界面能看待处理量与估算 Token。

### M4 — 有来源的按需 Retrieval

**文件**：重构 `retrieval.py`，调整 `main.py` 事件注入。

- [ ] 查询当前群公共记忆、提问者群画像及已授权全局画像；默认不读取其它成员私人画像。
- [ ] 支持本地时间范围、成员、关键词、消息引用检索；结合快照和原文证据组装受 Token 限制的上下文。
- [ ] 上下文不相关时零注入；剔除未审、删除、过时候选；记录检索来源与排除原因。
- [ ] 容量随 Provider 实际可用配置变化，不将 1M 当作每次用量。

**完成标准**：询问昨日板材决定能找到对应群聊原文；另一群无法读到该消息；无关提问不带大量历史。

### M5 — 可视化审查、权限和上线治理

**文件**：`panel.py`、`views/memory/*`、`main.py` 私聊命令、`README.md`。

- [ ] Dreaming 状态：最后成功/失败/跳过、候选数、实际修改数、当前水位、Token/成本、队列积压。
- [ ] Diff 审核：`旧文本 → 建议新文本 → 来源消息`；管理员可预览、批准、拒绝、回滚；本人管理全局画像。
- [ ] 支持按群开关、手动 dry-run、处理预算、授权清单、撤销与清除。
- [ ] 旧数据库迁移说明、已知限制、单进程运行要求、版本和操作手册同步。

**完成标准**：WebUI 不依赖单独搭建服务；页面所有管理动作仍经 Dashboard 鉴权和群范围检查；同一对象版本冲突不会静默覆盖。

### M6 — 后续可选增强，不阻塞 v0.2.0

仅在数据证明值得后再做：中文 FTS5/自有分词增强、可选 Embedding + 向量检索、多模态理解（经过授权的视觉/语音）、Hook Inbox 自动补录、大型群多任务并行、跨进程锁、记忆质量评估。**不预装向量库，不引入 Hermes/OpenDream/jiuwenSwarm 运行时依赖**。

---

## 10. 人工验收场景（不新建测试代码）

1. **普通消息**：未 @机器人的微信文本进入 `raw_messages`；同消息重放不重复；异常长度显示跳过/待补录，而非假装成功。
2. **旧数据**：原 `memories`、`memory_versions`、`profile_permissions` 原封保留；迁移中断可恢复；有一致性备份。
3. **画像演进**：连续几天聊天内容不同，但旧画像中未被新证据推翻的部分继续存在。
4. **补充不冲突**：使用 AutoForm 后新增学习 TriboForm，不应自动判为旧软件失效；第三方猜测不写成本人确定事实。
5. **零变更**：每天的 200 条全是普通聊天或重复内容时，LLM 合法输出 `changes=[]`，无需强行落库。
6. **冲突保护**：同一记忆在 Dreaming 期间被人工修改，旧版本 Patch 因 CAS 失败而拒绝覆盖。
7. **失败恢复**：Provider 超时、JSON 无效、退出重启后历史仍可处理，成功水位没有错误前进；重复执行不重复写入。
8. **预算**：达到 Token/调用上限后不再调用；usage 有实际/估算标注；模型价格未知不出现凭空金额。
9. **群隔离**：A 群决策与原始历史不能在 B 群回答；群内个人画像不能因模型推理自动变成全局共享。
10. **本人跨群授权**：私聊建立全局画像 → 授权 A 群 → 仅本人在 A 群提问可使用；撤销后立即停止，B 群始终隔离。
11. **无关问答**：不引用长期记忆时，额外记忆上下文为零或很小；历史查询能给出真实来源。
12. **治理**：删除、拒绝、冲突审核及手动回滚有审计；被删除/未获批准的记忆不会在缓存中继续出现。
13. **后台可用性**：Dashboard 仅允许 `admin_users` 登录用户访问授权群；其他用户/群/账号不能通过 API 枚举数据。
14. **现有系统无回归**：不修改 PC Hook 收发、不更换现有聊天 Provider、不要求 Hermes/Agents/OneBot；Dreaming 失败不影响 AstrBot 正常回复。

## 11. 优先级、非目标、完成定义

- **P0**：历史原文保存与迁移、身份隔离、增量画像 Patch + 来源校验 + 版本保护、成本限额、失败恢复。
- **P1**：快照、按需原文检索、WebUI 差异审核、人工 dry-run、跨群撤权后的缓存失效。
- **P2**：语义检索、媒体理解、复杂语义反思、多进程扩展、跨旧数据库历史回填。

**v0.2.0 可交付定义**：一个群能够在不 @机器人的普通聊天持续到达后，以受控预算巩固既有群记忆和成员画像；每天不必生成新画像；每次变更有来源、版本和撤销机制；日常回答只消耗必要的记忆 Token；跨群权限默认关闭且可撤销；旧数据不丢失。

**实际进度声明**：这是待执行计划。v0.1.0 的功能代码目前未因本计划自动升级。每阶段实施后才在 PLAN 更新完成勾选项，不得把“参考项目文档写了”误报为“本插件已实现”。

---

## 12. 资料索引与核查说明

- [openJiuwen Agent Memory — 动态抽取与巩固](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/docs/features/construction/F02-dynamic-extraction-consolidation.md)
- [openJiuwen Agent Memory — Dreaming 定时演进](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/docs/features/F04-dreaming.md)
- [openJiuwen Agent Memory — 中期记忆非破坏式归档](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/docs/features/control/F06-middle-term-memory.md)
- [JiuwenSwarm — Dreaming Sweeper](https://github.com/openJiuwen-ai/jiuwenswarm/blob/341dfdddd4a6dd2b7f8f8fde5a27eabebe05a706/jiuwenswarm/agents/harness/common/memory/dreaming/sweeper.py)
- [OpenDream — Dreaming Memory Change Control](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/docs/technical-notes/dreaming-memory-change-control.md)
- [OpenDream — Semantic Verifier Promotion Policy](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/docs/adr/ADR-008-semantic-verifier-promotion-policy.md)
- [Hermes Dreaming — Apply Memory Operation](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/tools/apply_memory_op.py)
- [Hermes Dreaming — Orchestration](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/orchestration.py)
- [本插件现有提取器](https://github.com/abincn/astrbot_plugin_wechat_memory/blob/8a716750c5e7559419a1e15a25af0bd8be41d78e/extraction.py)、[存储](https://github.com/abincn/astrbot_plugin_wechat_memory/blob/8a716750c5e7559419a1e15a25af0bd8be41d78e/storage.py)、[调度器](https://github.com/abincn/astrbot_plugin_wechat_memory/blob/8a716750c5e7559419a1e15a25af0bd8be41d78e/scheduler.py)、[检索](https://github.com/abincn/astrbot_plugin_wechat_memory/blob/8a716750c5e7559419a1e15a25af0bd8be41d78e/retrieval.py)

核查范围：上述四个仓库的公开源码与设计文档，以及本插件 v0.1.0 实际代码；不代表已独立审计第三方项目的全仓安全或全部生产效果。项目文档中的基准与作者自述不当作本插件实测结果。