"""Read-only, account-bound Hook group-name lookup with persisted caching."""
import asyncio
import time
from collections import Counter


def _field(row,*names):
    fields={str(k).replace('_','').lower():v for k,v in row.items()}
    for name in names:
        value=fields.get(name.replace('_','').lower())
        if isinstance(value,str) and value.strip():return value.strip()
    return ''


def _rows(value,depth=0):
    if depth>4:return []
    if isinstance(value,list):return [r for r in value[:200] if isinstance(r,dict)]
    if isinstance(value,dict):
        for name in ('contacts','data','contact','result'):
            if isinstance(value.get(name),(dict,list)):return _rows(value[name],depth+1)
        return [value]
    return []


class RoomNames:
    def __init__(self,context,store):
        self.context=context;self.store=store;self.retry_at={};self.lock=asyncio.Lock()

    async def list(self,platform,allowed_rooms,refresh=False):
        async with self.lock:
            with self.store._lock:
                scopes=[dict(r) for r in self.store.db.execute('''SELECT platform,owner,room,room_name,name_updated_at
                    FROM room_state WHERE platform=? ORDER BY owner,room''',(platform,)) if r['room'] in allowed_rooms]
            groups={}
            for row in scopes:
                ident=(row['platform'],row['owner'],row['room'])
                stale=int(time.time())-row['name_updated_at']>=600
                if (refresh or not row['room_name'] or stale) and (refresh or self.retry_at.get(ident,0)<=time.monotonic()):
                    groups.setdefault(row['owner'],[]).append(row)
            for owner,rows in groups.items():
                adapter=self.context.get_platform_inst(platform)
                binding=getattr(adapter,'get_task_scope',None)
                contacts=getattr(adapter,'_contacts',None)
                if not callable(binding) or not callable(getattr(contacts,'lookup',None)):continue
                try:
                    info=binding()
                    if not isinstance(info,dict) or info.get('owner')!=owner:continue
                    wanted={r['room']:r for r in rows[:200]}
                    for room in wanted:self.retry_at[(platform,owner,room)]=time.monotonic()+60
                    async with asyncio.timeout(8):
                        response=await contacts.lookup('/BatchGetContactBriefInfo',{'wxidlist':list(wanted)})
                        for contact in _rows(response):self._save(contact,wanted,platform,owner)
                        semaphore=asyncio.Semaphore(4)
                        async def one(room):
                            async with semaphore:
                                try:
                                    async with asyncio.timeout(2):
                                        result=await contacts.lookup('/GetContact',{'wxid':room})
                                        for contact in _rows(result):self._save(contact,{room:wanted[room]},platform,owner)
                                except (TimeoutError,RuntimeError,ValueError):pass
                        await asyncio.gather(*(one(room) for room,row in wanted.items() if not row.get('_resolved')))
                        missing=[room for room,row in wanted.items() if not row.get('_resolved')]
                        database=getattr(contacts,'database',None)
                        if missing and callable(getattr(database,'resolve',None)):
                            for room,name in (await database.resolve(missing)).items():
                                self._save({'UserName':room,'NickName':name},wanted,platform,owner)
                except Exception:
                    # A stopped/offline connector must not prevent access to archived groups.
                    continue
            names=Counter(r['room_name'] for r in scopes if r['room_name'])
            return [{'platform':r['platform'],'owner':r['owner'],'room':r['room'],
                     'room_name':r['room_name'],
                     'display_name':(r['room_name']+(' · '+r['owner']+' · '+r['room'] if names[r['room_name']]>1 else ''))
                         if r['room_name'] else '群名未取得（'+r['room']+'）'} for r in scopes]

    def _save(self,contact,wanted,platform,owner):
        room=_field(contact,'UserName','wxid')
        name=''.join(c for c in _field(contact,'NickName','display_name') if c.isprintable())[:160]
        if room not in wanted or not name or name==room:return
        adapter=self.context.get_platform_inst(platform)
        binding=getattr(adapter,'get_task_scope',None)
        if not callable(binding) or binding().get('owner')!=owner:return
        with self.store.transaction():
            self.store.db.execute('UPDATE room_state SET room_name=?,name_updated_at=? WHERE platform=? AND owner=? AND room=?',
                                 (name,int(time.time()),platform,owner,room))
        wanted[room]['room_name']=name;wanted[room]['_resolved']=True
