export const state = {scope:null, view:'overview', cursor:'', names:{}};
export const $ = id => document.getElementById(id);

export async function api(operation, parameters={}) {
  const response = await window.AstrBotPluginView.apiPost('manage', {...state.scope, operation, ...parameters});
  const result = response.data ?? response;
  if (result.error) throw new Error(`${result.error.message} [${result.error.code}]`);
  return result.data ?? result;
}

export function element(tag, text, className='') {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

export function button(label, action) {
  const node = element('button', label);
  node.onclick = () => perform(node, action);
  return node;
}

export async function perform(node, action) {
  node.disabled = true;
  $('notice').textContent = '处理中…';
  try {
    await action();
    $('notice').textContent = '已完成';
  } catch(error) {
    $('notice').textContent = error.message;
  } finally {
    node.disabled = false;
  }
}

export function detail(value) {
  $('detail-body').replaceChildren(element('pre', JSON.stringify(value,null,2)));
  $('detail').showModal();
}

export const labels = {
  long_term:'长期画像',recent:'近期事项',event:'事件信息',uncertain:'待判断',
  active:'启用',review:'待审核',superseded:'已替代',deleted:'已撤回',
  queued:'排队',running:'进行中',paused:'已暂停',completed:'已完成',failed:'失败',cancelled:'已取消',
  new:'新消息分析',history:'历史分析',profile:'画像巩固',deferred:'延期重评',
  confirmed:'已确认',pending:'待确认',retracted:'已撤销',reserved:'已预留',settled:'已结算',needs_reconciliation:'用量待核对'
};
