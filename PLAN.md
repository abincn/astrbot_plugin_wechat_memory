# PLAN — 微信群长期记忆独立插件（A–E 实施版）

> v2.0 · 2026-11-03 · 仓库建议 `abincn/astrbot_plugin_wechat_memory` · 状态：本地源码已实现，静态语法检查通过；真实 AstrBot/Hook/Provider 部署尚未验收。

## 0. 固定边界与开发要求

- 运行链路：PC 微信 Hook → `abincn/astrbot_plugin_wechat_hook` → AstrBot → 本独立插件。
- 暂不使用 Hermes、`abincn/astrbot_plugin_wechat_agents` 或 OneBot v11 中间层；不修改现有 Hook/Inbox 或微信真实发送逻辑。
- 不覆盖 AstrBot 原生人格、Soul、其他插件及 AI 工具设置。
- Python 标准库 + SQLite + 官方 AstrBot 接口；按功能拆分、最小实现、最小依赖。
- **未经明确要求禁止编写任何测试代码（包括临时、内联、自动化测试）**。本次仅进行源码静态检查；本地检查不代表运行验收。
- 默认禁用；只采集配置白名单中的可信微信消息，跨群共享默认关闭。
- 当前生成的是本地源码，**尚未上传 GitHub、尚未部署生产**；新仓库创建后才能上传。

## 1. 身份及三层记忆模型

```
(platform_id, owner=self_id, room=group_id)                    群公共记忆
(platform_id, owner=self_id, room=group_id, user_id=sender)    群内成员画像
(platform_id, owner=self_id, user_id=sender, room='')           个人全局画像

跨群画像使用时额外要求 profile_permissions(owner,user_id,destination_room)=allowed。
```

- 群公共事实与群内成员画像必须留在当前群；个人全局画像仅由本人在私聊添加。
- 用户本人通过 `/wm share allow 群ID@chatroom` 和 `/wm share revoke ...` 授权目的群。授权范围仅限**本人在目的群向机器人发起的请求**，不得在其他群成员问答中检索出来。
- 不允许模型决定扩大群 ID、授权或成员身份；所有身份来自 AstrBot 可信事件字段。
- 全局画像与群对话原文彻底分离：共享事实，不共享原始群历史；私密信息及高风险数据拒绝写入。

## 2. 文件与责任拆分

| 文件 | 职责 |
|---|---|
| `main.py` | 普通群事件采集、LLM 请求时注入、本人私聊授权命令、生命周期 |
| `identity.py` | 校验平台、登录账号、群 ID、成员与消息 ID；只取真实 Plain 文本 |
| `policy.py` | 类型、内容边界、敏感项筛选、主题标识、中文检索词切分 |
| `storage.py` | SQLite 持久化、单进程并发锁、数据隔离、批次租约、审计、授权和删除 |
| `scheduler.py` | 5 分钟批次检查、阈值、失败退避、每日调用次数限制 |
| `extraction.py` | AstrBot Provider 的无工具 JSON 事实提取 |
| `retrieval.py` | 当前群与本人获授权画像的有限召回及临时动态上下文 |
| `panel.py` + `views/memory/` | Dashboard 身份检查、管理 API、可视化列表与操作 |
| `_conf_schema.json` | 插件配置及默认关停策略 |

数据目录：`data/plugin_data/astrbot_plugin_wechat_memory/memory.sqlite3`（由 `StarTools.get_data_dir()` 定位）。

## 3. 阶段 A — 普通聊天采集与可信身份（源码已实现）

- [x] 独立 AstrBot `Star` 插件，配置默认 `enabled=false`，群白名单默认空。
- [x] `@filter.event_message_type(GROUP_MESSAGE)` 监听不触发 LLM 的普通群聊天。
- [x] 仅读取真实 Plain 消息段，不把媒体标题、损坏附件视为已理解内容。
- [x] 按 `(platform, owner, room, message_id)` 唯一约束去重入队。
- [x] SQLite WAL + 事务 + 进程内短锁；`processing` 状态在启动时恢复为待处理。
- [ ] 实机验收：没 @机器人的普通群消息是否到达；Hook 停用或特殊消息过滤时的观测差异。

## 4. 阶段 B — 检索与上下文注入（源码已实现）

- [x] 群公共记忆、群内成员画像、全局个人画像的独立范围。
- [x] 基于中文连续词、双字片段的轻量检索；限制召回 6 条 / 2400 字。
- [x] `on_llm_request` 只根据当前可信群/用户加载数据，并以 `TextPart.mark_as_temp()` 注入本轮输入。
- [x] 提供手工新增、来源查询、编辑、停用与删除方法。
- [ ] 实机验收：当前 AstrBot 版本、Provider 请求是否正确接收临时 TextPart；切换群不会混用数据。

## 5. 阶段 C — 自动提取与成本控制（源码已实现）

- [x] 默认 5 分钟检查，每群 20 条或最长 60 分钟（至少 3 条）触发。
- [x] 最多 50 条、6000 字符每次请求；用户明确“记住”时高优先级触发。
- [x] 每群每天 12 次上限（包括失败），所有普通消息仍可留在待处理队列。
- [x] 使用指定 `extraction_provider_id` 通过 `context.llm_generate(tools=ToolSet())` 只读模型调用。
- [x] 解析限定格式 JSON、校验真实消息 ID、限制 8 个候选及成员作者身份。
- [x] 失败退避并保留待处理消息；历史结果通过内容指纹防止重复落库。
- [ ] 实机验收：模型输出兼容性、Token 统计来源/估算、重启恢复和额度上限。

