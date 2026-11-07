"""Resolve existing record authors through account-bound read-only Hook contacts."""
import asyncio
import json
import time
from collections import Counter

from .people import aliases, observe, key
from .rooms import _field, _rows

ID_FIELDS=('subject_id','sender_id','speaker_id','requester')
MISSING={'昵称未取得','未知昵称','未知用户'}


class MemberNames:
    def __init__(self,context,store):
        self.context=context;self.store=store;self.checked={};self.lock=asyncio.Lock()

    def _contacts(self,s):
        adapter=self.context.get_platform_inst(s.platform)
        binding=getattr(adapter,'get_task_scope',None)
        if not callable(binding):return None
        info=binding()
        if not isinstance(info,dict) or info.get('owner')!=s.owner:return None
        contacts=getattr(adapter,'_contacts',None)
        return contacts if callable(getattr(contacts,'lookup',None)) else None

    def _save(self,s,ident,name,kind):
        name=''.join(c for c in name if c.isprintable()).strip()[:100] if isinstance(name,str) else ''
        if not name or name==ident or name in MISSING or not self._contacts(s):return False
        observe(self.store,s,ident,name,kind,'hook_contacts')
        return True

    async def enrich(self,s,data):
        records=[];previews=[]
        def collect(value):
            if isinstance(value,list):
                for item in value:collect(item)
            elif isinstance(value,dict):
                records.append(value)
                for field,item in value.items():
                    if field=='preview_json' and isinstance(item,str) and item:
                        try:
                            preview=json.loads(item)
                        except (ValueError,TypeError):continue
                        previews.append((value,preview));collect(preview)
                    elif isinstance(item,(list,dict)):collect(item)
        collect(data)
        ids=sorted({record[field] for record in records for field in ID_FIELDS
                    if isinstance(record.get(field),str) and record[field]})
        if not ids:return data
        async with self.lock:
            with self.store._lock:
                # Never query unrelated accounts, group outsiders or opted-out members.
                known={r[0] for r in self.store.db.execute('SELECT DISTINCT sender_id FROM raw_messages WHERE platform=? AND owner=? AND room=?',key(s))}
            wanted=[ident for ident in ids if ident in known and not self.store.is_opted_out(s,ident)
                    and self.checked.get((*key(s),ident),0)<=time.monotonic()][:200]
            try:
                contacts=self._contacts(s)
                if wanted and contacts:
                    for ident in wanted:self.checked[(*key(s),ident)]=time.monotonic()+60
                    async with asyncio.timeout(8):
                        resolved=set()
                        cached=getattr(contacts,'get',None)
                        if callable(cached):
                            cards=cached(('group',s.room)) or {}
                            for ident in wanted:
                                if isinstance(cards,dict) and self._save(s,ident,cards.get(ident,''),'card'):resolved.add(ident)
                                if self._save(s,ident,cached(('user',ident)),'nickname'):resolved.add(ident)
                        # Group cards are more useful than account nicknames in group views.
                        response=await contacts.lookup('/GetContact',{'wxid':s.room})
                        for group in _rows(response):
                            if _field(group,'UserName','wxid')!=s.room:continue
                            members=group.get('members',[])
                            for member in members[:4096] if isinstance(members,list) else []:
                                if not isinstance(member,dict):continue
                                ident=_field(member,'UserName','wxid')
                                name=_field(member,'DisplayName','NickName','nickname')
                                if ident in known and not self.store.is_opted_out(s,ident) and self._save(s,ident,name,'card'):
                                    resolved.add(ident)
                        missing=[ident for ident in wanted if ident not in resolved]
                        if missing:
                            response=await contacts.lookup('/BatchGetContactBriefInfo',{'wxidlist':missing})
                            for contact in _rows(response):
                                ident=_field(contact,'UserName','wxid')
                                if ident in missing and self._save(s,ident,_field(contact,'NickName','display_name'),'nickname'):resolved.add(ident)
                        missing=[ident for ident in wanted if ident not in resolved]
                        database=getattr(contacts,'database',None)
                        if missing and callable(getattr(database,'resolve',None)):
                            for ident,name in (await database.resolve(missing)).items():
                                if ident in missing and self._save(s,ident,name,'nickname'):resolved.add(ident)
                        semaphore=asyncio.Semaphore(4)
                        async def one(ident):
                            async with semaphore:
                                try:
                                    async with asyncio.timeout(2):
                                        response=await contacts.lookup('/GetContact',{'wxid':ident})
                                        for contact in _rows(response):
                                            if _field(contact,'UserName','wxid')==ident and self._save(s,ident,_field(contact,'NickName','display_name'),'nickname'):
                                                resolved.add(ident)
                                except (TimeoutError,ValueError,RuntimeError):pass
                        await asyncio.gather(*(one(ident) for ident in missing if ident not in resolved))
                        for ident in resolved:self.checked[(*key(s),ident)]=time.monotonic()+600
            except Exception:
                # Offline name resolution must not hide already archived evidence.
                pass
            names=aliases(self.store,s)
            selected={}
            for ident in ids:
                own=[a for a in names if a['subject_id']==ident and a['status']=='confirmed'
                     and a['name']!=ident and a['name'] not in MISSING]
                own.sort(key=lambda a:(bool(a['valid_to']),{'admin':0,'card':1,'nickname':2}.get(a['kind'],3),-a['last_seen']))
                selected[ident]=own[0]['name'] if own else '昵称未取得'
            duplicates=Counter(name for name in selected.values() if name!='昵称未取得')
            for record in records:
                for field in ID_FIELDS:
                    ident=record.get(field)
                    if isinstance(ident,str) and ident:record[field.removesuffix('_id')+'_name']=selected.get(ident,'昵称未取得')
                if 'claims' in record and record.get('subject_id'):
                    ident=record['subject_id'];name=selected.get(ident,'昵称未取得')
                    record['display_name']=name+(' · '+ident[-6:] if duplicates[name]>1 else '')
                    record['aliases']=[a for a in names if a['subject_id']==ident and a['status']!='retracted']
            for record,preview in previews:record['preview_json']=json.dumps(preview,ensure_ascii=False)
        return data
