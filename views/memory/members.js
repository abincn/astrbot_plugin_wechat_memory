import {api,button,element,labels,detail} from './api.js?v=0.5.0-alpha.4';

export function memberCard(row, reload) {
  const node = element('article','');
  node.append(element('h3',row.name), element('p',row.member,'meta'));
  for (const purpose of ['long_term','recent','event','uncertain']) {
    const claims = row.claims.filter(c=>c.purpose===purpose);
    if (claims.length) node.append(element('h4',labels[purpose]));
    for (const claim of claims) {
      node.append(element('p',claim.content),button('查看来源',async()=>detail(await api('sources',{id:claim.id}))));
    }
  }
  node.append(button('身份与称呼',async()=>detail(await api('aliases',{member:row.member}))),
    button('设置显示名称候选',async()=>{
      const name=prompt('管理员显示名称，确认后优先于群名片');if(name===null)return;
      await api('alias_add',{member:row.member,name,alias_kind:'admin'});
      detail(await api('aliases',{member:row.member}));
    }),button('添加称呼候选',async()=>{
      const name=prompt('请输入称呼'); if(name===null)return;
      await api('alias_add',{member:row.member,name});
      detail(await api('aliases',{member:row.member}));
    }),button('巩固此成员画像',async()=>{detail(await api('submit',{kind:'profile',member:row.member}));}),
    button('退出画像',async()=>{await api('privacy',{member:row.member,opted_out:true});await reload();}),
    button('清除本人原文与派生资料',async()=>{
      if(!confirm(`清除 ${row.name} 在当前群的原文与派生资料，并退出画像？`))return;
      await api('privacy',{member:row.member,opted_out:true,erase:true});await reload();
    }));
  return node;
}
