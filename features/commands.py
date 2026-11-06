from ..core.types import Scope, Failure

HELP = '''私聊记忆命令：
/wm profile add 内容
/wm profile list
/wm profile delete 记忆ID
/wm share allow 群ID@chatroom
/wm share revoke 群ID@chatroom
/wm share list
/wm group off 群ID@chatroom
/wm group on 群ID@chatroom
/wm group erase 群ID@chatroom
/wm privacy off'''


class Commands:
    def __init__(self, archive, memory, access, privacy, config):
        self.archive, self.memory, self.access, self.privacy, self.config = archive, memory, access, privacy, config

    def execute(self, platform, owner, member, text):
        parts = text.split(' ', 3)
        if len(parts)<3:
            return HELP
        _, group, action, *tail = parts
        argument = tail[0].strip() if tail else ''
        global_scope = Scope(platform, owner, '')
        if group=='profile':
            self.archive.ensure_room(global_scope)
            if action=='list':
                return '\n'.join(r['id']+' '+r['content'] for r in self.memory.own(global_scope, member)) or '暂无个人记忆。'
            if not argument:
                raise Failure('invalid_input', '请提供记忆内容或ID')
            if action=='add':
                return '已添加，仅在本人授权的群内使用：'+self.memory.add_manual(global_scope, member, argument)
            if action=='delete':
                rows = self.memory.own(global_scope, member, argument)
                if not rows:
                    raise Failure('not_found', '没有找到本人的记忆')
                row = rows[0]
                self.memory.revise(global_scope, row['id'], row['revision'], {'status':'deleted'}, member)
                return '已删除。'
        if group=='share' and action=='list':
            rows = self.access.permissions(platform, owner, member)
            return '\n'.join(rows) or '没有授权群。'
        if group=='privacy' and action=='off':
            for room in self.access.permissions(platform, owner, member):
                self.access.set_permission(platform, owner, member, room, False)
            return '已撤销全部跨群授权。'
        if group in ('share','group'):
            if argument not in self.config.get('allowed_rooms', []):
                raise Failure('scope_denied', '该群不在授权群列表内')
            if group=='share' and action in ('allow','revoke'):
                self.access.set_permission(platform, owner, member, argument, action=='allow')
                return '已更新本人跨群授权。'
            if group=='group' and action in ('on','off','erase'):
                self.privacy.change(Scope(platform, owner, argument), member, action!='on', action=='erase')
                return '已更新本人群内记忆设置。原文归档与画像退出独立；erase 才清除已有原文。'
        return HELP
