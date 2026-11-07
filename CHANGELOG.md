## 0.4.0-beta.5-dev.14

- V3 对误用旧主张 ID 且主体类别、成员 ID 或主张类型不一致的提案单独拒收。其余通过校验的提案可继续处理；原主张不变，涉及的本批消息按现有流程标记待重评并显示具体不一致字段。

## 0.4.0-beta.5-dev.13

- V3 新消息和历史分析的失败记录保留请求、校验或提交等阶段、异常类型及可用的具体原因；后台日志附执行 ID，便于查找同一异常。
- 历史任务自动重试耗尽时保留最后一次失败原因。旧记录、重试条件和游标不变。

## 0.4.0-beta.5-dev.12

- V3 新消息、历史分析和画像巩固共用可配置的单批模型超时（默认 90 秒）；群租约随超时延长。
- 可选用 AstrBot 提供方的流式接口。收到完整响应后才校验和保存，继续使用单批总超时，不自动改走非流式请求。
- 未新增测试代码；真实提供方联调待完成。

## 0.4.0-beta.5-dev.11

- V3 分析和画像巩固共用紧凑输入：每种记录只声明一次列名；消息使用批次短号与相对发送时间，有结构化 @ 对象时才附上。程序将证据短号还原成原始 ID，数据库记录不变。
- AstrBot 插件配置新增两份可编辑的记忆提示词，留空使用默认文案；输出协议和证据校验由程序维护。
- 未新增测试代码；真实模型验收待完成。

## 0.4.0-beta.5-dev.10

- 画像巩固复用完整 V3 协议和模型调用，过滤带事件的旧主张；单批输入估算不超过 16000 Token，保留独立进度，不修改每日预算或增加重试。
- 失败记录区分请求、校验与提交阶段，保留具体校验原因和已取得的用量；未取得实际用量时保持未知。旧失败记录不改写。
- 未新增测试代码；必要静态检查通过，真实模型重试尚待验收。

## 0.4.0-beta.5-dev.9

- V3 对明确直引核对所选证据的正文和发言者，忽略空白与全半角差异；缺少原话时不提交该主张，有正确来源时仅保留支持引文的证据。延期记录显示具体原因，不增加模型调用。
- 画像证据显示在各自主张下，重复点击不叠加；核对后可直接撤回错误主张，保留原文和修订历史。
- 未自动修改已有主张，未新增测试代码；完成必要静态检查。

## 0.4.0-beta.5-dev.8

- 同一主体、类型、事件、归属、敏感度和有效期内，正文完全相同的主张直接补充证据，即使模型生成的谓词不同。近似措辞仍进入待审核，不按主题自动合并。
- 成员画像新增“整理重复主张”：对照两条原话后，由管理员明确选择保留哪条正文并合并证据，保留原文及修订历史。合并时校验当前群、双方版本、证据归属、披露范围和有效期。
- 未新增测试代码；仅完成必要静态检查，未修改部署数据库中的历史主张。

## 0.4.0-beta.5-dev.7

- 识别 Hook 展平的“仅接收内嵌元数据”应用卡片与同类通知。即使已变成普通文本，地址、令牌和对象字段也不会进入提取、引用或历史原文召回。
- 原始群聊与延期列表用简短标签展示这类记录，详情与数据库保留原文。原文召回复用提取前的内容整理，保留机器识别类型。
- 未新增测试代码；仅完成必要静态检查。

## 0.4.0-beta.5-dev.6

- 在模型调用前统一整理消息内容，图片/语音/视频/表情/文件 XML 外壳、合并转发和未知或无效 XML 进入待解析状态，不作为文本交给模型。
- 引用回复仅将本条标题作为发送者正文，引用作者/文本单独标注为未独立验证；链接只提供标题和描述，不传下载、解密及哈希属性。
- 已有媒体识别派生文本保留机器生成来源；服务器拒绝将机器生成或分享内容当作本人陈述。
- 新消息、历史重分析、画像巩固、辅助引用与旧版提取共用同一内容整理。仅有待解析附件的批次不预留文本调用预算。XML 不做普通文本分片，避免证据偏移错位。
- 保留原始消息、历史主张与证据，不重置生产游标；继续沿用现有媒体解析开关和附件路径，未新增下载或付费识图调用。
- 未新增任何测试代码。仅做必要编译与静态检查，真实 Provider 与部署数据库验收尚未完成。

## 0.4.0-beta.5-dev.5

- 原始群聊按发言时间、消息ID倒序分页，避免补录旧消息排到新消息之前；非分页入口同步使用此排序。
- 微信 XML 卡片在列表显示标题，引用回复只读取外层自己的标题，不将被引用内容当作发言正文；详情仍保留原文，不改写数据库与分析输入。
- 区分发言与归档时间，记录数量标注为已加载，ready 状态显示为已归档。
- 未新增测试代码；仅完成必要静态检查，实机展示待核对。

## 0.4.0-beta.5-dev.4

- 保持 0.4 引擎，新增从同一插件数据目录的 memory_vNext.sqlite3 补回原始消息的入口，不依赖 Hook Inbox。
- 只读来源并限定当前平台、账号与群；按消息 ID 去重，保留发言时间、原文和披露状态，每批500条，可继续导入。原文与补录进度一起提交，不改分析游标，不导入0.5画像与任务。
- Hook 补录页面显示原有返回说明与具体错误，避免仅显示新增0而看不到原因。
- 未新增测试代码；真实0.5数据恢复仍需在部署实例核对。

