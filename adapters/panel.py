"""HTTP boundary: authenticate, validate once, dispatch to feature methods."""
import asyncio
import json
import logging
from astrbot.api.web import request, json_response, error_response
from ..core.types import Scope, Failure, error_data, ident
from ..features.memory import PURPOSES
from .contacts import Contacts

log = logging.getLogger(__name__)
OPERATIONS = {
    'scopes','overview','messages','members','claims','events','tasks','task','calls','aliases','sources',
    'links','versions','audits','deferred','submit','control','manual_add','claim_edit','alias_add','alias_edit',
    'link_resolve','disclose','pause','privacy','import','export','contacts',
    'event_sources','event_edit',
}


def validate(payload):
    if not isinstance(payload, dict) or payload.get('operation') not in OPERATIONS:
        raise Failure('invalid_input', '未知的 operation，请刷新工作台')
    op = payload['operation']
    for field in ('platform','owner','room','query','member','id','task_id','content','name','path','cursor'):
        if field in payload and not isinstance(payload[field], (str, int) if field=='cursor' else str):
            raise Failure('invalid_input', f'{field} 类型无效')
    for field in ('limit','revision','start','end','raw_id'):
        if field in payload and (type(payload[field]) is not int or payload[field]<0):
            raise Failure('invalid_input', f'{field} 必须为非负整数')
    for field in ('preview','paused','opted_out','erase','restricted'):
        if field in payload and type(payload[field]) is not bool:
            raise Failure('invalid_input', f'{field} 必须为布尔值')
    required = {
        'task':('task_id',), 'control':('task_id','action'), 'sources':('id',), 'event_sources':('id',),
        'event_edit':('id','revision','content'),
        'manual_add':('content',), 'claim_edit':('id','revision'), 'alias_add':('member','name'),
        'alias_edit':('id','revision','status'), 'link_resolve':('id','revisions','action'),
        'disclose':('raw_id','disclosure'), 'pause':('paused',), 'privacy':('member','opted_out'),
    }
    if any(field not in payload for field in required.get(op, ())):
        raise Failure('invalid_input', '缺少必填参数：'+','.join(required[op]))
    enums = {'purpose':PURPOSES, 'kind':('new','history','profile','deferred'),
             'disclosure':('private','public','restricted'), 'alias_kind':('admin','alias')}
    for field, values in enums.items():
        if field in payload and payload[field] not in values:
            raise Failure('invalid_input', f'{field} 可选值为：'+','.join(values))
    if op=='claim_edit' and payload.get('status','active') not in ('active','review','superseded','deleted'):
        raise Failure('invalid_input','记忆状态无效')
    if op=='alias_edit' and payload['status'] not in ('confirmed','pending','retracted'):
        raise Failure('invalid_input','称呼状态无效')
    if op=='control' and payload['action'] not in ('resume','pause','cancel'):
        raise Failure('invalid_input','任务操作无效')
    if op=='link_resolve' and (payload['action'] not in ('keep_both','merge','replace') or not isinstance(payload['revisions'],list) or len(payload['revisions'])!=2 or any(type(r) is not int for r in payload['revisions'])):
        raise Failure('invalid_input','关联操作或版本无效')
    if op=='submit' and payload.get('kind')=='profile' and not payload.get('member'):
        raise Failure('invalid_input','画像任务必须指定成员ID')
    if op=='submit' and payload.get('kind')=='deferred' and 'raw_id' not in payload:
        raise Failure('invalid_input','延期重评必须指定消息内部ID')
    if payload.get('start',0)>payload.get('end',2**63-1):
        raise Failure('invalid_input','起始位置不能大于终点')
    if not 1<=payload.get('limit',50)<=100:
        raise Failure('invalid_input','分页条数必须为1至100')
    return payload


