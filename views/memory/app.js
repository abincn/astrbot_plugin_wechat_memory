const bridge=window.AstrBotPluginView;
const $=id=>document.getElementById(id);
const state={scopes:[],scope:null,view:'overview',rows:[],selected:'',jobSelected:'',jobs:[],busy:false,stats:null,edit:null};
const TITLES={
  deferred_list:['延期重评','缺少原文的记录需先补录；长文本可分片重评，生产游标不回退。'],
  model_calls:['模型费用明细','逐次预留与结算；待核对表示中断后无法确认实际用量。'],
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
  disabled:'未启用',denied:'无权执行',retryable:'待缩批重试',transient:'上游暂时不可用',empty:'无待处理内容',
  insufficient:'未达到处理门槛',budget_exceeded:'额度不足',busy:'有任务运行中',oversize:'内容过长',
  person_fact:'个人背景',preference:'偏好',behavior_pattern:'行为观察',event_detail:'事件细节',
  long_term:'长期画像',recent:'近期事项',event:'事件信息',uncertain:'待判断',unclassified:'待分类',
  settled:'已结算',needs_reconciliation:'用量待核对',reserved:'已预留',
  active:'启用',candidate:'候选',review:'待审核',reported:'第三方说法',
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
function memberName(row,field='subject_id'){return row[field.replace(/_id$/,'')+'_name']||(row[field]?'昵称未取得':'当前群');}
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
  $('calls').title='额度重置：'+(stats.budget?.next_reset_local||'—');
  $('budget-reset').textContent=stats.budget?.next_reset_local||'—';
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
function runKind(r){
  const mode=r.v3_detail?.mode;
  return ({live:'新消息分析',preview:'新消息预览',replay_apply:'历史重分析批次',replay_preview:'历史预览批次',profile_consolidation:'画像巩固'})[mode]||(r.status==='running'?'正在分析':r.v2_detail?'旧版分析':'分析执行');
}
function runTitle(r){return runKind(r)+' · '+label(r.status);}
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
    case 'model_calls':return label(row.status)+' · '+row.purpose;
    case 'deferred_list':return row.reason+' · '+(row.content||'缺少原文，需重新导入');
    case 'v3_profiles':return row.display_name+' · '+row.content;
    case 'v3_claims':return row.content||'（无正文）';
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
  if(view==='v3_profiles')return row.display_name+' · '+row.claims.length+' 条资料';
  if(view==='v3_claims')return memberName(row)+' · '+label(row.claim_type);
  if(view==='v3_events')return label(row.event_type)+(row.anchor_date?' · '+row.anchor_date:'');
  if(view==='v3_coverage')return '处理结果';
  if(view==='runs')return fmtDate(row.created_at)+' · '+(row.provider||'');
  if(view==='history')return fmtDate(row.message_at)+' · '+memberName(row,'sender_id');
  return fmtDate(row.updated_at||row.created_at||row.message_at);
}
function recordId(row){return String(row.id??row.result??row.message_id??row.run_id??'');}
function recordStatus(row,view=state.view){
  if(view==='v3_coverage')return fmtNum(row.count)+' 条';
  return label(row.status||row.state||row.confidence||'');
}
async function loadRecords(append=false,preserve=false){
  const view=state.view;
  const desc=TITLES[view]||[view,''];
  $('list-title').textContent=desc[0];$('list-subtitle').textContent=desc[1];
  if(!preserve&&!append){$('query').value='';$('filter-status').value='';$('filter-kind').value='';}
  state.paged=['memories','v3_profiles','v3_claims','v3_events','history','runs','topics','observations','retrieval_audit','deferred_list','model_calls'].includes(view);
  const r=await api(view==='memories'?'list':view,{paged:state.paged,limit:50,cursor:append?state.nextCursor:'',query:$('query').value,status:$('filter-status').value,kind:$('filter-kind').value});
  state.nextCursor=r.next_cursor;state.hasMore=r.has_more;
  $('load-more').hidden=!r.has_more;
  state.rows=append?[...state.rows,...(r.items||[])]:r.items||[];state.selected='';
  $('add').hidden=view!=='memories';
  const statuses=[...new Set(state.rows.map(x=>x.status||x.state||'').filter(Boolean))];
  const kinds=[...new Set(state.rows.map(x=>x.claim_type||x.kind||x.signal_type||x.event_type||'').filter(Boolean))];
  if(!preserve&&!append){fillOptions($('filter-status'),statuses,'全部状态');fillOptions($('filter-kind'),kinds,'全部分类');}
  renderRecords();
  $('detail').replaceChildren(el('p','选择左侧记录，查看证据和详细状态。','empty'));
}
function fillOptions(select,items,allLabel){
  select.replaceChildren();
  const blank=el('option',allLabel);blank.value='';select.append(blank);
  for(const value of items){const option=el('option',label(value));option.value=value;select.append(option);}
  select.hidden=items.length===0;
}
function filteredRecords(){
  if(state.paged)return state.rows;
  const q=$('query').value.trim().toLowerCase();
  const status=$('filter-status').value,kind=$('filter-kind').value;
  return state.rows.filter(row=>{
    if(status&&(row.status||row.state)!==status)return false;
    if(kind&&(row.claim_type||row.kind||row.signal_type||row.event_type)!==kind)return false;
    return !q||[row.content,row.title,row.summary,row.display_name,row.subject_name,row.sender_name,row.subject_id,row.sender_id,row.message_id,row.predicate]
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
      el('div',(row.last_supported?'最后支持：':'记录更新：')+fmtDate(row.last_supported||row.updated_at||row.created_at||row.message_at),'record-foot'));
    b.addEventListener('click',()=>work(async()=>{state.selected=id;renderRecords();await renderDetail();}));
    box.append(b);
  }
}
async function renderDetail(){
  const box=$('detail');box.replaceChildren();
  const row=state.rows.find(x=>recordId(x)===state.selected);
  if(!row){box.append(el('p','请选择一条记录。','empty'));return;}
  const view=state.view;
  if(view==='deferred_list'){
    box.append(el('h3',row.reason),sourceCard(row.content||'缺少可分析原文'),metaRows([['原话时间',fmtDate(row.message_at)],['自动尝试',row.auto_attempts]]));
    for(const mode of ['preview','apply'])box.append(button(mode==='preview'?'预览重评':'正式重评',async()=>{
      if(!await confirmAction('本操作会调用模型并计入额度；'+(mode==='preview'?'仅预览。':'正式写入主张和证据。')))return;
      await api('deferred_replay',{raw_id:row.id,mode});notice('已创建重评任务，请到历史分析查看进度。');
    }));return;
  }
  if(view==='model_calls'){
    box.append(metaRows([['用途',row.purpose],['结算状态',row.status],['预留输入',row.reserved_input],['预留输出',row.reserved_output],['输入用量',row.actual_input],['输出用量',row.actual_output],['用量类型',row.estimated?'估算':'模型返回'],['UTC记账日',row.day],['请求时间',fmtDate(row.created_at)]]));return;
  }
  if(view==='v3_profiles'){
    box.append(el('h3',row.display_name),el('p','账号：'+row.subject_id));
    const name=el('input');name.placeholder='管理员确认的称呼';
    box.append(name,button('添加称呼',async()=>{
      await api('alias_add',{subject_id:row.subject_id,name:name.value});await loadRecords();
    }));
    for(const alias of row.aliases){
      box.append(button('称呼来源：'+alias.name,async()=>{
        const sources=await api('alias_sources',{id:alias.id});
        box.append(el('p','记录来源：'+alias.source));
        for(const source of sources.items||[])box.append(sourceCard(source.text,memberName(source,'sender_id')+' · '+fmtDate(source.message_at)));
      }));
      if(alias.status==='candidate')box.append(button('确认称呼：'+alias.name,async()=>{
        await api('alias_status',{id:alias.id,revision:alias.revision,status:'confirmed'});await loadRecords();
      }));
      box.append(button('撤销称呼：'+alias.name,async()=>{
      await api('alias_status',{id:alias.id,revision:alias.revision,status:'retracted'});await loadRecords();
    }));}
    box.append(button('预览画像分类',async()=>{
      const preview=await api('profile_classify_preview');
      const items=preview.items.filter(c=>row.claims.some(old=>old.id===c.id));
      const area=el('div');
      for(const c of items){
        const select=el('select');
        for(const value of ['long_term','recent','event','uncertain']){
          const option=el('option',label(value));option.value=value;select.append(option);
        }
        select.value=c.purpose;select.addEventListener('change',()=>c.purpose=select.value);
        area.append(el('p',c.content),select);
      }
      area.append(button('确认应用分类',async()=>{
        await api('profile_classify_apply',{items});await loadRecords();
      }));box.append(area);
    }));
    box.append(el('p','已确认称呼：'+row.aliases.filter(a=>a.status==='confirmed').map(a=>a.name).join('、')));
    for(const purpose of ['long_term','recent','event','uncertain','unclassified']){
      box.append(el('h4',label(purpose)));
      for(const c of row.claims.filter(c=>c.purpose===purpose)){
        box.append(sourceCard(c.content,label(c.status)+' · '+label(c.confidence)+' · 最后支持 '+fmtDate(c.last_supported)));
        box.append(button('查看证据',async()=>{
          const r=await api('v3_sources',{id:c.id});
          const detail=el('div');
          for(const e of r.items||[])detail.append(sourceCard(e.text,memberName(e,'sender_id')+' · 原话时间 '+fmtDate(e.message_at)));
          box.append(detail);
        }));
      }
    }
    return;
  }
  if(view==='v3_claims'){
    box.append(el('div',row.content,'detail-title'),
      metaRows([['主体',memberName(row)],['主体账号 ID',row.subject_id],['类别',label(row.claim_type)],
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
    for(const e of r.items||[])box.append(sourceCard(e.text,memberName(e,'sender_id')+' · '+fmtDate(e.message_at)+' · '+label(e.relation)+' · '+e.message_id+(e.spans?.length?' · 原文片段 '+e.spans.map(s=>s.start_offset+'～'+s.end_offset).join('、'):'')));
    const links=await api('claim_links',{id:row.id});
    for(const link of links.items||[]){
      box.append(sourceCard(link.target_content,'关联：'+link.relation+' · '+link.status));
      if(link.status==='pending')for(const action of (link.relation==='possible_duplicate'?['keep_both','supersede_target','merge_into_target']:['keep_both','supersede_target']))box.append(button(action==='keep_both'?'保留双方说法':action==='merge_into_target'?'确认等价，合并证据到旧主张':'将关联旧主张标记已替代',async()=>{
        if(!await confirmAction('确认处理这项冲突？此操作保留原文与历史版本。'))return;
        await api('claim_link_resolve',{id:row.id,target_id:link.target_id,revision:row.revision,target_revision:link.target_revision,action});await loadRecords();
      }));
    }
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
    for(const e of sources.items||[])box.append(sourceCard(e.text,memberName(e,'sender_id')+' · '+fmtDate(e.message_at)));
    return;
  }
  if(view==='runs'){
    const info=row.v3_detail||row.v2_detail;
    box.append(el('div',runTitle(row),'detail-title'),
      metaRows([['时间',fmtDate(row.created_at)],['运行结果',label(row.status)],
        ['消息范围',row.from_cursor+' → '+row.to_cursor],
        ['流程已处理（非人工确认）',info?.analyzed??info?.analyzed_messages??'—'],
        ['新增主张',info?.claims_written??'—'],['新增事件',info?.events_written??'—'],
        ['来源关联',info?.sources_added??'—'],['模型',row.provider]]));
    if(row.error)box.append(el('p','失败原因：'+row.error,'inline-warning'));
    if(info?.preview_json){
      let preview=info.preview_json;
      try{preview=JSON.parse(preview);}catch{}
      const rejected=Array.isArray(preview?.rejected_claims)?preview.rejected_claims:[];
      const corrected=Array.isArray(preview?.corrected_claims)?preview.corrected_claims:[];
      if(corrected.length){
        const correctedPreview=String(row.v3_detail?.mode||'').includes('preview');
        box.append(el('p','本轮有 '+corrected.length+
          ' 条被错误标为本人自述的主张，已按真实发送者归为未经确认的第三方说法'
          +(correctedPreview?'（预览尚未写入）。':'（已按候选记录入库）。'),'inline-warning'));
        box.append(el('h4','安全调整的第三方说法'));
        for(const item of corrected.slice(0,20)){
          box.append(sourceCard(item.content||'待核实第三方说法',
            '说话者：'+memberName(item,'speaker_id')+' · 被描述者：'+memberName(item)
            +' · 消息 ID：'+(item.source_message_ids||[]).join(', ')));
        }
      }
      if(rejected.length){
        const affected=new Set(rejected.flatMap(item=>item.source_message_ids||[]));
        const previewOnly=String(row.v3_detail?.mode||'').includes('preview');
        box.append(el('p','本轮有 '+rejected.length+' 条主张的身份或来源无法确认，涉及 '
          +affected.size+' 条原始消息；'
          +(previewOnly?'当前仅预览，不修改消息状态。':'已在消息分析账本标记待重分析。')
          +'这些主张没有被当作本人事实保存。','inline-warning'));
        box.append(el('h4','需要重新核对的来源'));
        for(const item of rejected.slice(0,20)){
          box.append(sourceCard(item.content||'未采纳的提取提案',
            (item.reason||'归属待复核')+' · 消息 ID：'+(item.source_message_ids||[]).join(', ')));
        }
      }
      box.append(el('h4',row.v3_detail?.mode?.includes('preview')?'本次预览提取结果':'本次提取明细'),
        codeDetails('展开提取协议和来源明细',preview));
    }
    box.append(codeDetails('查看原始运行数据',row));return;
  }
  if(view==='memories'){
    box.append(el('div',row.content,'detail-title'),
      metaRows([['作用域',row.scope],['成员',memberName(row)],['成员账号 ID',row.subject_id],['类型',row.kind],['状态',label(row.status)],['版本','r'+row.revision]]));
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
    for(const v of sr.sources||[])box.append(sourceCard(v.text,memberName(v,'sender_id')+' · '+fmtDate(v.message_at||v.created_at)));
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
      metaRows([['作者',memberName(row,'sender_id')],['作者账号 ID',row.sender_id],['发送时间',fmtDate(row.message_at)],['类型',row.kind],['消息 ID',row.message_id],['原文召回',row.disclosure==='public'?'已确认可公开':'未审核，不自动注入']]));
    box.append(button(row.disclosure==='public'?'撤销原文公开许可':'确认原文可在本群召回',async()=>{
      if(!await confirmAction('请确认整条原文适合当前群受众；受限主张与高风险内容仍会被拦截。'))return;
      await api('raw_disclosure',{id:row.id,disclosure:row.disclosure==='public'?'unreviewed':'public'});await loadRecords();
    }));return;
  }
  if(view==='v3_coverage'){
    box.append(el('div',label(row.result),'detail-title'),
      metaRows([['消息数量',fmtNum(row.count)],['范围', '当前群 · V3 分析账本']]),
      el('p','“没有提取信号”只是本次模型未发现长期价值，不代表该消息以后不能重新评估。','muted'));return;
  }
  box.append(el('div',recordTitle(row),'detail-title'),codeDetails('查看完整记录',row));
}
async function openExecution(id){
  const r=await api('run_detail',{id});
  await navigate('runs');
  if(!state.rows.some(row=>String(row.id)===String(id)))state.rows.unshift(r.item);
  state.selected=String(id);renderRecords();await renderDetail();
}
async function loadHistoryExecutions(append=false){
  const scope=state.scope;
  const r=await api('runs',{paged:true,limit:50,cursor:append?state.executionCursor:''});
  if(scope!==state.scope)return;
  state.executionCursor=r.next_cursor;
  state.executions=append?[...(state.executions||[]),...(r.items||[])]:r.items||[];
  const box=$('history-executions');box.replaceChildren();
  $('history-executions-more').hidden=!r.has_more;
  $('history-execution-count').textContent='已加载 '+fmtNum(state.executions.length)+' 条';
  if(!state.executions.length)box.append(el('p','暂无执行记录。分析新消息后可在这里查看结果。','empty'));
  for(const row of state.executions){
    const b=el('button',undefined,'record');b.type='button';
    b.append(el('small',fmtDate(row.created_at)),el('strong',runTitle(row)),
      el('div','消息范围 '+row.from_cursor+' → '+row.to_cursor+(row.v3_detail?' · 本批处理 '+fmtNum(row.v3_detail.analyzed)+' 条':''),'record-foot'));
    if(row.error)b.append(el('p',row.error,'inline-warning'));
    b.addEventListener('click',()=>work(()=>openExecution(row.id)));box.append(b);
  }
}
function watchAnalysis(requestId,scope){
  const scopeKey=s=>s&&[s.platform,s.owner,s.room].join('|');let attempts=0;
  async function poll(){
    if(scopeKey(scope)!==scopeKey(state.scope))return;
    if(state.busy){setTimeout(poll,500);return;}
    try{
      const response=await bridge.apiPost('manage',{...scope,operation:'run_status',request_id:requestId});
      if(scopeKey(scope)!==scopeKey(state.scope))return;
      if(state.view==='replay_jobs')await work(()=>loadHistoryExecutions());
      if(scopeKey(scope)!==scopeKey(state.scope))return;
      if(response.status==='finished'){
        const result=response.result||{};
        const success=['committed','preview','empty'].includes(result.status);
        const message=result.status==='committed'?'新消息分析已完成并提交，可点击执行记录查看来源和结果。':result.status==='preview'?'新消息预览已完成，尚未写入正式记忆。':result.status==='empty'?'本次没有可直接分析的新文本，未调用模型；待处理媒体和缺失原文可在延期重评中查看。':result.reason||label(result.status)||'本次分析未完成，请查看执行记录。';
        notice(message,!success);return;
      }
      if(response.status==='unavailable'){notice(response.reason,true);return;}
      if(++attempts>=240){notice('分析仍未结束，请刷新分析执行记录查看最新状态。');return;}
      setTimeout(poll,1500);
    }catch(error){notice(error?.message||'查询分析状态失败，请刷新分析执行记录。',true);}
  }
  setTimeout(poll,500);
}
async function startAnalysis(dryRun){
  const scope=state.scope;const r=await api(dryRun?'dry_run':'run_now');
  await navigate('replay_jobs');
  notice(dryRun?'已提交新消息预览，完成后将在分析执行记录中显示。':'已提交新消息分析，完成后将在分析执行记录中显示。');
  if(r.request_id)watchAnalysis(r.request_id,scope);
}

async function loadJobs(){
  const [r]=await Promise.all([api('replay_jobs'),loadHistoryExecutions()]);
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
      ['阻塞原因',row.blocked_reason],['下次可恢复',fmtDate(row.next_attempt)],['已尝试批次',row.attempts],['下一消息 ID',fmtNum(row.next_raw_id)],['最近更新',fmtDate(row.updated_at)]]));
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
async function loadScopes(refresh=false){
  const previous=state.scope;
  const r=await bridge.apiPost('manage',{operation:'scopes',refresh});
  state.scopes=r.scopes||[];
  const select=$('scope');select.replaceChildren();
  for(const s of state.scopes){
    const option=el('option',s.display_name||s.room_name||('群名未取得（'+s.room+'）'));
    option.value=s.platform+'|'+s.owner+'|'+s.room;
    option.title='群 ID：'+s.room+' · 微信账号：'+s.owner;
    select.append(option);
  }
  if(!state.scopes.length){state.scope=null;notice('暂无已归档的授权群。请确认插件已启用并配置群白名单。',true);return false;}
  const index=previous?state.scopes.findIndex(s=>s.platform===previous.platform&&s.owner===previous.owner&&s.room===previous.room):-1;
  select.selectedIndex=index>=0?index:0;
  state.scope=state.scopes[select.selectedIndex];select.title=select.selectedOptions[0]?.title||'';
  return true;
}
async function connect(){
  await bridge.ready();
  if(await loadScopes())await navigate('overview');
}

