# AstrBot WeChat Memory — v0.4.0-beta.3

> **Dreaming V3 重构预览版（beta.2）。** Event / Claim / Evidence 是新的统一信息模型；Profile 为成员视图。已提交源码，但**未在真实 AstrBot / Hook / Provider / Dashboard 实机运行验证**，不能视为正式稳定版。不新增测试代码；必须先使用数据库副本进行人工验收。

## beta.3 历史预览覆盖校验修复

- 修复模型把 `reviewed_count` 填成 0 或其他错误数量后，预览任务持续报 `V3 reviewed count does not cover input messages` 的问题。
- 旧协议让模型自己数消息，并在 JSON 示例中硬编码 `0`；新协议由服务端生成每批消息的 `batch_coverage_key`，模型原样回传，服务器验证正确批次与原始消息证据。
- 仍严格检查 `coverage.complete`、`deferred_message_ids`、来源消息 ID 和群作用域；不完整结果不会推进游标。
- 历史预览、正式重分析均允许在覆盖异常时缩小批次自动重试（最多额外两次），仍不完整则暂停，保留断点。
- 旧任务无需删除，升级后点击“恢复任务”。模型重试仍消耗每日调用预算。

## beta.2 工作台与配置

- 管理界面默认只显示 **概览、成员画像、群事件、事实与观察、历史分析**；执行记录、旧版话题/观察、原始群聊与审计统一收入左侧「高级管理」。
- 任务列表直接呈现状态、消息 ID 范围、进度和错误原因；失败任务升级后可点击「恢复任务」。曾显示 TypeError 的预览，根因是 V3 动态批次计算在无跳过消息时调用了错误的 max()。
- AstrBot 配置面板提供 **9 个基础项 + 6 组高级项**，旧平铺字段首次加载时会迁移到对应分组并保存；保留原先已填写的模型与预算等值。
- 关闭高级选项不影响已启用的 Hook 原始归档；历史 ID 范围的预检不再错误要求 V2。
- 媒体处理、补录与导出保留；导出会包含当前群 V3 事件、主张和覆盖摘要，请避免把受限个人资料分享给无关人员。

## V3 从头重建的核心能力

- **老群画像**：除本人明确表述外，允许从长期群内行为和带说话者归属的第三方陈述提取成员线索；推测和第三方说法不得无证据当作已确认职业或健康事实。
- **同一事件关联**：事件作为稳定 ID，会议、聚餐、项目的地点/人数/任务为关联原子 Claim；优先复用原事件与主张，不把不同时间的活动强行合并。
- **原文证据**：Claim 与原文保持支持/转述关系，每条原始消息有 V3 处理账本；不以模型返回「0 处修改」代替分析结果。
- **独立画像巩固**：画像巩固操作可以对多批历史证据进行成员专项分析，不要求有新消息或本人重复自述；普通自动调度在存在待巩固成员时也会处理。
- **可用的个人背景**：允许模型收录对回复有用的联系方式、地址、健康、财务背景，敏感数据标记受限；在群回复的检索注入中限制精确信息输出，不能因群里曾出现就默认公开。
- **动态分批**：单批最多沿用 500 条硬保护，但按输入 Token 与字符实际限制缩减，不再使用 V2 固定 100 条上限。
- **历史重分析**：V3 接管原有历史重分析任务 UI，沿用独立历史任务水位；预览结果进入 V3 运行详情，正式写入原子主张及证据，不回退生产 `dream_cursor`。
- **版本和人工控制**：管理员可查看 V3 事件、成员画像、所有 Claim、证据、覆盖率，批准/更正/撤回 Claim；编辑保留前版记录。

### 升级及历史恢复步骤

