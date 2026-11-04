const bridge=window.AstrBotPluginView;
const $=id=>document.getElementById(id);
const state={scopes:[],scope:null,view:'overview',rows:[],selected:'',jobSelected:'',jobs:[],busy:false,stats:null,edit:null};
const TITLES={
  v3_profiles:['成员画像','长期互动形成的行为、事实与背景，保留证据和可信度。'],
  v3_events:['群事件','聚餐、项目与持续讨论；具体事实和任务关联同一事件。'],
  v3_claims:['事实与观察','可核实的成员背景、群决定、行为模式及第三方说法。'],
  v3_coverage:['消息覆盖','每条原始消息的 V3 处理结论，未提取不等于没阅读。'],
  runs:['执行记录','查看每一轮模型处理结果、错误及预览提案。'],
  history:['原始群聊','已经归档的群聊内容，以原始来源为准。'],
  memories:['旧版与人工记忆','历史保留的数据，可继续管理，但并非 V3 核心真相源。'],
  candidates:['旧版审核','升级前保存的差异候选与人工审核。'],
  topics:['旧版话题','V2 历史主题，仅作兼容参考。'],
  observations:['旧版观察','V2 保存的观察；新 V3 使用原子主张。'],
  retrieval_audit:['检索审计','已记录的群内上下文调用。']
};
const LABELS={
  active:'有效',candidate:'候选',review:'待审核',reported:'第三方说法',
  pending:'待执行',running:'进行中',completed:'已完成',failed:'失败',
  paused:'已暂停',cancelled:'已取消',committed:'已提交',preview:'预览',
  observing:'观察中',superseded:'已替代',retracted:'已撤销',deleted:'已删除',
  planning:'筹备中',concluded:'已结束',disputed:'存在分歧',
  analyzed_with_evidence:'已关联证据',analyzed_no_signal:'无长期信号',
  excluded_by_policy:'不参与文本提取',deferred:'延后分析'
};
function el(tag,text,className){
  const node=document.createElement(tag);
  if(text!==undefined&&text!==null)node.textContent=String(text);
  if(className)node.className=className;
  return node;
}
function fmtDate(seconds){
  if(!Number(seconds))return '—';
  return new Date(Number(seconds)*1000).toLocaleString('zh-CN',{hour12:false});
}
function fmtNum(n){return (Number(n)||0).toLocaleString('zh-CN');}
function label(v){return LABELS[v]||String(v||'—');}
function notice(message,error=false){
  const box=$('status');
  box.hidden=!message;
  box.classList.toggle('error',error);
  box.textContent=message||'';
}
function button(text,click,variant='quiet'){
  const b=el('button',text,variant);b.type='button';
  b.addEventListener('click',()=>work(click));
  return b;
}
function metaRows(pairs){
  const dl=el('dl',undefined,'detail-meta');
  for(const [k,v] of pairs){dl.append(el('dt',k),el('dd',v===null||v===undefined||v===''?'—':v));}
  return dl;
}
function sourceCard(text,caption=''){
  const b=el('div',undefined,'source');
  if(caption)b.append(el('small',caption),el('div',text));
  else b.textContent=text;
  return b;
}
function codeDetails(name,obj){
  const details=el('details',undefined,'json-detail');
  details.append(el('summary',name),el('pre',JSON.stringify(obj,null,2)));
  return details;
}
async function api(operation,data={}){
  if(!state.scope)throw new Error('请先选择群');
  return bridge.apiPost('manage',{...state.scope,operation,...data});
}
async function work(fn){
  if(state.busy)return;
  state.busy=true;
  const disabled=[];
  document.querySelectorAll('button').forEach(b=>{
    if(!b.disabled&&!b.closest('dialog')){b.disabled=true;disabled.push(b);}
  });
  try{await fn();}
  catch(err){notice(err?.message||'操作未成功，请查看插件日志',true);}
  finally{
    state.busy=false;
    disabled.forEach(b=>b.disabled=false);
  }
}
function confirmAction(text){
  const dlg=$('confirm-dialog');$('confirm-message').textContent=text;
  dlg.returnValue='';
  return new Promise(resolve=>{
    dlg.addEventListener('close',()=>resolve(dlg.returnValue==='ok'),{once:true});
    dlg.showModal();
  });
}
function showPage(view){
  state.view=view;state.selected='';state.jobSelected='';
  $('overview-page').hidden=view!=='overview';
  $('history-page').hidden=view!=='replay_jobs';
  $('data-page').hidden=view==='overview'||view==='replay_jobs';
  for(const node of document.querySelectorAll('[data-view]')){
    const active=node.dataset.view===view;node.classList.toggle('active',active);
    if(active)node.setAttribute('aria-current','page');
    else node.removeAttribute('aria-current');
  }
  if(!['overview','v3_profiles','v3_events','v3_claims','replay_jobs'].includes(view))$('advanced-menu').open=true;
}
async function navigate(view){
  if(!state.scope)return;
  showPage(view);
  if(view==='overview')await loadOverview();
  else if(view==='replay_jobs')await loadJobs();
  else await loadRecords();
  notice('');
}
function applySummary(stats){
  state.stats=stats;
  const v3=stats.v3||{};
  $('raw').textContent=fmtNum(stats.raw);
  $('profiles-count').textContent=fmtNum(v3.profiles);
  $('events-count').textContent=fmtNum(v3.events);
  $('claims-count').textContent=fmtNum(v3.claims);
  $('pending-count').textContent=fmtNum(v3.pending_consolidation);
  $('running-status').textContent=stats.paused?'暂停':stats.last_error?'上次运行有错误':'正常';
  $('cursor').textContent=fmtNum(stats.cursor);
  $('calls').textContent=fmtNum(stats.today_usage?.calls);
  $('tokens').textContent=fmtNum((stats.today_usage?.input_tokens||0)+(stats.today_usage?.output_tokens||0));
  $('last-success').textContent=fmtDate(stats.last_success);
  $('last-error').hidden=!stats.last_error;
  $('last-error').textContent=stats.last_error?'最近错误：'+stats.last_error:'';
}
async function refreshSummary(){
  const r=await api('overview');
  applySummary({...r.stats,paused:r.paused});
  $('action-pause').textContent=r.paused?'恢复自动分析':'暂停自动分析';
  $('action-pause').dataset.paused=String(r.paused);
}
function runTitle(r){return r.v3_detail?'V3 · '+label(r.status):'旧版 · '+label(r.status);}
async function loadOverview(){
  await refreshSummary();
  const r=await api('runs');
  const list=$('recent-runs');list.replaceChildren();
  const rows=(r.items||[]).slice(0,5);
  if(!rows.length){list.append(el('p','还没有执行记录。','empty'));return;}
  for(const row of rows){
    const item=el('button',undefined,'compact-row');item.type='button';
    const body=el('div');body.append(el('strong',runTitle(row)),el('small',fmtDate(row.created_at)+(row.error?' · '+row.error:'')));
    item.append(body,el('span',row.v3_detail?'消息 '+fmtNum(row.v3_detail.analyzed):'变更 '+fmtNum(row.changes_count),'meta'));
    item.addEventListener('click',()=>work(async()=>{await navigate('runs');state.selected=String(row.id);renderRecords();await renderDetail();}));
    list.append(item);
  }
}
function recordTitle(row,view=state.view){
  switch(view){
    case 'v3_profiles':case 'v3_claims':return row.content||'（无正文）';
    case 'v3_events':return row.title||'未命名事件';
    case 'v3_coverage':return label(row.result);
    case 'runs':return runTitle(row)+(row.v3_detail?' · '+fmtNum(row.v3_detail.analyzed)+' 条消息':' · '+fmtNum(row.changes_count)+' 处变更');
    case 'history':return row.text||'['+(row.kind||'媒体')+']';
    case 'memories':return row.content;
    case 'candidates':return row.new_content||row.action;
    case 'topics':return row.title||row.summary;
    case 'observations':return row.content;
    case 'retrieval_audit':return '检索 '+(row.mode||'');
    default:return row.summary||row.content||row.title||'记录';
  }
}
function recordCaption(row,view=state.view){
  if(view==='v3_profiles'||view==='v3_claims')return (row.subject_id||'群公共')+' · '+label(row.claim_type);
  if(view==='v3_events')return label(row.event_type)+(row.anchor_date?' · '+row.anchor_date:'');
  if(view==='v3_coverage')return '处理结果';
  if(view==='runs')return fmtDate(row.created_at)+' · '+(row.provider||'');
  if(view==='history')return fmtDate(row.message_at)+' · '+(row.sender_id||'');
  return fmtDate(row.updated_at||row.created_at||row.message_at);
}
function recordId(row){return String(row.id??row.result??row.message_id??row.run_id??'');}
function recordStatus(row,view=state.view){
  if(view==='v3_coverage')return fmtNum(row.count)+' 条';
  return label(row.status||row.state||row.confidence||'');
}
async function loadRecords(){
  const view=state.view;
  const desc=TITLES[view]||[view,''];
  $('list-title').textContent=desc[0];$('list-subtitle').textContent=desc[1];
  const r=await api(view==='memories'?'list':view);
  state.rows=r.items||[];state.selected='';
  $('add').hidden=view!=='memories';
  const statuses=[...new Set(state.rows.map(x=>x.status||x.state||'').filter(Boolean))];
  const kinds=[...new Set(state.rows.map(x=>x.claim_type||x.kind||x.signal_type||x.event_type||'').filter(Boolean))];
  fillOptions($('filter-status'),statuses,'全部状态');
  fillOptions($('filter-kind'),kinds,'全部分类');
  $('query').value='';renderRecords();
  $('detail').replaceChildren(el('p','选择左侧记录，查看证据和详细状态。','empty'));
}
function fillOptions(select,items,allLabel){
  select.replaceChildren();
  const blank=el('option',allLabel);blank.value='';select.append(blank);
  for(const value of items){const option=el('option',label(value));option.value=value;select.append(option);}
  select.hidden=items.length===0;
}
function filteredRecords(){
  const q=$('query').value.trim().toLowerCase();
  const status=$('filter-status').value,kind=$('filter-kind').value;
  return state.rows.filter(row=>{
    if(status&&(row.status||row.state)!==status)return false;
    if(kind&&(row.claim_type||row.kind||row.signal_type||row.event_type)!==kind)return false;
    return !q||[row.content,row.title,row.summary,row.subject_id,row.sender_id,row.message_id,row.predicate]
      .some(value=>String(value||'').toLowerCase().includes(q));
  });
}
function renderRecords(){
  const box=$('items');box.replaceChildren();
  const rows=filteredRecords();
  $('record-total').textContent=fmtNum(rows.length)+' 条记录';
  if(!rows.length){box.append(el('p','没有符合条件的记录。','empty'));return;}
  for(const row of rows){
    const id=recordId(row);const b=el('button',undefined,'record');b.type='button';
    b.setAttribute('aria-pressed',String(state.selected===id));
    const head=el('div',undefined,'record-top');
    head.append(el('small',recordCaption(row)),el('span',recordStatus(row),'pill '+(row.status||row.state||'')));
    b.append(head,el('div',recordTitle(row),'record-body'),
      el('div',fmtDate(row.updated_at||row.created_at||row.message_at),'record-foot'));
    b.addEventListener('click',()=>work(async()=>{state.selected=id;renderRecords();await renderDetail();}));
    box.append(b);
  }
}
async function renderDetail(){
  const box=$('detail');box.replaceChildren();
  const row=state.rows.find(x=>recordId(x)===state.selected);
  if(!row){box.append(el('p','请选择一条记录。','empty'));return;}
  const view=state.view;
  if(view==='v3_profiles'||view==='v3_claims'){
    box.append(el('div',row.content,'detail-title'),
      metaRows([['主体',row.subject_id||'当前群'],['类别',label(row.claim_type)],
        ['状态',label(row.status)],['可信度',label(row.confidence)],
        ['证据属性',label(row.attribution)],['敏感度',label(row.sensitivity)],
        ['关联事件',row.event_id],['最后证据',fmtDate(row.last_supported)],['版本','r'+row.revision]]));
    const actions=el('div',undefined,'detail-actions');
    if(row.status==='candidate'||row.status==='review'){
      actions.append(button('批准为有效主张',async()=>{
        await api('v3_claim_approve',{id:row.id,revision:row.revision});
        await loadRecords();notice('已批准该主张。');
      },'primary'));
    }
    if(row.status!=='retracted'){
      actions.append(button('修订',async()=>openEditor('v3_edit',row)),
        button('撤销',async()=>{
          if(!await confirmAction('撤销这条主张？历史版本和原始证据仍会保留。'))return;
          await api('v3_claim_retract',{id:row.id,revision:row.revision});
          await loadRecords();notice('已撤销。');
        },'danger'));
    }
    box.append(actions,el('h4','原始证据'));
    const r=await api('v3_sources',{id:row.id});
    if(!(r.items||[]).length)box.append(el('p','暂未找到对应原文来源。','muted'));
    for(const e of r.items||[])box.append(sourceCard(e.text,e.sender_id+' · '+fmtDate(e.message_at)+' · '+label(e.relation)+' · '+e.message_id));
    const versions=await api('v3_claim_versions',{id:row.id});
    if((versions.items||[]).length){
      box.append(el('h4','修订历史'));
      for(const v of versions.items)box.append(sourceCard(v.previous_content,'r'+v.revision+' · '+v.actor+' · '+fmtDate(v.occurred_at)));
    }
    return;
  }
  if(view==='v3_events'){
    box.append(el('div',row.title,'detail-title'),el('p',row.summary||'暂无事件摘要。'),
      metaRows([['事件状态',label(row.state)],['类型',label(row.event_type)],
        ['时间标识',row.anchor_date],['首次证据',fmtDate(row.first_seen)],
        ['最近证据',fmtDate(row.last_seen)],['版本','r'+row.revision]]),el('h4','关联事实与任务'));
    const related=await api('v3_claims',{event_id:row.id});
    if(!(related.items||[]).length)box.append(el('p','暂无关联主张。','muted'));
    for(const c of related.items||[])box.append(sourceCard(c.content,label(c.claim_type)+' · '+label(c.status)));
    box.append(el('h4','原始群聊来源'));
    const sources=await api('v3_event_sources',{id:row.id});
    for(const e of sources.items||[])box.append(sourceCard(e.text,e.sender_id+' · '+fmtDate(e.message_at)));
    return;
  }
  if(view==='runs'){
    const info=row.v3_detail||row.v2_detail;
    box.append(el('div',runTitle(row),'detail-title'),
      metaRows([['时间',fmtDate(row.created_at)],['运行结果',label(row.status)],
        ['消息范围',row.from_cursor+' → '+row.to_cursor],
        ['已分析',info?.analyzed??info?.analyzed_messages??'—'],
        ['新增主张',info?.claims_written??'—'],['新增事件',info?.events_written??'—'],
        ['来源关联',info?.sources_added??'—'],['模型',row.provider]]));
    if(row.error)box.append(el('p','失败原因：'+row.error,'inline-warning'));
    if(info?.preview_json){
      let preview=info.preview_json;
      try{preview=JSON.parse(preview);}catch{}
      box.append(el('h4','本次预览提取结果'),codeDetails('展开事件、主张和消息覆盖详情',preview));
    }
    box.append(codeDetails('查看原始运行数据',row));return;
  }
  if(view==='memories'){
    box.append(el('div',row.content,'detail-title'),
      metaRows([['作用域',row.scope],['成员',row.subject_id],['类型',row.kind],['状态',label(row.status)],['版本','r'+row.revision]]));
    const actions=el('div',undefined,'detail-actions');
    if(row.status==='review')actions.append(button('批准',async()=>{
      await api('approve',{id:row.id,revision:row.revision});await loadRecords();
    },'primary'));
    if(row.status!=='deleted'){
      actions.append(button('编辑',async()=>openEditor('legacy_edit',row)),
        button('停用',async()=>{
          if(!await confirmAction('停用这条历史记忆？'))return;
          await api('disable',{id:row.id,revision:row.revision});await loadRecords();
        }),
        button('回滚一版',async()=>{
          if(!await confirmAction('回滚到上一版？'))return;
          await api('rollback',{id:row.id,revision:row.revision});await loadRecords();
        }),
        button('删除',async()=>{
          if(!await confirmAction('删除这条历史记忆？'))return;
          await api('delete',{id:row.id,revision:row.revision});await loadRecords();
        },'danger'));
    }
    box.append(actions,el('h4','来源'));
    const sr=await api('sources',{id:row.id});
    for(const v of sr.sources||[])box.append(sourceCard(v.text,v.sender_id+' · '+fmtDate(v.message_at||v.created_at)));
    const ver=await api('versions',{id:row.id});
    if((ver.items||[]).length)box.append(el('h4','历史版本'));
    for(const v of ver.items||[])box.append(sourceCard(v.previous_content,'r'+v.revision+' · '+v.action));
    return;
  }
  if(view==='candidates'){
    box.append(el('div',row.new_content||row.action,'detail-title'),
      metaRows([['类型',row.action],['状态',label(row.status)],['旧文本',row.old_content],['原因',row.reason]]));
    if(row.status==='review'){
      const a=el('div',undefined,'detail-actions');
      for(const [name,op] of [['批准','candidate_approve'],['拒绝','candidate_reject']]){
        a.append(button(name,async()=>{await api(op,{id:row.id});await loadRecords();},op==='candidate_approve'?'primary':'danger'));
      }box.append(a);
    }
    box.append(codeDetails('查看候选数据',row));return;
  }
  if(view==='history'){
    box.append(el('div',row.text||'（无文本）','detail-title'),
      metaRows([['作者',row.sender_id],['发送时间',fmtDate(row.message_at)],['类型',row.kind],['消息 ID',row.message_id]]));return;
  }
  if(view==='v3_coverage'){
    box.append(el('div',label(row.result),'detail-title'),
      metaRows([['消息数量',fmtNum(row.count)],['范围', '当前群 · V3 分析账本']]),
      el('p','“没有提取信号”只是本次模型未发现长期价值，不代表该消息以后不能重新评估。','muted'));return;
  }
  box.append(el('div',recordTitle(row),'detail-title'),codeDetails('查看完整记录',row));
}
async function loadJobs(){
  const r=await api('replay_jobs');
  state.jobs=r.items||[];
  const active=state.jobs.filter(x=>['running','pending','paused','failed'].includes(x.status)).length;
  $('job-count').hidden=active===0;$('job-count').textContent=String(active);
  $('tasks-total').textContent=fmtNum(state.jobs.length)+' 个任务';
  renderJobs();
  if(state.jobSelected)await renderJobDetail();
  else $('job-detail').replaceChildren(el('p','选择任务查看进度、错误原因及操作。','empty'));
}
function renderJobs(){
  const box=$('jobs-list');box.replaceChildren();
  if(!state.jobs.length){box.append(el('p','暂无历史分析任务。','empty'));return;}
  for(const row of state.jobs){
    const b=el('button',undefined,'record');b.type='button';
    b.setAttribute('aria-pressed',String(state.jobSelected===row.id));
    const heading=el('div',undefined,'record-top');
    heading.append(el('small',fmtDate(row.created_at)),el('span',label(row.status),'pill '+row.status));
    b.append(heading,el('div',(row.mode==='preview'?'预览':'正式分析')+' · 消息 ID '+row.start_raw_id+'–'+row.end_raw_id,'record-body'),
      el('div','已处理 '+fmtNum(row.messages_scanned)+' 条 · 下次 ID '+row.next_raw_id,'record-foot'));
    if(row.last_error)b.append(el('p','原因：'+row.last_error,'inline-warning'));
    b.addEventListener('click',()=>work(async()=>{state.jobSelected=row.id;renderJobs();await renderJobDetail();}));
    box.append(b);
  }
}
async function renderJobDetail(){
  const row=state.jobs.find(x=>x.id===state.jobSelected);
  const box=$('job-detail');box.replaceChildren();
  if(!row){box.append(el('p','选择任务查看详情。','empty'));return;}
  const range=Math.max(1,row.end_raw_id-row.start_raw_id+1);
  const progress=Math.min(100,Math.max(0,(row.next_raw_id-row.start_raw_id)*100/range));
  box.append(el('div',(row.mode==='preview'?'预览任务':'正式重分析')+' · '+label(row.status),'detail-title'),
    metaRows([['任务状态',label(row.status)],['ID 范围',row.start_raw_id+'–'+row.end_raw_id],
      ['已扫描文本',fmtNum(row.messages_scanned)+' 条'],['完成批次',fmtNum(row.batches_done)],
      ['下一消息 ID',fmtNum(row.next_raw_id)],['最近更新',fmtDate(row.updated_at)]]));
  const bar=el('div',undefined,'job-progress');const fill=el('span');
  fill.style.width=progress.toFixed(1)+'%';bar.append(fill);
  box.append(el('h4','ID 范围进度 · '+progress.toFixed(1)+'%'),bar);
  if(row.last_error)box.append(el('p','错误 / 暂停原因：'+row.last_error,'inline-warning'));
  const actions=el('div',undefined,'detail-actions');
  if(['running','pending'].includes(row.status))actions.append(button('暂停任务',async()=>{
    await api('replay_pause',{job_id:row.id});await loadJobs();notice('任务已暂停。');
  }));
  if(['failed','paused'].includes(row.status))actions.append(button('恢复任务',async()=>{
    await api('replay_resume',{job_id:row.id});await loadJobs();notice('已提交恢复请求，请刷新检查任务状态。');
  },'primary'));
  if(!['completed','cancelled'].includes(row.status))actions.append(button('取消剩余任务',async()=>{
    if(!await confirmAction('取消历史任务？已经正式提交的批次不会撤销。'))return;
    await api('replay_cancel',{job_id:row.id});await loadJobs();
  },'danger'));
  box.append(actions,codeDetails('查看任务原始数据',row));
}
function openEditor(mode,row=null){
  state.edit={mode,row};
  $('editor-title').textContent=mode==='legacy_add'?'新增人工记忆':'修改记忆内容';
  $('content').value=row?.content||'';
  $('new-fields').hidden=mode!=='legacy_add';
  $('editor').showModal();
}
async function connect(){
  await bridge.ready();
  const r=await bridge.apiPost('manage',{operation:'scopes'});
  state.scopes=r.scopes||[];
  const select=$('scope');select.replaceChildren();
  for(const s of state.scopes){
    const option=el('option',s.room+' · '+s.owner);option.value=s.room+'|'+s.owner;
    select.append(option);
  }
  if(!state.scopes.length){notice('暂无已归档的授权群。请确认插件已启用并配置群白名单。',true);return;}
  state.scope=state.scopes[0];
  await navigate('overview');
}
document.querySelectorAll('[data-view]').forEach(node=>node.addEventListener('click',()=>work(()=>navigate(node.dataset.view))));
$('scope').addEventListener('change',()=>work(async()=>{
  state.scope=state.scopes[$('scope').selectedIndex];await navigate(state.view);
}));
$('refresh').addEventListener('click',()=>work(()=>navigate(state.view)));
$('refresh-overview').addEventListener('click',()=>work(loadOverview));
$('list-refresh').addEventListener('click',()=>work(loadRecords));
$('history-refresh').addEventListener('click',()=>work(loadJobs));
$('more-runs').addEventListener('click',()=>work(()=>navigate('runs')));
$('action-history').addEventListener('click',()=>work(()=>navigate('replay_jobs')));
$('action-run').addEventListener('click',()=>work(async()=>{
  await api('run_now');notice('已安排新消息分析。稍后在“执行记录”中查看结果。');
}));
$('action-preview').addEventListener('click',()=>work(async()=>{
  await api('dry_run');notice('已安排下一批预览，不会写入长期记忆，但会产生模型费用。');
}));
$('action-profile').addEventListener('click',()=>work(async()=>{
  await api('v3_profile_consolidate');notice('已安排待巩固成员的画像综合。');
}));
$('action-backfill').addEventListener('click',()=>work(async()=>{
  const r=await api('backfill',{limit:500});
  await loadOverview();
  notice('Hook 补录：新增 '+fmtNum(r.imported)+' · 重复 '+fmtNum(r.duplicates)+' · 跳过 '+fmtNum(r.skipped));
}));
$('action-pause').addEventListener('click',()=>work(async()=>{
  const paused=$('action-pause').dataset.paused==='true';
  await api(paused?'resume':'pause');await loadOverview();
  notice(paused?'已恢复自动分析。':'已暂停自动分析。');
}));
$('query').addEventListener('input',renderRecords);
$('filter-status').addEventListener('change',renderRecords);
$('filter-kind').addEventListener('change',renderRecords);
$('add').addEventListener('click',()=>openEditor('legacy_add'));
$('cancel').addEventListener('click',()=>$('editor').close());
$('editor-form').addEventListener('submit',event=>{
  event.preventDefault();
  work(async()=>{
    const {mode,row}=state.edit||{};
    const content=$('content').value.trim();
    if(!content)throw new Error('记忆内容不能为空');
    if(mode==='v3_edit')await api('v3_claim_edit',{id:row.id,revision:row.revision,content});
    else if(mode==='legacy_edit')await api('edit',{id:row.id,revision:row.revision,content});
    else if(mode==='legacy_add')await api('add',{content,kind:$('kind').value,subject:$('subject').value.trim(),topic:$('topic').value.trim()});
    else throw new Error('编辑状态无效');
    $('editor').close();await loadRecords();notice('修改已保存。');
  });
});
$('history-form').addEventListener('submit',event=>{
  event.preventDefault();
  work(async()=>{
    const from_id=Number($('replay-from').value),to_id=Number($('replay-to').value);
    const mode=$('replay-mode').value;
    if(!Number.isSafeInteger(from_id)||from_id<1||!Number.isSafeInteger(to_id)||to_id<0)
      throw new Error('消息范围必须是有效的非负整数。');
    const p=await api('replay_prepare',{from_id,to_id});
    const message='本次将在当前群重新分析 '+fmtNum(p.rows)+' 条归档消息。'
      +'\n范围：ID '+p.from_id+'～'+p.to_id
      +'\n执行：'+(mode==='preview'?'只预览，不写入':'正式提交到 V3 记忆库')
      +'\n模型调用会消耗每日额度。确认开始？';
    if(!await confirmAction(message))return;
    const r=await api('replay_start',{from_id,to_id,mode,confirmed:true});
    state.jobSelected=r.id||r.job_id||'';
    await loadJobs();notice('历史任务已创建。状态会在任务详情中显示。');
  });
});
work(connect);
