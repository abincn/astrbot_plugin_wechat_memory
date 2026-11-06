## 0.5.0-alpha.1

- 按功能重建最小内核，独立 Schema 1 数据库，旧库只读导入。
- 统一新消息、历史、延期和画像的模型协议、持久任务、费用与提交路径。
- 重建工作台和回复前检索，保留来源、权限、人工修订及隐私清除。
- 删除旧引擎、存储门面、迁移兼容、向量与主动媒体解析。
- 未新增测试代码；真实环境联调尚未完成，仅作预览。

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