1. 停止插件后台任务，保留 SQLite 文件和 WAL / SHM 文件；自行额外制作**一致性 SQLite 备份**。首次迁移到 Schema 5 时，插件会尝试自动保存 `memory.sqlite3.before-v3.sqlite3`，存在时不覆盖。
2. 新 Schema 是**新表增量迁移**，保留原始消息、旧 V2 话题/观察/记忆、旧生产游标以及 Hook Inbox 补录游标。旧版 V2 记录继续可在原视图查看；它们**没有自动被认定为 V3 已确认画像**。
3. V3 配置 `dreaming_v3_enabled=true`（新版默认值）；如果要重建之前已处理的群历史，还需 `reanalysis_enabled=true`。
4. 在工作台选择「历史起始原文 ID 1」「历史结束原文 ID 0」，先 Preview，再 Apply。**原来的生产游标不会自动回退**，否则升级后的老群可能看起来仍没有新 V3 画像。
5. 在 V3「成员画像」「群事件」「原子主张」「分析覆盖」查看来源，必要时点击「画像巩固」处理待巩固成员。预览、Apply、画像巩固都消耗 Provider 预算。
6. `max_calls_per_group_day` 默认仍是 **2 次/群/UTC 日**，`max_tokens_per_group_day` 默认 **40000**。批量历史重建应按实际费用自主调整，预算耗尽的历史任务可在额度重置后手动恢复。

### 与旧 V2 的边界

V3 直接接管新的提取、画像与内部检索；V2 的 `topic_states` / `observation_signals` / `memories` 表作为兼容历史保留，**不继续作为 V3 唯一真相来源**。保留旧人工记忆召回，避免原有手工输入不可用。旧 PR #2 的试验性修复没有合并进入此重构。

**已知限制 / 尚未声称完成的事项**：语义相似事件的准确自动合并仍需要真实聊天语料人工核验；个人资料的使用策略当前是群内检索前过滤及上下文最小化，并非外部插件通用安全网关；超长消息以可见 deferred 状态保存，需人工重评；模型对单批声称 complete 仍不能数学上保证语义零遗漏；跨版本 SQLite 降级必须恢复备份。实机验收完成前请先在非唯一生产环境使用。

## 历史版本文档

