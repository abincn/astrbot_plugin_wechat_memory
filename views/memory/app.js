const bridge = window.AstrBotPluginView;
const $ = (id) => document.getElementById(id);
const state = { scopes: [], current: null, list: [], selected: null, busy: false, editorMode: 'add' };
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=String(text); if(cls)el.className=cls; return el; }
function notify(text, isError=false) { $('status').textContent=text; $('status').classList.toggle('error',isError); }
function selectedScope() { if(!state.current) throw new Error('请先选择一个有消息记录的群。'); return state.current; }
async function api(operation,extra={}) { return bridge.apiPost('manage',{...selectedScope(),operation,...extra}); }
async function lock(fn) { if(state.busy)return; state.busy=true; document.querySelectorAll('button').forEach(x=>x.disabled=true); try{await fn();}catch(err){notify(err?.message||'操作失败',true);}finally{state.busy=false; document.querySelectorAll('button').forEach(x=>x.disabled=false);} }
function fmtDate(n){return n?new Date(n*1000).toLocaleString('zh-CN',{hour12:false}):'—';}
async function connect() {
 const x=await bridge.apiPost('manage',{operation:'scopes'});
 state.scopes=x.scopes||[]; $('scope').replaceChildren();
 for(const s of state.scopes){const op=node('option',`${s.owner} · ${s.room}`);op.value=state.scopes.indexOf(s);$('scope').append(op);}
 if(!state.scopes.length){state.current=null;notify('当前没有已接收消息的已授权群。请先在插件配置中启用群并发送消息。');return;}
 state.current=state.scopes[0]; await refresh();
}
async function refresh() {
 if(!state.current)return;
 const [overview,listing]=await Promise.all([api('overview'),api('list',filters())]);
 for(const k of ['active','review','pending'])$(k).textContent=overview.stats[k];
 $('calls').textContent=overview.stats.today_usage.calls;
 $('tokens').textContent=(overview.stats.today_usage.input_tokens+overview.stats.today_usage.output_tokens).toLocaleString();
 $('pause').textContent=overview.paused?'恢复提取':'暂停提取';
 $('pause').dataset.paused=String(overview.paused);
 state.list=listing.items||[];renderList();renderDetail();
 notify(`已加载 ${state.current.room}，当前页面最多显示 100 条记录。`);
}
function filters(){return {status:$('filter-status').value,kind:$('filter-kind').value,query:$('query').value.trim()};}
function renderList(){
 $('items').replaceChildren();
 if(!state.list.length){$('items').append(node('p','暂无匹配记录。','empty'));return;}
 for(const item of state.list){
  const btn=node('button',undefined,'record');btn.type='button';btn.setAttribute('aria-pressed',String(state.selected===item.id));
  const bar=node('div',undefined,'record-top');bar.append(node('small',`${item.kind}${item.subject_id?' · '+item.subject_id:''}`),node('span',item.status,'pill '+item.status));
  btn.append(bar,node('div',item.content,'record-content'),node('small',fmtDate(item.updated_at)));
  btn.addEventListener('click',()=>lock(async()=>{state.selected=item.id;renderList();await renderDetail();}));
  $('items').append(btn);
 }
}
async function renderDetail(){
 const el=$('detail');el.replaceChildren();
 const item=state.list.find(x=>x.id===state.selected);
 if(!item){el.append(node('p','选择一条记录查看详情。','muted'));return;}
 el.append(node('h3',item.status==='review'?'待审核记忆':'记忆内容'),node('p',item.content,'content'));
 el.append(node('p',`ID: ${item.id}\n类别: ${item.kind}\n范围: ${item.scope} ${item.subject_id||''}\n来源数: ${item.source_count}\n创建: ${fmtDate(item.created_at)}\n更新: ${fmtDate(item.updated_at)}`,'info'));
 const actions=node('div',undefined,'actions');
 function act(text,op,cls){const b=node('button',text,cls);b.type='button';b.onclick=()=>lock(async()=>{if(op==='edit'){editor('edit',item);return;}if(op==='delete'&&!confirm('确认将这条记忆标记为删除？'))return;await api(op,{id:item.id});if(op==='delete'||op==='disable')state.selected=null;await refresh();});actions.append(b);}
 if(item.status==='review')act('批准并替换旧同主题记忆','approve','primary');
 if(item.status!=='deleted')act('编辑','edit');
 if(item.status==='active'||item.status==='review')act('停用','disable');
 if(item.status!=='deleted')act('删除','delete','danger');
 el.append(actions,node('h3','原始消息来源'));
 const sources=await api('sources',{id:item.id});
 if(!sources.sources?.length)el.append(node('p','由管理员手动创建，或暂无消息来源。','muted'));
 else for(const s of sources.sources)el.append(node('p',`${s.sender_id} · ${fmtDate(s.created_at)}\n${s.message_id}`,'source'));
}
function editor(mode,item=null){
 state.editorMode=mode;$('editor-title').textContent=mode==='edit'?'编辑记忆':'新增本群记忆';
 $('content').value=item?.content||'';$('new-fields').hidden=mode==='edit';
 if(mode==='add'){$('kind').value='fact';$('topic').value='';$('subject').value='';}
 $('editor').showModal();
}
$('editor-form').addEventListener('submit',(event)=>{event.preventDefault();lock(async()=>{
 const value=$('content').value.trim();
 if(state.editorMode==='edit')await api('edit',{id:state.selected,content:value});
 else {const result=await api('add',{content:value,kind:$('kind').value,topic:$('topic').value.trim(),subject:$('subject').value.trim()});state.selected=result.id;}
 $('editor').close();await refresh();
});});
$('cancel').addEventListener('click',()=>$('editor').close());
$('add').addEventListener('click',()=>{if(state.current)editor('add');});
$('refresh').addEventListener('click',()=>lock(refresh));
$('scope').addEventListener('change',()=>lock(async()=>{state.current=state.scopes[Number($('scope').value)];state.selected=null;await refresh();}));
$('filter-status').addEventListener('change',()=>lock(refresh));
$('filter-kind').addEventListener('change',()=>lock(refresh));
$('query').addEventListener('change',()=>lock(refresh));
$('pause').addEventListener('click',()=>lock(async()=>{await api($('pause').dataset.paused==='true'?'resume':'pause');await refresh();}));
$('export').addEventListener('click',()=>lock(async()=>{
 const data=await api('export');const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;
 a.download='wechat-memory-'+state.current.room.replace(/[^a-zA-Z0-9.@-]/g,'_')+'.json';
 document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);
 notify('已导出当前群的最近 200 条记录，不含跨群个人画像。');
}));
try{await bridge.ready();await lock(connect);}catch(e){notify('无法连接 AstrBot 插件管理接口：'+(e?.message||'未知错误'),true);}
