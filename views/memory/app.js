const bridge=window.AstrBotPluginView;
const $=(id)=>document.getElementById(id);
const state={scopes:[],current:null,rows:[],selected:null,view:'memories',busy:false};
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;};
const notify=(msg,bad=false)=>{$('status').textContent=msg;$('status').classList.toggle('error',bad);};
const date=(n)=>n?new Date(n*1000).toLocaleString('zh-CN',{hour12:false}):'—';
const safeText=(v)=>v===null||v===undefined?'':String(v);
async function api(op,fields={}){if(!state.current)throw new Error('请先选择群');return bridge.apiPost('manage',{...state.current,operation:op,...fields});}
async function busy(fn){if(state.busy)return;state.busy=true;const disabled=[];document.querySelectorAll('button').forEach(b=>{if(!b.disabled&&!b.closest('#confirm-dialog')){b.disabled=true;disabled.push(b);}});try{await fn();}catch(e){notify(e?.message||'操作失败',true);}finally{state.busy=false;disabled.forEach(b=>b.disabled=false);}}
function askConfirm(message){const dialog=$('confirm-dialog');$('confirm-message').textContent=message;dialog.returnValue='';return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true});dialog.showModal();});}
async function connect(){const r=await bridge.apiPost('manage',{operation:'scopes'});state.scopes=r.scopes||[];$('scope').replaceChildren();for(let i=0;i<state.scopes.length;i++){$('scope').append(el('option',`${state.scopes[i].owner} · ${state.scopes[i].room}`));}
if(!state.scopes.length){notify('没有已观测的白名单群。可先发送消息，或在配置中启用 Hook Inbox 补录。');return;}
state.current=state.scopes[0];await refresh();}
async function refresh(){if(!state.current)return;
const r=await api('overview');const stats=r.stats;
for(const id of ['raw','active','pending'])$(id).textContent=stats[id]??'—';
$('review').textContent=(stats.review||0)+(stats.candidates_review||0);
$('tokens').textContent=((stats.today_usage?.input_tokens||0)+(stats.today_usage?.output_tokens||0)).toLocaleString();
$('pause').textContent=r.paused?'恢复巩固':'暂停巩固';$('pause').dataset.paused=String(r.paused);
$('meta').textContent=`上次成功 ${date(stats.last_success)} · 游标 ${stats.cursor} · 调用 ${stats.today_usage?.calls||0} · 估算 $${stats.today_cost_usd_estimate??'未配置单价'} · 错误 ${stats.last_error||'无'}`;
await list();notify(`已加载 ${state.current.room} · 当前查看 ${$('view').selectedOptions[0]?.textContent||''}`);}
async function list(){let op=state.view==='memories'?'list':state.view;
const args=op==='list'?{status:$('filter-status').value,kind:$('filter-kind').value,query:$('query').value.trim()}:{};
const r=await api(op,args);state.rows=r.items||[];state.selected=null;
$('memory-filters').hidden=state.view!=='memories';$('add').hidden=state.view!=='memories';
const titles={memories:'长期记忆',candidates:'增量候选与差异',runs:'后台执行记录',topics:'话题累计摘要',observations:'成员与群观察',history:'原始聊天证据',retrieval_audit:'检索审计'};
$('list-title').textContent=titles[state.view];renderList();await detail();}
function title(row){if(state.view==='memories')return row.content;
if(state.view==='candidates')return `${row.action}: ${row.new_content}`;
if(state.view==='runs')return `${row.status} · ${row.changes_count} 处修改 · ${row.provider}`;
if(state.view==='history')return row.text||`[${row.kind}] ${row.media_kind||''}`;
if(state.view==='topics')return `${row.title} · ${row.state} · ${row.summary}`;
if(state.view==='observations')return `${row.scope==='member'?row.subject_id:'群公共'} · ${row.signal_type} · ${row.content}`;
return `${row.mode} · ${row.selected_ids} · ${row.raw_ids}`;}
function key(row){return state.view==='memories'?row.id:state.view==='candidates'?row.id:state.view==='runs'?row.id:state.view==='history'?row.id:row.id;}
function renderList(){const box=$('items');box.replaceChildren();if(!state.rows.length){box.append(el('p','暂无记录。','empty'));return;}
for(const r of state.rows){const n=el('button',undefined,'record');n.type='button';n.setAttribute('aria-pressed',String(state.selected===key(r)));
const head=el('div',undefined,'record-top');head.append(el('small',state.view==='memories'?`${r.kind} · ${r.subject_id||'群公共'}`:state.view==='history'?r.sender_id:date(r.created_at)),el('span',r.status||r.action||r.mode||r.kind,'pill '+(r.status||'')));
n.append(head,el('div',title(r),'record-content'),el('small',date(r.updated_at||r.message_at||r.created_at)));
n.onclick=()=>busy(async()=>{state.selected=key(r);renderList();await detail();});box.append(n);}}
function button(label,fn,cls=''){const b=el('button',label,cls);b.type='button';b.onclick=()=>busy(fn);return b;}
async function detail(){const box=$('detail');box.replaceChildren();const row=state.rows.find(r=>key(r)===state.selected);
if(!row){box.append(el('p','选择记录查看详情。','muted'));return;}
if(state.view==='memories'){
box.append(el('h3',`记忆 ${row.kind} · r${row.revision}`),el('p',row.content,'content'),el('p',`ID: ${row.id}\n作用域: ${row.scope} / ${row.subject_id||'群'}\n状态: ${row.status}\n更新时间: ${date(row.updated_at)}`,'info'));
const actions=el('div',undefined,'actions');if(row.status==='review')actions.append(button('批准普通待审核记录',async()=>{await api('approve',{id:row.id,revision:row.revision});await refresh();},'primary'));
if(row.status!=='deleted')actions.append(button('编辑',async()=>showEditor('edit',row)),button('回滚前一版',async()=>{if(await askConfirm('恢复到上一版本？')){await api('rollback',{id:row.id,revision:row.revision});await refresh();}}));
if(row.status==='active'||row.status==='review')actions.append(button('停用',async()=>{await api('disable',{id:row.id,revision:row.revision});await refresh();}));
if(row.status!=='deleted')actions.append(button('删除',async()=>{if(await askConfirm('删除本群记忆？')){await api('delete',{id:row.id,revision:row.revision});await refresh();}},'danger'));
box.append(actions,el('h3','证据来源'));const src=await api('sources',{id:row.id});if(!(src.sources||[]).length)box.append(el('p','未记录对应的聊天证据（可能是人工录入）。','muted'));
for(const s of src.sources||[])box.append(el('p',`${s.sender_id} · ${date(s.created_at)}\n${s.message_id}\n${s.text}`,'source'));
const ver=await api('versions',{id:row.id});box.append(el('h3','历史修订'));
for(const v of ver.items||[])box.append(el('p',`r${v.revision} ${v.action} · ${date(v.occurred_at)}\n${v.previous_content}`,'source'));
}else if(state.view==='candidates'){
box.append(el('h3',`${row.action} · ${row.status}`),el('p',`旧版：\n${row.old_content||'（新记录）'}`,'source'),el('p',`建议：\n${row.new_content}`,'content'),el('p',`原因：${row.reason}\n来源：${row.sources_json}\n旧版本要求：${row.expected_revision??'不适用'}\nID: ${row.id}`,'info'));
if(row.status==='review'){const actions=el('div',undefined,'actions');actions.append(button('批准变更',async()=>{await api('candidate_approve',{id:row.id});await refresh();},'primary'),button('拒绝变更',async()=>{await api('candidate_reject',{id:row.id});await refresh();},'danger'));box.append(actions);}
}else {box.append(el('h3',state.view==='history'?'原始事件':'运行记录'),el('p',JSON.stringify(row,null,2),'source'));}}
function showEditor(mode,row=null){$('editor-title').textContent=mode==='edit'?'修改自然语言记忆':'添加本群记忆';$('content').value=row?.content||'';$('new-fields').hidden=mode==='edit';$('editor').dataset.mode=mode;$('editor').showModal();}
$('editor-form').onsubmit=(event)=>{event.preventDefault();busy(async()=>{if($('editor').dataset.mode==='edit'){const row=state.rows.find(r=>r.id===state.selected);await api('edit',{id:row.id,revision:row.revision,content:$('content').value.trim()});}
else await api('add',{content:$('content').value.trim(),kind:$('kind').value,subject:$('subject').value.trim(),topic:$('topic').value.trim()});$('editor').close();await refresh();});};
$('cancel').onclick=()=>$('editor').close();
$('view').onchange=()=>busy(async()=>{state.view=$('view').value;await list();});
$('scope').onchange=()=>busy(async()=>{state.current=state.scopes[$('scope').selectedIndex];await refresh();});
$('refresh').onclick=()=>busy(refresh);
for(const id of ['query','filter-status','filter-kind'])$(id).onchange=()=>busy(list);
$('add').onclick=()=>showEditor('add');
$('dream').onclick=()=>busy(async()=>{await api('run_now');notify('已安排巩固，稍后在“后台执行记录”中查看结果。');});
$('dry-run').onclick=()=>busy(async()=>{await api('dry_run');notify('已安排预览，结果不会写入记忆，也不会推进游标，但会产生模型费用。');});
$('backfill').onclick=()=>busy(async()=>{if(!state.current){notify('请先选择群',true);return;}const scope={...state.current};notify(`正在从 Hook Inbox 读取 ${scope.room} 的记录…`);const r=await bridge.apiPost('manage',{operation:'backfill',...scope,limit:500});await refresh();const errors=(r.errors||[]).map(x=>`${x.room} #${x.sequence}: ${x.error}`);notify(`Hook 补录：新增 ${r.imported}，重复 ${r.duplicates}，跳过 ${r.skipped}；来源库 ${r.source_rows} 条，当前群匹配 ${r.matching_rows} 条。${r.note?' '+r.note:''}${errors.length?' 失败：'+errors.join('；'):''}`,errors.length>0);});
$('media').onclick=()=>busy(async()=>{await api('media_run');notify('已安排媒体分析。只有启用媒体模型并设置允许路径后才会处理。');});
$('pause').onclick=()=>busy(async()=>{await api($('pause').dataset.paused==='true'?'resume':'pause');await refresh();});
$('export').onclick=()=>busy(async()=>{const data=await api('export');const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=el('a');a.href=url;a.download='wechat-memory-'+state.current.room.replace(/[^a-zA-Z0-9.@-]/g,'_')+'.json';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);notify('已导出当前群的记忆。');});
try{await bridge.ready();await busy(connect);}catch(e){notify('插件视图连接失败：'+(e?.message||'未知错误'),true);}
