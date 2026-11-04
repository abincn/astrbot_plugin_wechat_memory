# AstrBot WeChat Memory — v0.3.0-alpha.3

独立的 AstrBot 长期记忆插件。运行链：PC 微信 Hook → [astrbot_plugin_wechat_hook](https://github.com/abincn/astrbot_plugin_wechat_hook) → AstrBot → 本插件。**不需要 Hermes、OneBot 或微信 Agent 插件**；不接管真实微信消息发送。

> **发布状态：源码实现 / 开发预览。** 已进行语法静态检查，**未在真实 AstrBot、PC 微信 Hook、Provider、Dashboard 环境中完成运行验收**。首次安装只在测试群使用，数据库备份和 API 费用须自行管理。当前实验版支持 v0.2.2 SQLite 数据库增量升级至 Schema v4；只做旧库原位扩展，不重建原始聊天表。升级前须完成一致性备份，并先在测试副本上验证。

## v0.3.0-alpha.3 改进

### 话题关联补充（同一实验分支）

- V2 现在支持模型给新话题一个批内临时 `ref`，观察使用 `topic_ref` 引用同批新话题或授权群的已有话题 ID，提交时解析并写入 `observation_signals.topic_id`。
- 只有能与一个明确话题关联的全新 `group/topic_participation` 观察，才被并入话题证据；`ongoing_task` 等独立事项仍保留观察记录。
- 已有的历史观察不做自动删除或无证据语义合并，旧记录可能仍显示两条；新建记录可在观察列表看到归属话题。
- 此处实现的是**话题关联和部分冗余抑制**，不是通用语义去重或自动事件合并。



- 修复历史重分析期间 AstrBot 重启后任务永久停在 running 的问题：重启后转换为 paused，可在控制台继续。
- 预览结果不只展示计数：每次成功的 V2 预览会保存有来源 ID 的话题、观察和正式记忆提案，在“Dreaming 运行记录”中打开对应的 preview 记录可直接查看。
- “话题累计摘要”“成员与群观察”支持点开查看关联原始消息；成员退出画像或清除本人原文时，移除该群受影响的预览结果。
- 观察上下文额外包含已关联的证据条数；新批次优先复用既有 topic/observation 的 ID，实现跨批次补证据，而不是重复新增。
- Schema 正向增量升级到 **v4**（新增 `dream_run_previews` 表）；旧数据保留，**不能直接对已升级 DB 使用 alpha.2 回滚**，应从备份恢复。
- 本分支仍为未实机验收的实验版；不要在唯一生产实例直接试用。

## v0.3.0-alpha.3 Dreaming V2 实验分支

> **仅用于手动验收，不要直接升级唯一生产实例。** 功能默认关闭。升级数据库前必须使用 SQLite backup API 完成一致性备份。Schema 将从 v2/v3 迁移到 v4；旧插件不可直接对 v3 数据库回滚运行，降级须连同升级前备份恢复。

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