class Panel:
    def __init__(self, context, app):
        self.context, self.app, self.active = context, app, True
        self.contacts = Contacts(context, app.identity, app.archive)
        context.register_web_api('/astrbot_plugin_wechat_memory/manage', self.handle, ['POST'], '微信群长期记忆工作台')

    async def handle(self):
        actor = getattr(request, 'username', None)
        if not self.active or actor not in self.app.config.get('admin_users', []):
            return error_response('请使用已授权的 Dashboard 账号登录', status_code=403)
        try:
            body = await request.body()
            if len(body)>65536:
                raise Failure('invalid_input','请求超过64KB限制')
            p = validate(json.loads(body))
            result = await self.dispatch(p, str(actor))
            if p['operation'] not in ('scopes','contacts') and isinstance(result, dict) and 'items' in result:
                scope = Scope(p['platform'],p['owner'],p['room'])
                names = self.app.identity.names(scope)
                for row in result['items']:
                    for field in ('subject','sender','member'):
                        if row.get(field):
                            row[field+'_name'] = names.get(row[field],row[field])
            return json_response({'data':result}, headers={'Cache-Control':'no-store'})
        except Exception as exc:
            log.exception('工作台请求失败 actor=%s', actor)
            return json_response({'error':error_data(exc)}, headers={'Cache-Control':'no-store'})

    async def dispatch(self, p, actor):
        app, op = self.app, p['operation']
        platform = app.config.get('platform_id','wechat_pc_hook')
        if op=='scopes':
            adapter = self.context.get_platform_inst(platform)
            binding = getattr(adapter,'get_task_scope', None)
            if binding:
                info = binding()
                for room in app.config.get('allowed_rooms', []):
                    app.archive.ensure_room(Scope(platform, info['owner'], room))
            return [{key:r[key] for key in ('scope','platform','owner','room','name')}
                    for r in app.archive.scopes() if r['room'] in app.config.get('allowed_rooms', []) and r['platform']==platform]
        if p.get('platform')!=platform or p.get('room') not in app.config.get('allowed_rooms', []) or not p.get('owner'):
            raise Failure('scope_denied','当前账号或群未获授权')
        scope = Scope(platform, p['owner'], p['room'])
        app.workbench.room(scope)
        limit, cursor, query = p.get('limit',50), p.get('cursor',''), p.get('query','')
        if op=='contacts':
            return await self.contacts.refresh(scope)
        if op=='overview':
            return app.workbench.overview(scope)
        if op=='messages':
            return app.archive.list(scope, int(cursor or 0), query, limit)
        if op=='members':
            return app.memory.members(scope, app.identity.names(scope), query, str(cursor), limit)
        if op=='claims':
            return app.memory.list(scope, str(cursor), query, p.get('member',''), limit)
        if op in ('events','links','versions','audits','deferred'):
            return app.workbench.list(scope, op, cursor, limit)
        if op=='tasks':
            return app.tasks.list(scope, str(cursor), limit)
        if op=='task':
            return app.tasks.detail(scope, p['task_id'])
        if op=='calls':
            return app.usage.list(scope, str(cursor), limit)
        if op=='aliases':
            return app.identity.page(scope, p.get('member',''), str(cursor), limit)
        if op=='sources':
            return {'items':app.memory.sources(scope, p['id'])}
        if op=='event_sources':
            return {'items':app.memory.event_sources(scope, p['id'])}
        if op=='submit':
            kind = p.get('kind','new')
            if kind in ('history','deferred') and not app.config.get('reanalysis_enabled'):
                raise Failure('disabled','请先开启历史重分析')
            start, end = p.get('start'), p.get('end')
            if kind=='deferred':
                start, end = p['raw_id']-1, p['raw_id']
            return app.tasks.submit(scope, kind, p.get('member',''), p.get('preview',False), start, end)
        if op=='control':
            app.tasks.control(scope, p['task_id'], p['action'])
        elif op=='manual_add':
            return {'id':app.memory.add_manual(scope, p.get('member',''), p['content'], p.get('purpose','long_term'), p.get('restricted',False))}
        elif op=='claim_edit':
            changes = {k:p[k] for k in ('content','purpose','status','restricted') if k in p}
            app.memory.revise(scope, p['id'], p['revision'], changes, actor)
        elif op=='event_edit':
            app.memory.revise_event(scope, p['id'], p['revision'], p['content'], actor)
        elif op=='alias_add':
            app.identity.observe(scope, p['member'], p['name'], p.get('alias_kind','alias'), actor, False)
        elif op=='alias_edit':
            app.identity.revise(scope, p['id'], p['revision'], p['status'], actor)
        elif op=='link_resolve':
            app.memory.resolve_link(scope, p['id'], p['revisions'], p['action'], actor)
        elif op=='disclose':
            app.archive.disclose(scope, p['raw_id'], p['disclosure'])
        elif op=='pause':
            app.access.pause(scope, p['paused'])
        elif op=='privacy':
            app.privacy.change(scope, p['member'], p['opted_out'], p.get('erase',False))
        elif op=='import':
            if not app.config.get('backfill_enabled'):
                raise Failure('disabled','请先开启历史导入')
            path = p.get('path','').strip()
            if not path:
                adapter = self.context.get_platform_inst(scope.platform)
                binding = getattr(adapter,'get_task_scope', None)
                if binding is None:
                    raise Failure('inbox_unavailable','当前连接器未提供 Inbox 路径，请在页面填写服务器路径后导入')
                info = binding()
                if info['owner'] != scope.owner:
                    raise Failure('scope_denied','当前微信账号与所选归档账号不同，不能导入其 Inbox')
                path = app.config.get('hook_inbox_db') or info['inbox_db']
            return await asyncio.to_thread(app.transfer.import_file, path, scope, 500)
        elif op=='export':
            directory = app.directory/'exports'
            directory.mkdir(exist_ok=True)
            return await asyncio.to_thread(app.transfer.export, scope, directory/(ident()+'.jsonl'))
        return {'status':'completed'}
