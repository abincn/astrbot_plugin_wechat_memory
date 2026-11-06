import {state,$,api,element,button,perform,detail,labels} from './api.js';
import {memberCard} from './members.js';
import {taskCard} from './task-view.js';

const views={overview:'概览',members:'成员画像',claims:'事实与记忆',events:'群事件',messages:'原始群聊',tasks:'分析任务',deferred:'延期消息',aliases:'称呼审核',links:'冲突审核',calls:'模型用量',audits:'检索记录',settings:'导入与设置'};
let page=null;
async function load(append=false){
  $('title').textContent=views[state.view];
  $('actions').replaceChildren();
  $('more').hidden=true;
  if(!append){state.cursor='';$('content').replaceChildren();}
  for(const node of $('nav').children)node.setAttribute('aria-current',String(node.dataset.view===state.view));
  if(state.view==='overview'){
    const counts=await api('overview');
    for(const [key,label] of Object.entries({messages:'原始消息',claims:'事实与记忆',events:'群事件',tasks:'分析任务',deferred:'延期消息'}))$('content').append(element('p',`${label}：${counts[key]}`));
    for(const [kind,label,preview] of [['new','分析新消息',false],['new','预览新消息',true],['history','分析全部历史',false]]){
      $('actions').append(button(label,async()=>{await api('submit',{kind,preview});state.view='tasks';await load();}));
    }
    return;
  }
  if(state.view==='tasks')$('actions').append(button('更新任务状态',()=>load()));
  if(state.view==='settings'){
    $('content').append(element('p','导入旧插件数据库或 Hook Inbox，旧文件保持只读。导入不会自动调用模型；每次最多500条，可重复继续。'),
      button('选择服务器上的导入路径',async()=>{
        const path=prompt('数据库在 AstrBot 服务器或容器内的绝对路径');if(path===null)return;
        detail(await api('import',{path}));
      }),button('完整导出到服务器文件',async()=>detail(await api('export'))),
      button('暂停当前群',async()=>{await api('pause',{paused:true});}),
      button('恢复当前群',async()=>{await api('pause',{paused:false});}),
      button('恢复成员画像资格',async()=>{
        const member=prompt('成员账号ID');if(member===null)return;
        await api('privacy',{member,opted_out:false});
      }));
    return;
  }
  page=await api(state.view,{cursor:state.cursor,query:$('query').value});
  for(const row of page.items){
    let node;
    if(state.view==='members')node=memberCard(row,load);
    else if(state.view==='tasks')node=taskCard(row,load);
    else{
      node=element('article','');
      const title=row.content||row.title||row.text||row.name||row.reason||row.id||row.raw_id;
      const person=row.subject_name||row.sender_name||row.member_name;
      if(person)node.append(element('p',person,'meta'));
      node.append(element('h3',String(title)),element('p',`${labels[row.status]||row.status||''} ${labels[row.purpose]||''}`,'meta'));
      if(row.at)node.append(element('p',new Date(row.at*1000).toLocaleString(),'meta'));
      if(state.view==='claims'){
        node.append(button('原话与来源时间',async()=>detail(await api('sources',{id:row.id}))),button('启用已审核记忆',async()=>{await api('claim_edit',{id:row.id,revision:row.revision,status:'active'});await load();}),button('编辑',async()=>{
          const content=prompt('修改事实（将保留旧版本）',row.content);if(content===null)return;
          await api('claim_edit',{id:row.id,revision:row.revision,content});await load();
        }),button('分类',async()=>{
          const purpose=prompt('long_term 长期画像 / recent 近期事项 / event 事件 / uncertain 待判断',row.purpose);if(purpose===null)return;
          await api('claim_edit',{id:row.id,revision:row.revision,purpose});await load();
        }),button('撤回',async()=>{await api('claim_edit',{id:row.id,revision:row.revision,status:'deleted'});await load();}));
      }
      if(state.view==='events')node.append(button('事件来源',async()=>detail(await api('event_sources',{id:row.id}))),button('修订摘要',async()=>{
        const content=prompt('修订事件摘要（此后自动分析不会覆盖人工修订）',row.content);if(content===null)return;
        await api('event_edit',{id:row.id,revision:row.revision,content});await load();
      }));
      if(state.view==='aliases')for(const [status,label] of [['confirmed','确认称呼'],['retracted','撤销称呼']])node.append(button(label,async()=>{await api('alias_edit',{id:row.id,revision:row.revision,status});await load();}));
      if(state.view==='deferred')node.append(button('重评此消息',async()=>{await api('submit',{kind:'deferred',raw_id:row.raw_id});state.view='tasks';await load();}));
      if(state.view==='links')node.append(button('审核关联',async()=>{
        const values=prompt('输入来源与目标记忆的 revision，用逗号分隔（先查看记忆详情）');if(values===null)return;
        const action=prompt('keep_both 保留双方 / merge 合并来源证据到目标 / replace 用来源替代目标','keep_both');if(action===null)return;
        await api('link_resolve',{id:row.id,revisions:values.split(',').map(Number),action});await load();
      }));
      if(state.view==='messages')node.append(button('限制此原文的记忆召回',async()=>{await api('disclose',{raw_id:row.id,disclosure:'restricted'});await load();}));
      node.append(button('记录详情',async()=>detail(row)));
    }
    $('content').append(node);
  }
  $('more').hidden=!page.has_more;
  if(!page.items.length)$('content').append(element('p','暂无记录。'));
  if(state.view==='claims')$('actions').append(button('添加人工记忆',async()=>{
    const content=prompt('记忆内容');if(content===null)return;
    const member=prompt('成员ID，群内共同记忆留空','');if(member===null)return;
    await api('manual_add',{member,content});await load();
  }));
}
async function scopes(){
  const previous=state.scope?.scope;
  const rows=await api('scopes');
  $('scope').replaceChildren();
  for(const row of rows){const node=element('option',row.name||`群名未取得 · ${row.room}`);node.value=row.scope;node.title=`${row.owner} / ${row.room}`;$('scope').append(node);}
  state.scope=rows.find(r=>r.scope===previous)||rows[0];
  if(!state.scope){$('notice').textContent='暂无授权群，请配置 allowed_rooms 并连接微信账号或先归档消息。';return;}
  $('scope').value=state.scope.scope;
  await load();
}
for(const [view,label] of Object.entries(views)){
  const node=button(label,async()=>{state.view=view;$('more').hidden=true;await load();});node.dataset.view=view;$('nav').append(node);
}
$('scope').onchange=()=>perform($('scope'),async()=>{
  const rows=await api('scopes');state.scope=rows.find(r=>r.scope===$('scope').value);await load();
});
$('search').onclick=()=>perform($('search'),()=>load());
$('more').onclick=()=>perform($('more'),async()=>{state.cursor=page.next_cursor;await load(true);});
$('refresh').onclick=()=>perform($('refresh'),async()=>{const result=await api('contacts');if(result.status!=='updated')throw new Error(result.reason);await scopes();});
$('close-detail').onclick=()=>$('detail').close();
await window.AstrBotPluginView.ready();
await perform($('refresh'),scopes);