document.querySelectorAll('[data-view]').forEach(node=>node.addEventListener('click',()=>work(()=>navigate(node.dataset.view))));
$('scope').addEventListener('change',()=>work(async()=>{
  state.scope=state.scopes[$('scope').selectedIndex];$('scope').title=$('scope').selectedOptions[0]?.title||'';await navigate(state.view);
}));
$('refresh').addEventListener('click',()=>work(async()=>{if(await loadScopes(true))await navigate(state.view);}));
$('refresh-overview').addEventListener('click',()=>work(loadOverview));
$('list-refresh').addEventListener('click',()=>work(()=>loadRecords()));
$('load-more').addEventListener('click',()=>work(()=>loadRecords(true,true)));
let searchTimer;
function scheduleSearch(delay=400){
  clearTimeout(searchTimer);const view=state.view;
  searchTimer=setTimeout(()=>{if(state.view!==view)return;if(state.busy){scheduleSearch(50);return;}work(()=>loadRecords(false,true));},delay);
}
$('query').addEventListener('input',()=>scheduleSearch());
for(const id of ['filter-status','filter-kind'])$(id).addEventListener('change',()=>work(()=>loadRecords(false,true)));
$('history-refresh').addEventListener('click',()=>work(loadJobs));
$('history-executions-more').addEventListener('click',()=>work(()=>loadHistoryExecutions(true)));
$('more-runs').addEventListener('click',()=>work(()=>navigate('runs')));
$('action-history').addEventListener('click',()=>work(()=>navigate('replay_jobs')));
$('action-run').addEventListener('click',()=>work(()=>startAnalysis(false)));
$('action-preview').addEventListener('click',()=>work(()=>startAnalysis(true)));
$('action-profile').addEventListener('click',()=>work(async()=>{
  await api('v3_profile_consolidate');notice('已安排待巩固成员的画像综合。');
}));
$('action-backfill').addEventListener('click',()=>work(async()=>{
  const r=await api('backfill',{limit:500});
  await loadOverview();
  notice('Hook 补录：新增 '+fmtNum(r.imported)+' · 重复 '+fmtNum(r.duplicates)+' · 跳过 '+fmtNum(r.skipped));
}));
$('action-media').addEventListener('click',()=>work(async()=>{
  await api('media_run');
  notice('已请求处理待解析媒体，结果可在执行记录中查看。');
}));
$('action-export').addEventListener('click',()=>work(async()=>{
  const session=await api('export_prepare');
  const chunks=[JSON.stringify({format:'wechat-memory-jsonl',scope:state.scope,created_at:session.created_at,note:'管理导出，非数据库备份'})+'\n'];
  const counts={};let position=[0,0];
  try{
    while(true){
      const page=await api('export_next',{token:session.token,position});
      for(const line of page.lines){const entry=JSON.parse(line);counts[entry.table]=(counts[entry.table]||0)+1;chunks.push(line+'\n');}
      position=page.position;
      notice('正在生成导出：'+Object.values(counts).reduce((a,b)=>a+b,0)+' 条记录');
      if(page.done)break;
    }
    chunks.push(JSON.stringify({counts})+'\n');
  }finally{await api('export_cancel',{token:session.token});}
  const blob=new Blob(chunks,{type:'application/x-ndjson'});
  const url=URL.createObjectURL(blob);
  const link=el('a');link.href=url;
  link.download='wechat-memory-'+state.scope.room.replace(/[^a-zA-Z0-9.@-]/g,'_')+'.jsonl';
  document.body.append(link);link.click();link.remove();URL.revokeObjectURL(url);
  notice('已生成当前群的记忆导出文件，请妥善保管其中的个人背景信息。');
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
