"""Translate current and historic operational diagnostics for the workbench."""
GENERIC_ERROR='处理未完成：返回内容、参数或记录版本不符合要求，请检查执行记录后重试。'
MESSAGES=(
    ('direct_quote_not_in_source','引号中的原话未出现在所引用消息的正文中，未关联该证据。'),
    ('Unexpected V3 protocol','模型没有返回 schema_version=3 的 JSON 对象。'),
    ('Invalid claim event link','模型引用了本批未提供的事件，或事件引用字段类型无效。'),
    ('Unknown claim','模型引用了本批未提供的主张 ID。'),
    ('Stale claim revision','模型返回的主张版本与输入不一致。'),
    ('Stale V3 claim revision','主张在模型调用期间被修改，请刷新后重试。'),
    ('Provider did not return plain V3 response','模型返回了工具调用或非助手消息。'),
    ('EmptyModelOutputError','提供方没有返回可用的完整模型结果。'),
    ('Incomplete V3 coverage','模型未完整处理本批消息，需要缩小批次后重试。'),
    ('V3 proposal at limit','本批提取结果达到容量上限，需要缩小批次后重试。'),
    ('V3 proposal limit exceeded','本批提取结果超过容量上限，需要缩小批次后重试。'),
    ('Model did not echo this batch coverage key','模型返回的批次标识不匹配，需要重新分析。'),
    ('Missing V3 complete flag','模型未返回完整性标记，需要重新分析。'),
    ('Invalid V3 coverage','模型返回的消息覆盖信息无效，需要重新分析。'),
    ('V3 reviewed message IDs do not cover','模型返回的消息范围不完整，需要缩小批次后重试。'),
    ('CoverageError','模型返回的批次覆盖信息不完整，需要缩小批次后重试。'),
    ('TimeoutError','模型或上游服务响应超时，请稍后重试。'),
    ('ConnectionError','无法连接上游服务，请检查连接后重试。'),
    ('JSONDecodeError','模型返回的内容不是有效的 JSON 数据，请重新分析。'),
    ('Group disabled during Dreaming','分析期间插件或群已停用，本批结果未提交。'),
    ('Consolidation disabled before commit','画像巩固期间权限已变化，本批结果未提交。'),
    ('Lost group Dreaming lease','当前群的任务执行权已失效，本批结果未提交。'),
    ('lease lost','任务执行权已失效，本批结果未提交。'),
    ('No provider configured','尚未配置分析模型。'),
    ('First message plus existing context exceeds','首条消息及上下文超过输入预算，请使用历史分片重评或调整预算。'),
    ('interrupted_restart','任务因服务重启而暂停，可从已提交进度恢复。'),
    ('interrupted_shutdown','任务因服务停止而暂停，可从已提交进度恢复。'),
    ('upstream_transient','上游服务暂时不可用，正在退避等待。'),
    ('auto_attempt_limit','该消息已达到自动重评次数上限，请人工预览处理。'),
    ('budget_exceeded','今日模型调用或用量额度不足。'),
    ('busy','当前群有其他任务正在运行。'),
    ('oversize','消息或上下文过长，请使用历史分片重评。'),
    ('insufficient','待分析消息尚未达到处理门槛。'),
)


def user_error(value, *, keep_detail=False):
    text=str(value or '')
    kind=type(value).__name__ if isinstance(value,Exception) else ''
    if not text and not kind:return ''
    if text.startswith(('V3 分析失败 · ','新消息分析失败 · ','画像巩固失败 · ','历史重分析失败 · ')):return text
    for key,message in MESSAGES:
        if key in text or key in kind:return message
    if any('\u4e00'<=c<='\u9fff' for c in text):return text
    if keep_detail:return (text or kind)[:400]
    return GENERIC_ERROR


def task_error(task,stage,exc):
    status=getattr(exc,'status_code',None)
    detail=f'提供方返回 HTTP {status}' if type(status) is int else user_error(exc,keep_detail=stage!='请求模型')
    if detail==GENERIC_ERROR:detail='请查看 AstrBot 后台日志中的异常堆栈。'
    return f'{task}失败 · {stage}（{type(exc).__name__}）：{detail}'


def localize(value):
    if isinstance(value,list):return [localize(item) for item in value]
    if isinstance(value,dict):
        return {key:user_error(item) if key in ('error','last_error','blocked_reason','reason') and isinstance(item,str)
                else localize(item) for key,item in value.items()}
    return value