## 0.4.0-beta.5-dev.3

- 历史分析页同时展示分析执行记录与历史重分析任务，已有的新消息执行记录无需迁移即可看到。
- 分清新消息分析、预览、历史批次和画像巩固，保留各自的游标及任务语义。
- 点击分析新消息或预览后自动进入记录页，查询本次请求并反馈完成、无可分析文本、额度不足或失败。
- 执行记录支持分页与按 ID 查看详情，不将新消息分析伪装成历史重分析任务。
- 未新增测试代码；静态检查通过，真实环境验收待完成。

## 0.4.0-beta.5-dev.2

- 补齐已有成员昵称与群名片的只读查询和缓存；不再要求成员发新消息才取得名字。
- 人物列表、事实主体、原始消息作者、来源证据、称呼来源及运行详情中的人物统一优先显示名字。账号 ID 仍留在详情供核对。
- 复用当前账号 Hook 的群成员资料、昵称缓存、批量联系人接口及只读联系人数据库；无法取得时明确显示“昵称未取得”。
- 顶栏刷新同时更新群名与成员姓名；过滤把账号 ID 或缺失提示当作昵称的旧记录。
- 静态检查通过；未新增测试代码，未完成真实 Hook / Dashboard 验收。

## 0.4.0-beta.5-dev.1

- 群选择框通过当前账号的 Hook 只读联系人接口查询并缓存群名，支持刷新及同名群区分；群 ID 仍用于数据隔离。
- 最近错误、历史执行错误及任务阻塞原因统一为中文，兼容旧记录中的 CoverageError。
- 未新增测试代码；通过静态检查，真实 Hook / Dashboard 验收仍待完成。

## 0.4.0-beta.5-dev（本地开发预览，未实机验收）

新增人物称呼档案、用途分类及按人展示；修复容量缩批和解析错误；加入画像独立进度、长文本分片与延期重评、逐调用费用结算、调度层任务管理、分页搜索、一致快照 JSONL 导出和 V3 向量缓存。Schema 6 增量迁移；具体状态见 IMPLEMENTATION_STATUS.md。

# Changelog

## 0.4.0-beta.4 (2026-11-04)

- Resolve `Self statement requires same-author evidence` from model-proposed cross-member citations without discarding other valid claims.
- Accept first-person claims only from exact same-sender raw sources; reduce certainty for mixed-source results.
- Preserve single-author mislabelled self-report as explicitly source-attributed, unconfirmed third-party candidate; never present it as a direct self-report.
- Quarantine unsupported member attribution, behavior and speaker IDs; persist affected raw-message IDs as deferred for later historical reanalysis.
- Surface rejected-claim details in run UI and keep unresolved profile consolidation pending.
- No tests written per project request; live provider validation pending.

## 0.4.0-beta.3 (2026-11-04)

- Replace unreliable model-side reviewed_count arithmetic with a server-bound batch coverage key.
- Retain strict complete/deferred/source-message validation and avoid cursor advance on partial coverage.
- Add bounded retry with smaller batches for both preview and apply, with pause after repeated incomplete outputs.
- Align member-profile consolidation with the new protocol; previously failed replay jobs remain resumable.
- No test code added, real AstrBot / Provider acceptance pending.

## 0.4.0-beta.2 (2026-11-04)

- Fix V3 history preview failing with TypeError when no rows are excluded: the selected-prefix cursor used max(int) without an iterable.
- Fix V3 history range preparation still requiring the obsolete V2 switch.
- Rebuild the workbench with focused overview, profiles, events, claims and historical task pages; move legacy records and audit under advanced management.
- Expose progress, per-task failure reasons, source evidence, and review operations without raw JSON as the primary interface.
- Group 48 previously flat AstrBot configuration fields into nine essentials and six advanced sections; migrate persisted flat settings once without changing their values.
- Retain media actions and extend current-group JSON exports to include V3 events, claims and analysis coverage.
- Add sanitized error descriptions to run/history records and stack trace in administrator service logs.
- No test code added; static front-end/config validation completed, live AstrBot/manual acceptance pending.


## 0.4.0-beta.1 (2026-11-04)

- Rebuilt Dreaming core around V3 Event / Claim / Evidence / Profile, rather than V2 parallel observations and changes.
- Added per-source analysis ledger, dynamic token-bounded batches, independent cross-day profile consolidation, V3 replay and preview details.
- Added group-scoped V3 dashboard, claim evidence and reviewer operations, migration to schema 5 and consistent pre-V3 backup.
- Preserved old raw archive, V2 history, manual memory records and historical production cursor; old messages require deliberate history reanalysis.
- Syntax/static review only; live AstrBot/Hook/provider acceptance remains outstanding. No test code added.


## 0.3.0-alpha.2 (2026-11-04)

- Experimental V2 topic and observation extraction behind an opt-in switch.
- Transactional SQLite Schema v3 migration, preserving existing raw data and cursors.
- Persistent historical reanalysis jobs with independent progress, preview/apply and controls.
- Correct revision approvals; invalidate relevant derived data on member opt-out/erase.
- This branch has not been verified in a live AstrBot/Hook/provider deployment. Back up before use.

## 0.2.2 (2026-11-03)

- Fix Dreaming panel buttons blocked by sandboxed native confirmation dialog.

## 0.2.1 (2026-11-03)

- Discover read-only Hook Inbox path through the running adapter and improve backfill diagnostics.
