// Public platform links only. Credentials continue to use the existing server-side store.
export const settingsTabs={providers:'模型与 API',services:'服务配置',mcp:'MCP',preferences:'回复设置'};
export const isSettingsTab=tab=>Object.hasOwn(settingsTabs,tab);
export const modelPlatforms=[
  {id:'deepseek',name:'DeepSeek',mark:'D',description:'对话、推理与工具调用',base_url:'https://api.deepseek.com',key_url:'https://platform.deepseek.com/api_keys',docs:'https://api-docs.deepseek.com/'},
  {id:'openai',name:'OpenAI',mark:'O',description:'对话与多模态模型',base_url:'https://api.openai.com/v1',key_url:'https://platform.openai.com/api-keys',docs:'https://developers.openai.com/api/docs/quickstart'},
  {id:'qwen',name:'通义千问 · 百炼',mark:'Q',description:'阿里云百炼模型服务',base_url:'',key_url:'https://help.aliyun.com/zh/model-studio/get-api-key',docs:'https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope',note:'请从百炼控制台复制当前地域与工作空间对应的 OpenAI 兼容 Base URL；地址与 API Key 的地域需一致。'},
  {id:'doubao',name:'豆包 · 火山方舟',mark:'豆',description:'火山方舟模型与推理服务',base_url:'https://ark.cn-beijing.volces.com/api/v3',key_url:'https://console.volcengine.com/ark/region:cn-beijing/apiKey',docs:'https://console.volcengine.com/ark/region%3Acn-beijing/docs/base-url-and-authentication',note:'填写控制台可用的模型 ID 或推理接入点 ID。'},
  {id:'kimi',name:'Kimi',mark:'K',description:'月之暗面开放平台',base_url:'https://api.moonshot.cn/v1',key_url:'https://platform.kimi.com/console/api-keys',docs:'https://platform.kimi.com/docs/get-api-key'},
  {id:'siliconflow',name:'硅基流动',mark:'S',description:'一个平台连接多种模型',base_url:'https://api.siliconflow.cn/v1',key_url:'https://cloud.siliconflow.cn/account/ak',docs:'https://docs.siliconflow.cn/docs/userguide/quickstart'},
];
const platformForURL=url=>{
  try{const host=new URL(url).hostname;return modelPlatforms.find(p=>p.id==='qwen'?(host==='dashscope.aliyuncs.com'||host.endsWith('.maas.aliyuncs.com')):new URL(p.base_url).hostname===host);}catch{return null;}
};
const links=(platform,esc)=>`<div class="api-platform-links"><a href="${esc(platform.key_url)}" target="_blank" rel="noopener noreferrer">申请 / 管理 API Key ↗</a><a href="${esc(platform.docs)}" target="_blank" rel="noopener noreferrer">官方文档 ↗</a></div>`;

export function renderAPISettings(tab,data,esc){
  document.querySelectorAll('#settings-tabs [data-ai-tab]').forEach(button=>{
    const active=button.dataset.aiTab===tab;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
  });
  const catalog=document.getElementById('api-platforms');
  catalog.hidden=tab!=='providers';
  if(tab!=='providers')return;
  const html=`<div class="api-catalog-heading"><div><span class="eyebrow">CONNECT A MODEL</span><h3>选择模型平台</h3><p>先在官方平台申请 Key，再添加连接。模型 ID 以你的账号实际可用列表为准。</p></div></div><div class="api-platform-grid">${modelPlatforms.map(p=>{
    const count=data.providers.filter(r=>platformForURL(r.base_url)?.id===p.id).length;
    return `<article class="api-platform" data-platform="${p.id}"><div class="api-platform-heading"><span class="api-platform-mark" aria-hidden="true">${esc(p.mark)}</span><div><h4>${esc(p.name)}</h4><p>${esc(p.description)}</p></div>${count?`<span class="api-platform-count">${count} 个连接</span>`:''}</div>${links(p,esc)}<button type="button" class="text-button" data-add-platform="${p.id}" aria-label="添加 ${esc(p.name)} 连接">添加连接 <span aria-hidden="true">＋</span></button></article>`;
  }).join('')}<article class="api-platform api-platform-custom"><div class="api-platform-heading"><span class="api-platform-mark" aria-hidden="true">＋</span><div><h4>自定义 / 本地模型</h4><p>填写兼容接口，连接你的模型服务</p></div></div><p class="field-hint">支持 Chat Completions 兼容接口，可使用自己的网关或本地服务。</p><button type="button" class="text-button" data-add-platform="custom">添加自定义连接 <span aria-hidden="true">＋</span></button></article></div>`;
  if(catalog.innerHTML!==html)catalog.innerHTML=html;
}

export function providerGuide(resource,creating,esc){
  const platform=platformForURL(resource.base_url)||(creating?modelPlatforms.find(p=>p.name===resource.name):null);
  return `${creating?`<label class="ai-field"><span>模型平台</span><select id="provider-platform" aria-label="模型平台"><option value="custom">自定义 / 本地模型</option>${modelPlatforms.map(p=>`<option value="${p.id}"${p.id===platform?.id?' selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`:''}<div id="provider-guide" class="api-provider-guide"></div>`;
}

export function bindProviderGuide(esc){
  const form=document.getElementById('ai-editor-form'),get=name=>form.elements.namedItem(name);
  const picker=document.getElementById('provider-platform');
  let previous=platformForURL(get('base_url').value)||modelPlatforms.find(p=>p.id===picker?.value);
  const update=()=>{
    const platform=platformForURL(get('base_url').value)||modelPlatforms.find(p=>p.id===picker?.value),box=document.getElementById('provider-guide');
    box.innerHTML=platform?`<strong>${esc(platform.name)} · 官方入口</strong>${links(platform,esc)}<p>${esc(platform.note||'在官方平台创建 API Key，复制到下方保存；模型 ID 请按账号可用列表填写。')}</p>`:'<p>填写服务商提供的 Base URL、模型 ID 和 API Key。本地无鉴权服务可留空密钥。</p>';
  };
  if(picker)picker.onchange=()=>{
    const selected=modelPlatforms.find(p=>p.id===picker.value);
    if(!get('name').value||get('name').value===previous?.name)get('name').value=selected?.name||'';
    get('base_url').value=selected?.base_url||'';
    get('model').value='';get('api_key').value='';
    previous=selected;update();
  };
  get('base_url').addEventListener('input',()=>{
    const platform=platformForURL(get('base_url').value);
    if(picker)picker.value=platform?.id||'custom';
    update();
  });
  update();
}
