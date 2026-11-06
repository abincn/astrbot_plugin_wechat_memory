"""Hook account verification and response normalization, one network boundary."""
import asyncio
import logging

log = logging.getLogger(__name__)


def field(row, *names):
    fields = {str(k).replace('_','').lower(): v for k,v in row.items()}
    return next((fields[n.replace('_','').lower()].strip() for n in names
                 if isinstance(fields.get(n.replace('_','').lower()), str) and fields[n.replace('_','').lower()].strip()), '')


def records(value):
    if isinstance(value, list):
        return value
    for name in ('contacts','data','contact','result'):
        if isinstance(value.get(name), (dict,list)):
            return records(value[name])
    return [value]


class Contacts:
    def __init__(self, context, identity, archive):
        self.context, self.identity, self.archive = context, identity, archive

    async def refresh(self, scope):
        adapter = self.context.get_platform_inst(scope.platform)
        binding = getattr(adapter, 'get_task_scope', None)
        contacts = getattr(adapter, '_contacts', None)
        if binding is None or contacts is None:
            return {'status': 'unavailable', 'reason': '当前连接器未提供联系人查询能力'}
        if binding()['owner'] != scope.owner:
            return {'status': 'unavailable', 'reason': '当前微信账号与归档账号不同'}
        try:
            async with asyncio.timeout(10):
                groups = records(await contacts.lookup('/GetContact', {'wxid':scope.room}))
                for group in groups:
                    if field(group,'UserName','wxid') != scope.room:
                        continue
                    name = field(group,'NickName','display_name')
                    if name:
                        self.identity.set_room_name(scope, name)
                    for member in group.get('members', []):
                        member_id = field(member,'UserName','wxid')
                        self.identity.observe(scope, member_id, field(member,'DisplayName'), 'card', 'hook_contacts')
                        self.identity.observe(scope, member_id, field(member,'NickName'), 'nickname', 'hook_contacts')
                members = self.archive.members(scope)
                # Bound each connector request, without hiding later members behind one overall cap.
                for start in range(0, len(members), 100):
                    wanted = {r['sender'] for r in members[start:start+100]}
                    response = records(await contacts.lookup('/BatchGetContactBriefInfo', {'wxidlist':list(wanted)}))
                    for row in response:
                        member_id = field(row,'UserName','wxid')
                        if member_id in wanted:
                            self.identity.observe(scope, member_id, field(row,'NickName','display_name'), 'nickname', 'hook_contacts')
            return {'status': 'updated'}
        except Exception as exc:
            log.exception('联系人查询失败 scope=%s', scope.key)
            return {'status':'failed', 'reason':f'联系人查询失败：{type(exc).__name__}: {exc}'}