独立的 AstrBot 长期记忆插件。运行链：PC 微信 Hook → [astrbot_plugin_wechat_hook](https://github.com/abincn/astrbot_plugin_wechat_hook) → AstrBot → 本插件。**不需要 Hermes、OneBot 或微信 Agent 插件**；不接管真实微信消息发送。

> **发布状态：源码实现 / 开发预览。** 已进行语法静态检查，**未在真实 AstrBot、PC 微信 Hook、Provider、Dashboard 环境中完成运行验收**。首次安装只在测试群使用，数据库备份和 API 费用须自行管理。当前实验版支持 v0.2.2 SQLite 数据库增量升级至 Schema v3；只做旧库原位扩展，不重建原始聊天表。升级前须完成一致性备份，并先在测试副本上验证。

## v0.3.0-alpha.2 Dreaming V2 实验分支

> **仅用于手动验收，不要直接升级唯一生产实例。** 功能默认关闭。升级数据库前必须使用 SQLite backup API 完成一致性备份。Schema 将由 V2 迁移到 v3；旧插件不可直接对 v3 数据库回滚运行，降级须连同升级前备份恢复。

本分支新增：

- 动态话题累计摘要 `topic_states`，附原文来源 `topic_sources`。
- 可跨批次的群/成员观察 `observation_signals`，及来源 `observation_sources`。待观察内容**不自动注入正式问答**。
- V2 模型 JSON：`topics` + `observations` + `changes`；严格核验来源消息 ID、成员本人自述、群作用域与 revision。空正式记忆不代表没有观察信号。
- 观察、话题、正式记忆与**生产分析游标**在同一事务内提交。模型失败或结构不合法不推进游标。
- 历史重分析任务 `reanalysis_jobs`，独立任务进度，支持预览/正式、暂停/恢复/取消、沿用群预算和锁；**不回拨正常分析游标**。
- 工作台查看“话题累计摘要”“成员与群观察”“历史重分析任务”，Dreaming 运行记录显示 V2 附加诊断。
- 成员退出或清除历史时使相关派生观察失效并遮蔽受影响的话题摘要；修复修订审批错误替代原记忆的行为。

**启用步骤**：部署分支后先确保已有数据库备份；在插件配置中设 `dreaming_v2_enabled=true`，需要重新分析旧记录则另设 `reanalysis_enabled=true`；先使用历史重分析 `preview` 确认模型输出，再选择 `apply`。模型调用仍计入 `max_calls_per_group_day` 和 Token 预算。单批软限制为 60 条，需按预算逐批处理。

**已知限制**：

- 尚未在你的真实 AstrBot 4.29.4、Hook 和模型网关环境验证；没有承诺可以提取特定数量画像。
- 观察按持久 ID 或精确归一化文本合并；复杂语义合并与矛盾自动仲裁仍需下一阶段，不会无依据地自动晋升。
- 话题与观察的可追溯原始来源记录在数据库表中；本原型管理页面先提供概要记录，完整来源详情交互仍待完善。
- 历史任务在每日预算耗尽后暂停，需在下一额度周期手动恢复；实机压力与中断恢复未验收。
- 不含任何新增自动化/临时测试代码，代码提交不等于真实运行验收。

## v0.2.2 修复

AstrBot 4.29.4 插件视图的 iframe 不允许浏览器原生 `confirm()`。此前点击「从 Hook Inbox 补录」会被浏览器直接拦截，导致无网络请求且无反馈。本版补录为只读操作，点击后立即显示运行状态并发起请求；删除及回滚改为页面内 `<dialog>` 确认，不再依赖受限的原生弹窗。页面 JS 附带版本参数防止旧脚本缓存。

## 功能

- **M0 原始证据**：普通群聊事件独立归档 SQLite（WAL）、按 `(platform, bot account, group, message_id)` 去重；与 Dreaming 已处理状态分离，自动分析不会删除原文。记录作者、时间、引用和媒体元数据。
- **M1 增量 Dreaming**：`新群聊 + 相关已有群事实/成员画像` → LLM 生成 JSON Patch；允许 `changes: []`；不自动将群内画像推广到全局。严格验证消息来源、本人自述、作用域及旧版本 revision，使用事务性增量写入。
- **M2 当前画像与证据分离**：长期记忆保留事实和来源；`working_snapshots` 是可重建的精简当前自然语言摘要，不代替证据。每次更新按成员或群局部刷新。
- **M3 成本受控**：每天按时区/群执行，支持手动、dry-run 和明确“请记住”唤醒。逐群调用量及估算 Token 预算，提供可选美元单价和预算预检。模型返回 usage 时记录实际数据，缺失时标注估算。失败保留原文和成功水位。
- **M4 按需召回**：只读取当前群群记忆、提问者本群画像及其本人授权的全局画像；本地中文 trigram FTS5（可用时）或 LIKE 降级检索，支持昨日/前日、历史和深度模式。日常问答优先少注入上下文，原始消息可附消息 ID。
- **M5 管理工作台**：AstrBot 原生 plugin Views；Dashboard 登录加用户名白名单、按群作用域、记忆内容编辑/禁用/删除、来源、修订版本、候选差异审核/拒绝、回滚、执行记录、成本、dry-run、单群暂停、JSON 导出。
- **M6 可选增强**：OpenAI 兼容 Embeddings API 的向量 + 词面混合召回（默认关闭）；仅从显式许可本地目录处理图片、音频、文本文件与视频单帧（默认关闭）；管理员对现有 Hook Inbox 只读补录（默认关闭）。这些能力需要正确配置相应模型/路径，**不代表在真实环境已验证**。

所有默认功能尽量只依赖 Python 标准库、SQLite 与 AstrBot 现有接口；视频首帧额外需要系统 `ffmpeg`。不创建额外后台 Web 服务。

## 安装与最小配置

1. 在 AstrBot 插件界面使用 `https://github.com/abincn/astrbot_plugin_wechat_memory` 安装，或将源码放入 `AstrBot/data/plugins/astrbot_plugin_wechat_memory`，确认该目录直接包含 `main.py`、`metadata.yaml`。
2. 先确认 [WeChat Hook adapter](https://github.com/abincn/astrbot_plugin_wechat_hook) 已经能向 AstrBot 投递群消息；如果平台实例 ID 不同，配置 `platform_id`。
3. 本插件配置设置：`enabled=true`，`allowed_rooms=["真实群ID@chatroom"]`。默认后台 Dreaming 关闭；普通文本可先不花模型 Token 落库。
4. 需要 Dreaming 才设置 `extraction_provider_id` 并打开 `dreaming_enabled`。每日执行小时 `dream_hour=3`，时区 `dream_timezone=Asia/Shanghai`，示例每群预算 `max_calls_per_group_day=2`、`max_tokens_per_group_day=40000`。
5. 设置 `admin_users` 为实际 AstrBot Dashboard 用户名，保存并重启插件；打开插件的“微信群长期记忆 · Dreaming”可视化工作台。**`admin_users=[]` 时禁止管理 API**。

### 最重要的预算参数

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `max_input_tokens_per_run` | 16000 | 单轮输入 Token **保守估算**上限 |
| `estimated_output_tokens_per_run` | 1500 | 每轮输出预算预留值，不是 Provider 硬性最大输出限制 |
| `max_calls_per_group_day` | 2 | 每群 UTC 日期最大后台调用数，含失败 |
| `max_tokens_per_group_day` | 40000 | 每群当天估算输入 + 输出预算预检；实际输出可能超出 |
| `max_cost_per_group_day_usd` | 0（关闭） | 可选金额预检上限，只有输入和输出单价都配置后启用 |
| `input_price_usd_per_million` | 0 | 根据实际服务商设定百万输入 Token 单价 |
| `output_price_usd_per_million` | 0 | 根据实际服务商设定百万输出 Token 单价 |

**注意**：Provider 未必返回准确 usage，也未必允许插件设置真正的模型输出上限，因此本插件能做到“请求前拒绝预估超支”，**不能保证严格不超过服务商最终账单**。本插件只统计自身调用；正常聊天回复成本由 AstrBot/Provider 单独统计，不能混为一谈。Embedding 和媒体调用设独立预算，媒体图像与音频费用不是简单的文本 Token 计价。

## 个人画像和群权限

群内画像只基于该成员**自己的明确陈述**，不把他人猜测、转发或回复文字当成个人自述。未经本人许可，自动群内画像不会进入跨群全局画像。本人私聊机器人使用：

```text
/wm help
/wm profile add 喜欢简洁有依据的技术答复
/wm profile list
/wm profile delete <ID>
/wm share allow 123456789@chatroom
/wm share revoke 123456789@chatroom
/wm share list
/wm privacy off
/wm group off 123456789@chatroom
/wm group on 123456789@chatroom
/wm group erase 123456789@chatroom
```

仅在用户本人于已授权群触发模型问答时，读取其主动建立的全局画像，不为其他群成员读取。`group off` 停止形成该群成员画像，并停用已形成的本人画像；`group erase` 删除该群本人已归档原始消息及其成员画像，并默认退出画像收集。**群内公开讨论仍可被当作群公共事实记录，不能把机器人当作私人保密渠道**；本人未来新发言的普通群消息仍可能按当前群的归档策略存储。完全停止群存档须管理员撤销该群白名单/关闭采集。

请确保群成员已了解普通群聊会被本地存档，并按实际使用环境的隐私要求设置保存和清除策略。

## 数据和运行边界

- SQLite 路径：`StarTools.get_data_dir('astrbot_plugin_wechat_memory')/memory.sqlite3`；**只适合一个 AstrBot 进程使用同一个数据库**。备份在线数据库请使用 SQLite backup API，而不是在 WAL 正写入时只拷贝 `.sqlite3`。
- `raw_messages` 为独立档案，Dreaming 成功不会删除记录；不设自动到期。系统存储容量由管理员管理。极端超长记录只保留 `oversized/skipped` 元信息，不假装文本已完整收录。
- `dream_runs` 保存运行状态；`room_state.dream_cursor` **只在本次批次成功入库后推进**；模型失败或版本冲突保持待处理并退避。当前本地 SQLite lease 用于防止同时执行；不支持多节点分布式运行。
- `working_snapshots` 是数据库内重建缓存，不是模型每次必须加载的整份大画像。管理员人工修改后会刷新关联快照；Snapshot 没有秘密跨群提权能力。
- 原始群聊、模型提案、媒体摘要都是**不可信内容**。`on_llm_request` 只注入本轮临时文本，不允许修改 AstrBot 的 Soul、配置或工具。
- 数据删除与历史版本关系复杂：管理界面“删除记忆”默认是软删除，保留管理员审计历史；本人群消息清除会清理归档原文及已知关联候选，但涉及其他成员共同讨论的公共事实可能需要管理员复核。删除所有痕迹需要停用存档、检查备份并执行实际数据库清除策略。

### 可选 M6

**Hook Inbox 补录（不使用 AstrBot 原生聊天历史）**：启用 `backfill_enabled`，选择当前微信账号与群并点击“从 Hook Inbox 补录”。插件使用 `Context.get_platform_inst()` 和 Hook 连接器的 `get_task_scope()` 获取真实 `inbox_db` 路径及所属微信账号，直接只读导入当前白名单群的 Hook 原始事件。默认不需要配置 `hook_inbox_db`，特殊部署可手动指定。使用消息 ID 去重、按群保存独立补录游标；不会修改 Hook 的数据库、采集开关或消息发送。**只能补录 Hook 连接器已经接收并保存的历史事件；Hook 开始采集前的微信历史不会从 Inbox 凭空恢复。**

**向量检索**：设置 `embedding_enabled=true`、`embedding_api_url`、`embedding_model`；密钥放入 AstrBot 运行环境变量 `WECHAT_MEMORY_EMBED_API_KEY`，不写入群聊或数据库。向量索引保存在 SQLite，索引重建通过 Dreaming 成功后触发，查询可能产生额外请求费用。

**媒体理解**：设置 `media_enabled=true`、`allowed_media_roots`、`media_api_url` 和视觉或音频模型名称，环境变量 `WECHAT_MEMORY_MEDIA_API_KEY`。文件必须在许可目录内。视觉仅描述图像可见内容；视频**只截取第一秒附近一帧**，不能声称理解整段视频。媒体派生信息不会直接作为成员的确定画像，需复核；当前 Hook 适配器未提供有效媒体绝对路径时无法处理。

## 开发资料

- `PLAN.md`：分 M0–M6 记录实现范围、上线门槛和人工验收清单。
- `main.py`、`identity.py`：原生 AstrBot 生命周期、可信身份、普通群聊事件。
- `storage.py`：SQLite 原始记录、来源、版本、快照、调度水位、额度、权限与审计。
- `extraction.py`、`evolution.py`：LLM 提案与严格事务提交。
- `scheduler.py`：每日按群 Dreaming、失败恢复、输入预算。
- `retrieval.py`、`embeddings.py`：本地/可选向量检索和上下文选择。
- `backfill.py`、`media.py`：经管理员启用的补录与媒体理解。
- `panel.py`、`views/memory/`：插件原生工作台。

研究参考（不引入第三方运行时）：[openJiuwen Memory](https://github.com/openJiuwen-ai/agent-memory)、[JiuwenSwarm](https://github.com/openJiuwen-ai/jiuwenswarm)、[OpenDream](https://github.com/pylit-ai/opendream)、[Hermes Dreaming](https://github.com/alejandroiglesias/hermes-dreaming)。

**验收**：根据先前项目约束，本次不编写任何测试代码；只进行语法/结构静态检查。是否能在实际 AstrBot 版本中安装、能否投递 Hook 媒体、Provider JSON 及 Dashboard bridge 是否兼容，都必须在测试群进行人工验收后才能判断生产可用。
