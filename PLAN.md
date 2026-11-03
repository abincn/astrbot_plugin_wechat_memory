# PLAN v0.2.0 — AstrBot 微信长期记忆 / 受控 Dreaming

> 2026-11-03。依据用户说明：**记忆系统从未部署，也没有旧数据**。本版直接建立新数据库 Schema，不开发 v0.1.0 数据迁移/回滚兼容。代码开发完成不等于已在真实 AstrBot/Hook/Provider 环境验收。
>
> 项目边界：独立 `astrbot_plugin_wechat_memory`，依赖已安装的 `astrbot_plugin_wechat_hook` 提供普通群消息。不使用 Hermes、wechat_agents、OneBot，不影响微信收发、AstrBot Agent Soul、现有工具。未经明确要求不编写任何测试代码，包括临时测试。

## 参考项目与取舍

| 上游源码 | 已核查机制 | 此插件的实现选择 |
|---|---|---|
| [openJiuwen Agent Memory](https://github.com/openJiuwen-ai/agent-memory/blob/48be7b315049e384eafe4d4a75ee2526396a1652/jiuwen_memory/construction/evolver_impl/dynamic_evolver.py) | extract→consolidate decision→reflect→persist，Dreaming 调度和演进分离 | `extraction.py` 只生成候选，`evolution.py` 校验/写库，`scheduler.py` 调度；不引入大框架 |
| [JiuwenSwarm Sweeper](https://github.com/openJiuwen-ai/jiuwenswarm/blob/341dfdddd4a6dd2b7f8f8fde5a27eabebe05a706/jiuwenswarm/agents/harness/common/memory/dreaming/sweeper.py) | 增量扫描、checkpoint、Token 限额、promotion | 独立原始消息档案及仅提交成功后推进水位 |
| [OpenDream](https://github.com/pylit-ai/opendream/blob/1c3035464e54f59ecc11947f71336d0d2d7bb1c7/docs/technical-notes/dreaming-memory-change-control.md) | 来源证据、验证后发布、检索审计、可回滚 | provenance、review、`revision` CAS、检索日志；无额外服务 |
| [Hermes Dreaming](https://github.com/alejandroiglesias/hermes-dreaming/blob/a6bf5f6ca8a1295c671d439abb0cfa495a979a22/hermes_dreaming/orchestration.py) | 零变更允许、少量有价值改动、可预览 | `changes=[]`、最大 8 改动、dry-run 审核；不安装 Hermes |

## 目标数据路径

```text
WeChat PC Hook → AstrBot 普通群消息事件
  → storage.raw_messages (长期证据、消息ID去重、按群独立)
  ├─ 低成本本地检索 → current-group scoped context → AstrBot 本轮 LLM
  └─ Dreaming scheduler (夜间/按需/主动记住 + Token/调用限额)
       → context = 新消息 + 相关已有群记忆及本人画像
       → provider JSON Patch (允许零修改)
       → evolution validation (权限、来源、身份、revision CAS)
       → SQLite 事务 (memories + sources + versions + cursor)
       → working_snapshots (本群/成员简明自然语言视图)
```

## 交付阶段

### M0 — 新数据库 / 原始历史  **[源码已实现，待实机验收]**
- [x] 新装 SQLite WAL 与作用域键 `(platform,owner,room)`、`raw_messages`、消息去重、时间、作者、引用、媒体路径元信息。
- [x] Raw History 与分析水位、Memory 分离；正常提取不删原文；超大文本标记 `oversized`。
- [x] Hook Inbox 只读补录预留及 `import_cursors`，不修改 Hook 数据库。
- [x] **按新装数据库设计**，不开发不存在旧数据的迁移功能。
- [ ] 实机检查普通非 @ 微信消息是否都投递；媒体字段是否由所用 Hook 版本提供。

### M1 — 增量 Memory Evolution  **[源码已实现，待实机验收]**
- [x] `extraction.py` 读取新增群聊及相关已有记忆，单次模型调用返回 JSON Patch。
- [x] `evolution.py` 验证 `source_message_ids`、本人陈述、群身份、旧版本 revision；采用 `add/revise/supersede/review`；禁止模型直接删除。
- [x] 支持 `changes=[]`；定量限制每轮最多 8 条；失败不前进 cursor。
- [x] 不确定/替代进入候选审核，管理员审批时再次检查旧记录 revision。
- [ ] 真实模型演练“AutoForm 和 TriboForm 可同时存在”“未提及旧画像不会消失”“第三方推测不得成为本人画像”。

### M2 — 精简 Working Snapshot 与证据分层 **[源码已实现，待实机验收]**
- [x] `memories` 保存事实正文，`memory_sources` 绑定原始消息，`memory_versions` 保留修订旧状态。
- [x] `working_snapshots` 是可从有效事实生成的短自然语言投影；按当前群/用户局部刷新。
- [x] 仅在用户明确询问自身/群的背景且常规检索没命中时可补充快照，不默认把整份画像每次注入。
- [ ] 验证更新、回滚、删除及本人退出时快照与真源一致。

### M3 — 低成本 Dreaming Scheduler **[源码已实现，待实机验收]**
- [x] 按 `dream_timezone` + `dream_hour` 执行，每群支持手动 `run_now`、费用真实发生的 `dry_run`、显式“请记住”唤醒。
- [x] 按群隔离的 SQLite lease、消息完整边界切批、失败退避、重启后保留成功水位及未处理原文。
- [x] 每日调用次数、估计 Token 上限，输出 Token 预算预留；可选输入/输出模型美元单价以及金额预算预检。
- [x] 实际 Provider usage 可用时优先记录，缺失时估算并标注；用 UTC 预留日期结算避免跨日错账。
- [ ] 真实 Provider 未必提供 output hard cap/usage；账单硬上限**未验证且不能承诺**。

### M4 — 最少必要记忆 / 原文检索 **[源码已实现，待实机验收]**
- [x] 当前群公共记忆、本人本群成员画像、本人显式授权目的群的全局画像。
- [x] 轻量关键词/双字片段匹配；SQLite trigram FTS5 可用时检索原文，否则 LIKE；历史问答可按“昨天/前天”时间边界过滤并引用原始消息 ID。
- [x] 三档 `normal/history/deep` 动态字符预算（粗略 Token 代理）；不把 500K/1M 上下文当默认用量。
- [x] 检索审计记录选中 ID/原始消息来源；不将未审核/软删除候选放入输出。
- [ ] 真实中文历史召回效果与 Provider 窗口仍需人工衡量。

### M5 — 管理工作台、隐私和审计 **[源码已实现，待实机验收]**
- [x] AstrBot 原生 Views + `register_web_api`；需 Dashboard 身份并在 `admin_users` 白名单。
- [x] 当前群统计、原文、运行记录、候选 Diff、审核/拒绝、记忆版本、人工编辑/回滚/停用/删除、dry-run、JSON 导出、按群暂停。
- [x] 用户私聊 `/wm` 维护全局画像与目的群授权；本人可退出当前群画像，并删除本群本人消息/画像；全局画像不会自动跨群。
- [ ] 实机检验 Dashboard bridge、`TextPart.mark_as_temp()`、Hook 私聊命令发送开关。
- [ ] **隐私边界**：群讨论公开内容可能同时涉及多人；清除用户原始消息后共享事实及备份仍需操作者审核处理；软删除记录留审计，不是不可逆物理擦除。

### M6 — 可选增强 **[基础源码已实现，默认关闭，集成尚未验收]**
- [x] SQLite FTS5 trigram / LIKE 降级；可选 OpenAI 兼容 Embeddings API + 进程内余弦相似度（不依赖向量库）。
- [x] 可选白名单本地文件图片理解、语音识别、文本摘录、ffmpeg 视频首帧；派生文本保留媒体身份并默认不自动晋升为确定成员画像。
- [x] 可选 Hook Inbox 只读分页补录，独立 sequence 游标。
- [ ] 不支持完整视频语义、未下载的微信媒体、向量索引高并发扩展、多进程分布式锁。
- [ ] 所有外部模型接口、ffmpeg、路径与费用需要实机验证；不要把 opt-in 基础接口当作端到端功能已通过。

## 安全与成本约束

1. 默认 `enabled=false`、`dreaming_enabled=false`、`embedding_enabled=false`、`media_enabled=false`、`backfill_enabled=false`；没有配置模型不会自动请求模型。
2. 数据只按可信 AstrBot Hook 消息身份隔离；群消息内容是**数据**而非系统指令；`llm_generate` 不传工具，不能更改 AstrBot Soul 或微信发送配置。
3. 模型不能创建全局画像/跨群授权。全局画像仅本人私聊添加，并在指定目的群由本人主动授权。本人不同微信机器人账号不自动关联。
4. Token/价格配置按 Provider 实际值填写。模型自报分数不当成可信证据；金额和 Token 是预检与统计，不能代替账单硬限制。
5. SQLite 原始消息长期保留会占用空间；管理员须提供告知、可清除机制、备份与实际部署数据保留策略。
6. 不引入外部框架运行时依赖；向量/媒体模型接口为可选。仅在启用视频功能且处理视频时需 ffmpeg。

## 仍需人工执行的验收（不编写测试代码）

1. `enabled=false` 运行不采集；测试群白名单接收未 @ 文本及特殊内容，跨群隔离。
2. 同消息重复送达只入库一次，重启后 Raw History 仍可读取；过长消息跳过不阻塞后续批次。
3. Provider 生成空变更，成功提交 cursor 但无新记忆；失败保持 cursor。
4. 修改已有画像后，旧版 Patch 因 CAS 冲突拒绝；审核批准或驳回后快照正确。
5. 昨日的消息可通过有时间约束的本地检索找到；另一群查不到。
6. 额度耗尽不再自动调用后台模型；实际 usage 与估算标签和美元单价一致。
7. 本人私聊授权、撤销、退出及清除后，不能继续访问对应画像；全局不能由群记录自动生成。
8. Dashboard 未授权用户无法读取 API；管理员只按白名单群访问数据；没有 XSS/HTML 注入。
9. Hook Inbox 补录、Embedding、图片/音频/视频首帧均在显式开启后且使用可信路径和模型时验证。
10. 在真实 AstrBot v4.29.x + 所用 Hook + Provider 上验证消息生命周期、LLM 请求和 Dashboard，检查实际运行日志。

## 当前发布口径

源码模块覆盖 M0–M5 及 M6 的**可选基础实现**；Python / JavaScript 静态语法检查可以做，但**没有自动化测试文件、没有真实 AstrBot/Hook/Provider 运行验收**。故不宣称生产可用。用户已确认无旧数据，本版不需要旧数据库升级方案。
