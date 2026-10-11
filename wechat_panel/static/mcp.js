let ctx,refresh;
const $=id=>document.getElementById(id);
export function initMCP(context,onRefresh){
  ctx=context;refresh=onRefresh;
  const dialog=document.createElement('dialog');dialog.id='mcp-discovery';dialog.className='modal ai-dialog';
  dialog.innerHTML='<div class="modal-heading"><div><p class="eyebrow">MCP TOOLS</p><h2>发现与导入工具</h2></div><button class="icon-button" id="mcp-close" aria-label="关闭 MCP 工具">×</button></div><p id="mcp-result-hint" class="field-hint"></p><div id="mcp-discovered-tools"></div><p class="form-error" id="mcp-error" hidden></p><div class="modal-actions"><button class="button primary" id="mcp-import">导入选中工具</button></div>';
  document.body.append(dialog);$('mcp-close').onclick=()=>dialog.close();
}

export function mcpFields(r,{input,select,choose,keyField}){
  return select('profile','服务预设',choose([['amap','高德地图（官方 MCP）'],['treemind','TreeMind 树图（待提供接口）'],['custom','自定义 MCP']],r.profile||'custom'))+
    '<p class="field-hint" id="mcp-profile-hint"></p>'+input('url','MCP 接入地址',r.url||'','type="url" placeholder="https://服务商/mcp，不含密钥"')+
    '<div class="ai-form-grid">'+select('auth','认证方式',choose([['query','URL 参数（如高德 key）'],['bearer','Authorization: Bearer'],['header','自定义 Header'],['none','无需认证']],r.auth||'bearer'))+
    input('auth_name','参数或 Header 名称',r.auth_name||'Authorization')+'</div>'+keyField(r)+
    input('timeout','MCP 请求超时（秒）',r.timeout||30,'type="number" min="5" max="90"')+
    `<label class="ai-check"><input type="checkbox" name="enabled" ${r.enabled!==false?'checked':''}>启用连接</label>`+
    '<p class="field-hint">使用 Streamable HTTP 协议。保存后点击“发现工具”，按真实参数导入，再在 Agent 中勾选授权。发现工具只读取列表，不执行远程操作。</p>';
}

export function bindMCPFields(){
  const form=$('ai-editor-form'),field=n=>form.elements.namedItem(n);
  const toggle=()=>{
    const profile=field('profile').value,amap=profile==='amap';
    field('url').readOnly=amap;field('auth').disabled=amap;field('auth_name').readOnly=amap;
    if(amap){field('url').value='https://mcp.amap.com/mcp';field('auth').value='query';field('auth_name').value='key';}
    $('mcp-profile-hint').textContent=amap?'申请高德 Web 服务 Key 后填入 API Key。天气、地点与出行按需调用，缺少位置先询问，不通过服务器 IP 猜测用户位置。':profile==='treemind'?'TreeMind 官网提供企业 API/SDK，但尚未核实公开 MCP 地址。请取得官方接入资料后配置；如果只提供 REST API，请使用“工具 → HTTP 工具”按官方文档接入。当前预设不会创建导图。':'填入你信任的 MCP 服务地址和凭证。导入工具默认执行前确认。';
  };
  field('profile').onchange=()=>{if(field('profile').value!=='amap'&&field('url').value==='https://mcp.amap.com/mcp'){field('url').value='';field('auth').value='bearer';field('auth_name').value='Authorization';}toggle();};toggle();
}

export function readMCP(form){
  const get=n=>form.get(n)||'';
  return {profile:get('profile'),url:get('url'),auth:get('profile')==='amap'?'query':get('auth'),auth_name:get('auth_name'),
    api_key:get('api_key')||null,clear_secret:form.has('clear_secret'),enabled:form.has('enabled'),timeout:Number(get('timeout'))};
}

