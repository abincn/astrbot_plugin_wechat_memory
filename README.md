# astrbot_plugin_wechat_memory

**微信长期记忆**：独立的 AstrBot 插件，支持 PC WeChat Hook 普通群聊采集、按群长期记忆、群成员画像、LLM 批量提取、可控 Token 预算、可追溯的冲突审核、用户明确授权的跨群个人画像以及 AstrBot 内置可视化工作台。

- 运行链路：**PC Hook → `abincn/astrbot_plugin_wechat_hook` → AstrBot → 本插件**。
- 不使用 Hermes、不依赖 `abincn/astrbot_plugin_wechat_agents`、不需要 OneBot。
- 不修改 Hook、微信 DLL、现有 Inbox、AstrBot 人格和真实发送开关。
- 仅使用 Python 标准库、SQLite 和 AstrBot API；不安装新第三方运行依赖。
- 当前源码为 **v0.1.0 开发预览**；已经完成语法静态检查，**未在真实 AstrBot + 微信环境中验证**。

## 兼容性

插件最低目标 AstrBot `>=4.24.0`（用于临时的 `TextPart.mark_as_temp()`）。可视化页面使用较新的 AstrBot 插件 Views 和 `AstrBotPluginView` / `register_web_api`。部署时推荐与 `abincn/astrbot_plugin_wechat_hook` 的现行版本和 AstrBot v4.29.x 配合；这只是接口适配目标，不表示完成实机验证。

## 安装

1. GitHub 建议仓库名：`abincn/astrbot_plugin_wechat_memory`（仓库创建后可以直接通过 AstrBot 后台的插件安装界面输入该链接）。
2. 未发布到 GitHub 前，将安装包 ZIP 解压至 `AstrBot/data/plugins/astrbot_plugin_wechat_memory`，确保 `main.py` 和 `metadata.yaml` 直接位于该目录。
3. AstrBot 插件后台 → **微信长期记忆** → 配置：
   - `enabled`：打开。
   - `platform_id`：默认 `wechat_pc_hook`，若你使用了其他实例 ID，填该平台实例 ID。
   - `allowed_rooms`：输入允许采集的真实 `群ID@chatroom`，默认空；建议先只启用一个群。
   - `collection_enabled` 和 `retrieval_enabled`：默认开；它们在 `enabled=false` 时不起作用。
   - `extraction_enabled`：默认关；开前必须选好 `extraction_provider_id`，否则不请求模型。
   - `admin_users`：填入允许管理工作台的 AstrBot Dashboard 用户名，例如 `admin`（以你实际账户名称为准）。空名单禁止访问后台管理 API。
4. 保存配置并**完整重启 AstrBot**。首次启动将在标准插件数据目录生成 `memory.sqlite3`。
5. 打开插件详情页 → **可视化视图 → 微信长期记忆**。

无消息时工作台尚无群可选；先用启用的群发送一条纯文本消息，刷新页面即可。

## 工作原理

```
普通群消息（即使没有 @机器人）
    → AstrBot GROUP_MESSAGE 事件
    → 按平台实例/登录账号/群/消息ID进行幂等落库
    → 定时/消息阈值/显式记忆语句触发提取
    → AstrBot Context.llm_generate（空工具集）
    → 严格 JSON / 来源ID / 群和成员校验
    → 长期记忆（active / review / superseded / deleted）

用户触发 AstrBot LLM
    → 仅检索当前群记忆 + 当前用户的本群画像
    → 如果本人授权，附加本人全局画像
    → TextPart.mark_as_temp() 追加到本轮动态上下文
```

提取器不会调用 MCP 或群消息发送工具，不会修改 Soul。普通未 @消息只是进入持久化队列，不会触发普通聊天模型的回复。提取失败只影响记忆流程，不阻止普通回复。

### 默认成本限制（每群）

| 参数 | 默认 |
|---|---:|
| 检查间隔 | 300 秒 |
| 消息数触发 | 20 条 |
| 最长等待 | 60 分钟且至少 3 条 |
| 单批 | 至多 50 条、6000 字符 |
| 每日模型调用次数 | 12 次（失败也计入） |
| 单条采集上限 | 1600 字符；过长会被跳过并保持在 Hook Inbox |
| 动态召回 | 至多 6 条、2400 字符 |

明确的“记住这个”等语句会触发优先处理，但**仍受每群每日 12 次的硬上限约束**。额度按 UTC 日期归档；若模型结果未给 Token usage，则统计使用字符数粗估，不代表精确账单。

