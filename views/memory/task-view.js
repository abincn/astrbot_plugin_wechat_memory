import {api,button,element,labels,detail} from './api.js';

export function taskCard(row, reload) {
  const node=element('article','');
  node.append(element('h3',`${labels[row.kind]} · ${labels[row.status]}`),
    element('p',`${new Date(row.created_at*1000).toLocaleString()} · 已处理 ${row.processed} 条 · 游标 ${row.cursor}`,'meta'),
    element('p',row.error.message||''),button('执行、调用与预览详情',async()=>detail(await api('task',{task_id:row.id}))));
  for(const [action,label,states] of [['resume','恢复',['paused','failed']],['pause','暂停',['queued','running']],['cancel','取消',['queued','running','paused']]]) {
    if(states.includes(row.status))node.append(button(label,async()=>{await api('control',{task_id:row.id,action});await reload();}));
  }
  if(row.preview&&row.status==='completed')node.append(button('重新调用模型并正式应用',async()=>{
    if(!confirm('正式应用会重新调用模型并消耗预算，结果可能与预览不同。继续？'))return;
    detail(await api('submit',{kind:row.kind,member:row.member,start:row.cursor,end:row.end_id}));
  }));
  return node;
}