export function mcpCard(r,data){
  const esc=ctx.escape,count=data.tools.filter(t=>t.connection_id===r.id).length;
  const apiTool=r.profile==='treemind'?data.tools.find(t=>t.name==='treemind_mindmap'):null;
  const ready=r.url&&(r.auth==='none'||r.key_configured),status=!r.enabled?'已停用':!r.url?'待提供官方接口':!ready?'待填写 Key':'已配置 · 可测试';
  const detail=r.profile==='amap'?'天气 · 地点 · 附近搜索 · 路线与导航':r.profile==='treemind'?'明确要求生成或修改导图时调用；外部创建默认确认。':'按 Agent 授权，按任务调用';
  const docs=r.profile==='amap'?'https://developer.amap.com/api/mcp-server/gettingstarted':r.profile==='treemind'?'https://shutu.cn/':'';
  return `<article class="ai-resource-card"><div class="ai-card-kicker"><span>MCP CONNECTION</span><span class="pill ${ready?'green':'amber'}">${status}</span></div><h4>${esc(r.name)}</h4><p>${detail}</p><p class="ai-muted-url">${esc(r.url||'尚未配置接入地址；不表示已连接 TreeMind')}</p><p>${count} 个已导入工具 · 导入后在 Agent 中授权</p><div class="ai-card-footer"><span>${docs?`<a class="text-button" href="${docs}" target="_blank" rel="noopener noreferrer">官方接入说明 ↗</a>`:'Streamable HTTP'}</span><div>${apiTool?`<button class="text-button" data-tree-api="${esc(apiTool.id)}">配置 API 工具</button>`:''}<button class="text-button" data-mcp-discover="${esc(r.id)}">发现工具</button><button class="text-button" data-edit="${esc(r.id)}">配置</button><button class="text-button ai-delete" data-delete="${esc(r.id)}">删除</button></div></div></article>`;
}

export async function discoverMCP(id){
  const esc=ctx.escape,dialog=$('mcp-discovery');
  $('mcp-result-hint').textContent='正在连接并读取真实工具列表…';$('mcp-discovered-tools').textContent='';$('mcp-error').hidden=true;$('mcp-import').disabled=true;dialog.showModal();
  try{
    const result=await ctx.api(`/api/ai/mcp/${id}/discover`,{method:'POST'});
    $('mcp-result-hint').textContent=`发现 ${result.tools.length} 个工具。导入不会自动绑定联系人；请在 Agent 编辑页授权。`;
    $('mcp-discovered-tools').innerHTML=result.tools.map(t=>`<div class="ai-step"><label class="ai-check"><input type="checkbox" name="mcp-tool" value="${esc(t.name)}" ${t.approval==='auto'?'checked':''}>${esc(t.name)} · ${t.approval==='auto'?'查询可自动执行':'执行前确认'}</label><p class="field-hint">${esc(t.description)}</p><details><summary>查看参数</summary><pre>${esc(JSON.stringify(t.parameters,null,2))}</pre></details></div>`).join('');
    $('mcp-import').disabled=!result.tools.length;
    $('mcp-import').onclick=async()=>{
      const names=[...dialog.querySelectorAll('[name="mcp-tool"]:checked')].map(x=>x.value);
      if(!names.length){$('mcp-error').textContent='请先选择要导入的工具';$('mcp-error').hidden=false;return;}
      $('mcp-import').disabled=true;
      try{const imported=await ctx.api(`/api/ai/mcp/${id}/import`,{method:'POST',body:{names}});await refresh(true);dialog.close();ctx.toast(`已导入 ${imported.tools.length} 个工具，请在 Agent 编辑页勾选授权`);}
      catch(error){$('mcp-error').textContent=error.message;$('mcp-error').hidden=false;}
      finally{$('mcp-import').disabled=false;}
    };
  }catch(error){$('mcp-result-hint').textContent='连接尚未就绪';$('mcp-error').textContent=error.message;$('mcp-error').hidden=false;}
}