## 跨群个人画像：必须本人授权

**默认没有任何跨群画像共享。** 群聊天自动提取的 `profile` 只存当前群。全局画像只能由微信用户本人在机器人私聊中创建，再由本人明确批准目的群。

私聊命令（仅在配置 `enable_private_profile_commands=true` 时）：

```text
/wm help
/wm profile add 喜欢简洁直接的回答
/wm profile list
/wm profile delete <记忆ID>
/wm share allow 123456789@chatroom
/wm share revoke 123456789@chatroom
/wm share list
/wm privacy off
```

`share allow` 的群必须已在 `allowed_rooms` 中。共享只用于**用户本人在该群触发的 AI 回复**，不会向群其他成员开放。私聊命令的确认回复仍通过现有 Hook 连接器，遵守其 `send_enabled` 设置；关闭真实发送时可能只生成草稿。

插件目前没有自动跨群身份合并：必须是同一 `platform_id`、同一个登录微信账号 `self_id`、同一微信用户 ID。不同微信登录账号之间默认隔离。

### 工作台能力

- 展示已观测群、排队数、有效记忆、待审核数、每日调用量/估算 Token。
- 按群检索、筛选、手工新增、编辑、批准冲突替代旧记忆、停用、删除。
- 查看来源消息 ID 与发送者；导出本群最近最多 200 条记忆（不包含跨群画像）。
- 暂停或恢复指定群的后台提取。

所有管理操作必须经 AstrBot Dashboard 鉴权且用户名存在于 `admin_users`；后台管理**没有**将任何用户画像变成跨群共享的权限接口。用户撤销授权不删除原画像，删除需要该用户自己执行。

## 数据目录

位于 `StarTools.get_data_dir('astrbot_plugin_wechat_memory')/memory.sqlite3`。卸载/升级插件文件时，不要删除 `data/plugin_data/astrbot_plugin_wechat_memory/`。数据库使用 WAL，在线备份应使用 SQLite backup API 或先停止 AstrBot 再拷贝（含必要的 WAL 数据），不要只拷贝一个正在写入的 `.sqlite3` 文件。

## 已知限制

- 只采集连接器实际投递给 AstrBot 的**纯文本 Plain** 消息。图片、视频、音频、文件正文和无法获得的媒体不能被冒充为已理解。
- 未实现完整旧微信历史补录；需要的话在下一版通过 Hook Inbox 的只读补偿接口添加。
- 检索为轻量中文关键词 / 双字片段匹配，不是 Embedding 语义搜索；无需向量数据库。
- 自动提取和冲突判定不是事实验证；`review` 状态在管理员批准前不注入。
- 高敏感数据有保守的文本过滤，但过滤不能代替用户授权和管理员的数据治理。
- 默认不保证没有消息丢失：极长文本、非文本消息、消息队列积满、插件尚未运行时的历史事件不会自动补录；当已进入记忆队列时重启可恢复未完成批次。
- 多进程同时访问同一数据库的调度租约未实现；**只支持单 AstrBot 进程管理一份本地插件数据库**。
- 模型输出 token 上限不保证由所有 Provider 强制执行；当前通过输入字符、候选条数、响应文本长度与每日调用次数控制风险。
- 未添加测试代码，遵循用户当前项目约束。已经通过 Python 语法与 JavaScript 语法静态检查；实机安装、Provider 兼容、消息事件行为、Dashboard 权限、私聊发送需自行验收。

## 开发资料

- `PLAN.md`：A–E 阶段和手工验收清单。
- `metadata.yaml` / `_conf_schema.json`：安装信息与插件配置。
- `main.py`：AstrBot 事件钩子和私聊个人画像命令。
- `identity.py` / `policy.py`：可信身份及输入边界。
- `storage.py`：SQLite、并发安全、群隔离、审计、来源关联、授权。
- `scheduler.py` / `extraction.py`：限额调度及只读模型提取。
- `retrieval.py`：动态记忆召回。
- `panel.py` / `views/memory/`：Dashboard 管理 API 与可视化页面。

## 上游项目

- https://github.com/abincn/astrbot_plugin_wechat_hook
- https://github.com/Bluezeamer/astrbot_plugin_memory （设计参考，不直接复制）
- https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- https://docs.astrbot.app/dev/star/guides/ai.html
- https://docs.astrbot.app/dev/star/guides/plugin-pages.html