## 6. 阶段 D — 巩固、冲突与跨群用户授权（源码已实现）

- [x] 相同内容指纹合并证据；同群同主题新旧事实冲突标记 `review`。
- [x] 管理员批准冲突时旧记录标记 `superseded`，保留版本审计。
- [x] 用户在机器人私聊 `/wm profile add` 自己创建全局画像，`/wm share allow|revoke` 按目的群授权。
- [x] 用户自己查询、删除全局画像以及撤销全部授权；默认无全局读取。
- [x] 敏感内容的基本过滤；不能由模型写入全局身份权限。
- [ ] 实机验收：授权/撤销实时生效、不同微信账号隔离、其他群成员无法读取私人画像。

## 7. 阶段 E — WebUI 和检索体验（基础版本已实现）

- [x] AstrBot 官方 Views：`views/memory/index.html`，通过 `AstrBotPluginView` 桥接。
- [x] `context.register_web_api` 后端；Dashboard 鉴权 + 明确 `admin_users` 用户名白名单。
- [x] 按群切换、今日 Token/调用统计、筛选、手工新增/编辑、审核、停用/删除、来源查看。
- [x] 支持暂停/恢复后台提取、导出最近 200 条本群记录。
- [x] 中文词片段召回，无额外向量库成本。
- [ ] 实机验收：所用 AstrBot Dashboard 的插件视图、安全桥接、登录权限与浏览器交互。
- [ ] **未实现的增强项**：真正的 Embedding/向量语义搜索、已授权媒体理解、旧历史补录和批量数据迁移。它们不是当前 E 阶段已完成能力；需要额外的模型与明确授权后单独开发。

## 8. 数据表

| 表 | 用途 |
|---|---|
| `pending` | 接收、去重、排队、批次租约、失败重试 |
| `memories` | 群事实、群内成员画像、经用户主动创建的全局个人画像及状态 |
| `memory_sources` | 将每条候选记忆绑定真实原始消息 ID |
| `memory_versions` | 管理员审核、编辑、替代、删除的历史记录 |
| `memory_usage_daily` | UTC 日期按群计入模型调用次数和 Token |
| `room_options` | 群级暂停/恢复 |
| `profile_permissions` | `(平台,账号,本人用户,目的群)` 共享授权 |

数据表中 `status`：`active / review / superseded / deleted`。自动模型候选未经来源校验不得落库。`review` 不自动进入回复上下文。

## 9. 消耗示例（每日 200 条纯文本）

以平均每条 45 Token、12 次调用、每次固定提示词 700 Token、平均输出 150 Token 估算：

- 消息正文输入：200×45=9,000 Token。
- 固定提示词输入：12×700=8,400 Token。
- 输出：12×150=1,800 Token。
- 合计约 **19,200 Token/天/群**（约 576,000 Token/月/群）。

上述是预算估算，未包含正常 AstrBot 会话模型调用、额外上下文、图片/音视频，也不代表各模型的实际 Token 统计。代码只将部分 Token 记录为模型 usage 或字符估算；每日限制是**调用次数硬限额**，不是精确 Token 硬限额。

## 10. 人工验收清单（不创建测试文件）

1. 启动时默认不采集；启用群白名单后，发送普通未 @文本，待处理数增加；非名单群不增加。
2. 相同 `message_id` 重复送达不重复排队；媒体失败标记不得进入长期事实。
3. 插件重启后数据库存在、积压保留；禁用插件不影响 Hook 收发。
4. 为群 A 手动建立记忆后，群 A 查询命中、群 B 不命中；用户 A 的群内画像不会成为用户 B 的全局画像。
5. 启用提取 Provider 后，达到阈值才调用模型；空闲不调用；失败重试；跨日期额度重新计数。
6. 明确“记住这个”能优先触发，但仍遵守每日调用次数限额。
7. 注入仅发生在 LLM 请求，来源内容不更改 AstrBot Soul 或模型工具能力。
8. 机器人私聊中本人建立画像 → 授权群 A → 仅本人在群 A 的提问可召回；撤销即停止；群 B 不命中。
9. 工作台需要 Dashboard 登录且用户名在 `admin_users` 中；非授权管理员用户被拒绝；只有当前群数据被导出。
10. 冲突记忆待审核、批准后旧版标记失效；删除后不会被召回。

## 11. 上线限制与后续

- 当前仅完成**本地源码与语法检查**，未部署真实运行环境，故上面带 `[x]` 的含义是**对应代码已实现**，不是已经通过实机验收。
- 不支持两台 AstrBot 进程同时共享同一个记忆 SQLite 文件运行调度器。
- 不处理视频画面、语音转录、图片及文件正文；不建立未经用户授权的跨群人格关联。
- 如果队列满或消息超长，原始记录仍在 Hook Inbox；本版未实现自动从原始 Inbox 补录，所以不能承诺绝对无遗漏。
- GitHub 仓库创建后再上传；发布仓库前不要宣称生产可用。
