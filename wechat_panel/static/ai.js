import {artworkFor,artworkHTML,syncContactArtwork} from './agent-art.js?v=3';
import {initMCP,mcpFields,bindMCPFields,readMCP,mcpCard,discoverMCP} from './mcp.js';
import {initCompanion,setCompanionData,agentFields,bindAgentFields,readAgentFields,serviceFields,bindServiceFields,readService,renderExtraTab,sessionFor,scopeFor,serviceDrivers} from './companion.js?v=skill-check-1';
import {skillFields,bindSkillFields,readSkill} from './skills.js';
import {hermesFields,readHermes,bindHermes} from './hermes.js';
import {vibeFields,readVibe} from './vibe.js?v=sparkflow-parity-2';
import {sections,sectionFor,renderNavigation,bindTabKeys,organizeAgentEditor,agentCapabilityNames,mergeCompanion} from './ai-workspace.js?v=api-settings-1';
import {isSettingsTab,modelPlatforms,renderAPISettings,providerGuide,bindProviderGuide} from './api-settings.js?v=1';
const $=id=>document.getElementById(id);
let ctx,data=null,runs=[],tab='agents',editing=null,steps=[],documents=[],detailRun=null,testRun=null,refreshing=null,refreshTimer;
const editedDrafts=new Map();
const lastTabs={};
let ownerId='';
let lastAITab='agents',lastSettingsTab='providers';
let cleanupEditor=()=>{};
const labels={mcp:'MCP 连接',agents:'Agent',providers:'模型连接',knowledge:'知识库',skills:'Skill 技能',tools:'工具',services:'能力连接',memory:'记忆与档案',schedules:'主动关怀',runs:'运行记录'};
const descriptions={mcp:'连接高德与外部 MCP，发现真实工具后按 Agent 授权；没有配置的服务不会被调用。',agents:'同一个 Agent 可以分配给多位联系人，每段对话保持独立上下文。',providers:'填写自己的模型接口、API Key 和模型 ID，保存后测试连接。',knowledge:'导入文本或 Markdown，按 Agent 分配资料，回复时按内容检索。',tools:'预设查询可自动执行，其他调用先在面板确认。',runs:'查看生成过程、知识引用、工具确认和微信发送结果。'};
Object.assign(labels,{skills:'Skills',mcp:'MCP',tools:'工具与权限',providers:'模型连接',preferences:'回复设置',schedules:'自动任务',services:'服务配置'});
Object.assign(descriptions,{preferences:'设置近期上下文与连续消息的处理方式。'});
Object.assign(descriptions,{skills:'导入标准技能包，按 Agent 绑定，按任务调用已有工具完成步骤。',services:'连接记忆、搜索、语音、图像与观测服务，填入自己的地址和密钥后测试。',memory:'按 Agent 与用户查看事实、摘要、情绪及关怀授权。',schedules:'安排 Agent 定时执行任务并分享结果，也可以管理联系人的关怀日程。'});
Object.assign(descriptions,{agents:'先编辑角色与能力，再管理它自己的记忆和自动任务；在联系人中选择由谁回复。',providers:'共享资源 · 模型接口配置一次，可供多个 Agent 选用。',skills:'共享资源 · 已安装的 Skills 直接显示在这里；在各 Agent 的「工具与技能」中选择使用。',mcp:'共享资源 · 已添加的 MCP 直接显示在这里；发现并导入工具后，由各 Agent 授权使用。',services:'共享资源 · 搜索、图像、音频等服务的账号与接口配置；各 Agent 选择使用。HTTP 服务与 MCP 分开管理。',knowledge:'共享资源 · 统一维护资料，在各 Agent 的「知识与记忆」中选择需要的知识库。',memory:'仅管理此 Agent 的记忆与档案；不同联系人、测试会话分别保存。',schedules:'仅管理此 Agent 的定时任务与联系人关怀，执行时沿用它的模型、Skills 和工具权限。'});
const statuses={pending:'等待处理',running:'正在处理',waiting_approval:'等待工具确认',draft:'回复待确认',queued:'等待微信发送',sent:'微信已核对',completed:'已完成',failed:'处理失败',cancelled:'已取消',uncertain:'结果待核对'};
const runStatus=r=>r.status==='pending'&&r.queue_position>0?`排队中 · 前面 ${r.queue_position} 项`:statuses[r.status]||r.status;
const stepNames={knowledge:'检索知识库',model:'模型处理',tool:'固定工具调用',reply:'输出回复'};
const esc=x=>ctx.escape(x);
const find=(kind,id)=>data?.[kind]?.find(x=>x.id===id)||(kind==='tools'&&({'hermes:task':{name:'Hermes 电脑任务'},'hermes:tool':{name:'Hermes 工具操作'}})[id]);
const options=(kind,selected='',empty='请选择')=>(empty===null?'':`<option value="">${esc(empty)}</option>`)+(data?.[kind]||[]).map(x=>`<option value="${esc(x.id)}" ${x.id===selected?'selected':''}>${esc(x.name)}</option>`).join('');
const input=(name,label,value='',extra='')=>`<label class="ai-field"><span>${esc(label)}</span><input name="${name}" aria-label="${esc(label)}" value="${esc(value)}" ${extra}></label>`;
const area=(name,label,value='',rows=4)=>`<label class="ai-field"><span>${esc(label)}</span><textarea name="${name}" aria-label="${esc(label)}" rows="${rows}">${esc(value)}</textarea></label>`;
const select=(name,label,html)=>`<label class="ai-field"><span>${esc(label)}</span><select name="${name}" aria-label="${esc(label)}">${html}</select></label>`;
const choose=(values,selected)=>values.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join('');
const keyField=r=>input('api_key','API Key','',`type="password" autocomplete="new-password" placeholder="${r.key_configured?'已保存，留空保持原密钥':'本机加密保存；本地模型可留空'}"`)+`<label class="ai-check"><input type="checkbox" name="clear_secret">清除已保存密钥</label>`;
const checks=(kind,selected)=>`<div class="ai-check-grid">${(data[kind]||[]).map(x=>`<label class="ai-check"><input type="checkbox" name="${kind}_ids" value="${esc(x.id)}" ${selected.includes(x.id)?'checked':''}>${esc(x.name)}</label>`).join('')||'<p class="field-hint">尚未添加，可先保存 Agent 后再配置。</p>'}</div>`;

export function initAI(context){
  ctx=context;
  initMCP(context,refreshAI);
  initCompanion({...context,refresh:refreshAI,openRun});
  const navigate=next=>{ownerId='';tab=next;lastTabs[sectionFor(tab)]=tab;renderLibrary();};
  $('ai-agent-context').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.agentBack!==undefined)navigate('agents');else if(b.dataset.agentPage){tab=b.dataset.agentPage;renderLibrary();}else if(b.dataset.ownerEdit)openEditor('agents',ownerId).catch(e=>ctx.toast(e.message,true));};
  bindTabKeys($('ai-agent-context'));
  $('ai-tabs').onclick=e=>{const b=e.target.closest('[data-ai-section]');if(b)navigate(lastTabs[b.dataset.aiSection]||sections[b.dataset.aiSection].tabs[0]);};
  $('ai-subtabs').onclick=e=>{const b=e.target.closest('[data-ai-tab]');if(b)navigate(b.dataset.aiTab);};
  bindTabKeys($('ai-tabs'));bindTabKeys($('ai-subtabs'));bindTabKeys($('settings-tabs'));
  $('settings-tabs').onclick=e=>{const b=e.target.closest('[data-ai-tab]');if(b)navigate(b.dataset.aiTab);};
  $('ai-workspace-settings').onclick=()=>{ctx.setView('settings');navigate('providers');};
  $('api-platforms').onclick=async e=>{
    const b=e.target.closest('[data-add-platform]');if(!b)return;
    const platform=modelPlatforms.find(p=>p.id===b.dataset.addPlatform);
    try{await openEditor('providers',null,platform?{name:platform.name,base_url:platform.base_url}:{});}catch(error){ctx.toast(error.message,true);}
  };
  $('ai-create').onclick=async()=>{try{await refreshAI();await openEditor(tab);}catch(e){ctx.toast(e.message,true);}};
  $('ai-settings-close').onclick=()=>$('ai-settings-dialog').close();
  $('ai-settings-form').onsubmit=async e=>{e.preventDefault();try{await ctx.api('/api/ai/settings',{method:'PUT',body:{...data.settings,context_messages:Number($('ai-context').value),debounce_seconds:Number($('ai-debounce').value)}});$('ai-settings-dialog').close();await refreshAI();ctx.toast('AI 回复设置已保存');}catch(error){ctx.toast(error.message,true);}};
  $('ai-manage').onclick=()=>ctx.setView('ai');
  $('contact-agent').onchange=()=>{const c=ctx.current();if(c)ctx.patchContact(c.id,{ai_agent_id:$('contact-agent').value});};
  $('ai-editor-close').onclick=$('ai-editor-cancel').onclick=()=>$('ai-editor').close();
  $('ai-run-close').onclick=()=>{detailRun=null;$('ai-run-dialog').close();};
  $('ai-test-close').onclick=()=>{testRun=null;$('ai-test-dialog').close();};
  $('ai-editor-form').onsubmit=saveEditor;
  $('ai-test-form').onsubmit=async e=>{
    e.preventDefault();$('ai-test-submit').disabled=true;
    try{const agent=$('ai-test-agent').value;const r=await ctx.api('/api/ai/test',{method:'POST',body:{agent_id:agent,text:$('ai-test-text').value,session_id:sessionFor(agent),media_id:$('ai-test-image').dataset.mediaId||null}});testRun=r.id;await renderRun(r,'ai-test-result');}
    catch(error){ctx.toast(error.message,true);}finally{$('ai-test-submit').disabled=false;}
  };
  $('ai-library').onclick=libraryAction;
  const hermesNote=document.createElement('p');hermesNote.id='ai-hermes-test-note';hermesNote.className='field-hint';
  hermesNote.textContent='Hermes 测试会按所选权限实际执行电脑任务；此测试台不会发送微信消息。';hermesNote.hidden=true;
  $('ai-test-form').prepend(hermesNote);
  $('ai-test-agent').addEventListener('change',()=>{hermesNote.hidden=find('agents',$('ai-test-agent').value)?.kind!=='hermes';});
  $('ai-test-image').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>8000000)throw new Error('图片最多 8 MB');const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});const r=await ctx.api('/api/ai/media',{method:'POST',body:{...scopeFor($('ai-test-agent').value),data:encoded}});e.target.dataset.mediaId=r.id;$('ai-test-image-hint').textContent='图片 ID：'+r.id;}catch(e){ctx.toast(e.message,true);}};
  $('ai-test-clear-image').onclick=()=>{$('ai-test-image').value='';delete $('ai-test-image').dataset.mediaId;$('ai-test-image-hint').textContent='';};
  $('ai-test-new-session').onclick=()=>{localStorage.setItem('relay-companion-session:'+$('ai-test-agent').value,crypto.randomUUID());testRun=null;$('ai-test-adult').checked=false;$('ai-test-result').textContent='已开启独立的新会话，旧测试记忆仍按旧会话隔离。';$('ai-test-clear-image').click();};
  $('ai-test-agent').onchange=async()=>{testRun=null;$('ai-test-result').textContent='';$('ai-test-clear-image').click();if($('ai-test-agent').value){const p=await ctx.api('/api/ai/companion?'+new URLSearchParams(scopeFor($('ai-test-agent').value)));$('ai-test-adult').checked=p.profile.age_status==='adult';}};
  $('ai-test-adult').onchange=async e=>{try{const scope=scopeFor($('ai-test-agent').value);const r=await ctx.api('/api/ai/companion?'+new URLSearchParams(scope));await ctx.api('/api/ai/companion/profile',{method:'PUT',body:{...scope,profile:{...r.profile,age_status:e.target.checked?'adult':'unknown'}}});}catch(e){ctx.toast(e.message,true);}};
  for(const id of ['ai-run-detail','ai-test-result','ai-conversation'])$(id).onclick=runAction;
  $('ai-run-detail').oninput=e=>{const id=$('ai-run-detail').dataset.runId;if(e.target.id==='ai-draft-text'&&id)editedDrafts.set(id,e.target.value);};
  refreshAI().catch(e=>ctx.toast(e.message,true));
  setInterval(()=>{if(document.visibilityState==='visible'&&(['ai','settings'].includes(ctx.state.view)||detailRun||testRun||ctx.current()?.mode.startsWith('ai_')))refreshAI().catch(()=>{});},2000);
}

export function activateAIView(view){
  if(view==='settings'&&!isSettingsTab(tab)){
    lastAITab=ownerId?'agents':tab;ownerId='';tab=lastSettingsTab;
  }else if(view==='ai'&&isSettingsTab(tab)){
    lastSettingsTab=tab;tab=lastAITab;ownerId='';
  }
  renderLibrary();
}

export function scheduleAIRefresh(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refreshAI().catch(()=>{}),250);}

export async function refreshAI(fresh=false){
  if(!ctx)return;
  if(refreshing){await refreshing;if(!fresh)return;}
  const pending=(async()=>{
    [data,runs]=await Promise.all([ctx.api('/api/ai/bootstrap'),ctx.api('/api/ai/runs')]);
    setCompanionData(data);
    $('ai-summary').innerHTML=[['AGENTS',data.agents.length,'可分配的 Agent'],['CONVERSATIONS',ctx.state.contacts.filter(c=>c.mode.startsWith('ai_')).length,'使用 AI 的会话'],['ATTENTION',data.health.waiting_approval+data.health.drafts,'等待你的确认']].map(([label,note,desc])=>`<div><span>${label}</span><strong>${note}</strong><p>${desc}</p></div>`).join('');
    renderLibrary();renderContactAI();
    if(detailRun&&$('ai-run-dialog').open&&document.activeElement?.id!=='ai-draft-text')await renderRun(await ctx.api('/api/ai/runs/'+detailRun),'ai-run-detail');
    if(testRun&&$('ai-test-dialog').open)await renderRun(await ctx.api('/api/ai/runs/'+testRun),'ai-test-result');
  })();
  refreshing=pending;
  try{await pending;}finally{if(refreshing===pending)refreshing=null;}
}

function renderLibrary(){
  if(!data)return;
  const host=$(isSettingsTab(tab)?'settings-resource-host':'ai-resource-host'),panel=$('ai-resource-panel');
  if(panel.parentElement!==host)host.append(panel);
  renderAPISettings(tab,data,esc);
  const owner=find('agents',ownerId);
  if(ownerId&&!owner){ownerId='';tab='agents';}
  $('view-ai').classList.toggle('ai-agent-detail',!!ownerId);
  renderNavigation(tab,labels);
  const context=$('ai-agent-context');context.hidden=!ownerId;
  const contextHTML=ownerId?`<div class="ai-agent-context-heading"><button type="button" class="text-button" data-agent-back>← 我的 Agent</button><strong>${esc(owner.name)}</strong><span class="pill">此 Agent 独立配置</span><button type="button" class="text-button" data-owner-edit="${esc(owner.id)}">编辑 Agent</button></div><div class="ai-subtabs" role="tablist" aria-label="此 Agent 的配置">${['memory','schedules'].map(key=>`<button type="button" role="tab" data-agent-page="${key}" aria-selected="${tab===key}" tabindex="${tab===key?0:-1}">${labels[key]}</button>`).join('')}</div>`:'';
  if(context.innerHTML!==contextHTML)context.innerHTML=contextHTML;
  $('ai-section-title').textContent=tab==='agents'?'我的 Agent':tab==='providers'?'已配置的模型':labels[tab];
  $('ai-section-description').textContent=descriptions[tab];
  $('ai-create').hidden=['runs','memory','schedules','preferences'].includes(tab);$('ai-create').textContent=tab==='skills'?'添加 Skill':tab==='mcp'?'添加 MCP':tab==='services'?'添加服务':'新建'+labels[tab];
  if(renderExtraTab(tab,ownerId))return;
  $('ai-library').onclick=libraryAction;
  if(tab==='preferences'){
    $('ai-library').className='ai-card-grid';
    $('ai-library').innerHTML=`<article class="ai-resource-card"><div class="ai-card-kicker">对话处理</div><h4>回复设置</h4><p>携带最近 ${data.settings.context_messages} 条消息 · 连续消息等待 ${data.settings.debounce_seconds} 秒</p><p>各联系人独立保留上下文。</p><div class="ai-card-footer"><span>全局默认设置</span><button class="text-button" id="ai-settings-open">调整回复设置</button></div></article>`;
    $('ai-settings-open').onclick=()=>{$('ai-context').value=data.settings.context_messages;$('ai-debounce').value=data.settings.debounce_seconds;$('ai-settings-dialog').showModal();};
    return;
  }
  const rows=tab==='runs'?runs:data[tab];
  $('ai-library').className=tab==='runs'?'ai-run-list':'ai-card-grid';
  if(!rows.length){$('ai-library').innerHTML=`<div class="ai-empty"><span class="ai-empty-mark">✦</span><h4>${tab==='runs'?'每次处理，都会留下记录':tab==='agents'?'让第一位 Agent 开始工作':'还没有'+labels[tab]}</h4><p>${tab==='agents'?'先添加模型连接，再配置系统提示词、知识库与工具。也可以直接接入你已开发好的 HTTP Agent。':tab==='runs'?'在 Agent 卡片中开始测试，或为联系人启用 AI 模式。':'通过右上角按钮添加'+labels[tab]+'。'}</p></div>`;return;}
  const libraryTab=tab;
  $('ai-library').innerHTML=rows.map(r=>{
    const tab=r._kind||libraryTab;
    if(tab==='runs')return `<button class="ai-run-row" data-run="${esc(r.id)}"><span class="pill ${r.status==='waiting_approval'?'amber':''}">${esc(runStatus(r))}</span><div><strong>${esc(find('agents',r.agent_id)?.name||'Agent')}</strong><p>${esc(r.input.slice(0,100))}</p></div><small>${r.dry_run?'测试台':esc(ctx.state.contacts.find(c=>c.id===r.contact_id)?.lookup||'已移除联系人')} · ${new Date(r.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</small></button>`;
    if(tab==='mcp')return mcpCard(r,data).replace('<article ', '<article data-resource-kind="mcp" ');
    const bound=ctx.state.contacts.filter(c=>c.ai_agent_id===r.id).length;
    let tag='',body='',foot='';
    if(tab==='agents'){
      tag=r.companion?.memory.enabled?'COMPANION AGENT':r.kind==='external'?'EXTERNAL AGENT':'WORKFLOW AGENT';
      body=`<p>${esc(r.description||'独立配置、按联系人分配')}</p><div class="ai-flow-preview">${(r.kind==='external'?['外部 Agent','微信回复']:r.steps.filter(s=>s.type!=='knowledge'||(r.knowledge_ids.length&&(r.knowledge_mode||'on_demand')==='always')).map(s=>stepNames[s.type])).map((x,i)=>`<span><i>${String(i+1).padStart(2,'0')}</i>${esc(x)}</span>`).join('')}</div>`;
      foot=`<span>${bound} 位联系人 · ${esc(r.kind==='external'?'HTTP 接入':find('providers',r.provider_id)?.model||'模型待配置')}</span>`;
      const abilities=agentCapabilityNames(r,data);
      if(abilities.length)body=`<p>${esc(r.description||'独立配置、按联系人分配')}</p><div class="ai-flow-preview">${abilities.map(name=>`<span>${esc(name)}</span>`).join('')}</div>`;
      if(r.kind==='hermes'){
        tag='HERMES COMPUTER AGENT';
        body=`<p>${esc(r.description||'执行电脑上的多步任务')}</p><div class="ai-flow-preview"><span>${r.hermes_tool_policy==='full'?'完全访问':r.hermes_tool_policy==='ai_review'?'AI 代审':r.hermes_approval_scope==='task'?'任务确认一次':'按权限确认'}</span><span>Hermes 执行</span><span>返回结果</span></div>`;
        foot=`<span>${r.hermes_managed?'本机托管':'API 接入'} · ${r.hermes_contact_id?'已指定授权联系人':'仅测试台'}</span><button class="text-button" data-hermes-test="${esc(r.id)}">检查引擎</button>`;
      }
      if(r.kind==='vibe'){
        tag='VIBE-TRADING AGENT';
        body=`<p>${esc(r.description||'金融研究与微信回复')}</p><div class="ai-flow-preview">${r.vibe_engine==='sparkflow'?'<span>SparkFlow 同源引擎</span><span>原生技能与工具</span><span>原文回复</span>':`<span>原生 Agent</span><span>${r.vibe_skills.length} 项技能</span><span>${r.vibe_tools.length} 项工具</span>`}</div>`;
        foot+=`<button class="text-button" data-vibe-test="${esc(r.id)}">检查引擎</button>`;
      }
    }else if(tab==='providers'){
      tag='MODEL CONNECTION';body=`<p class="ai-model-name">${esc(r.model)}</p><p class="ai-muted-url">${esc(r.base_url)}</p>`;foot=`<span>${r.key_configured?'密钥已加密保存':'未配置密钥 / 本地模型'}</span>`;

    }else if(tab==='knowledge'){
      tag='KNOWLEDGE';body=`<p>${esc(r.description||'可供多个 Agent 选用的共享资料')}</p><div class="ai-doc-count"><strong>${r.documents.length}</strong><span>份资料</span></div>`;foot=`<span>${r.documents.map(d=>d.title).slice(0,3).map(esc).join(' · ')||'等待添加资料'}</span>`;
    }else if(tab==='skills'){
      tag='SKILL PACKAGE';body=`<p>${esc(r.description)}</p><p>${r.files.length} 份参考文件 · ${r.enabled?'已启用':'已停用'}</p>`;foot=`<span>${data.agents.filter(a=>(a.skill_ids||[]).includes(r.id)).length} 个 Agent 已选用 · 共享 Skill</span>`;
    }else if(tab==='services'){
      tag='服务连接';body=`<p>${esc(Object.values(serviceDrivers).flat().find(x=>x[0]===r.driver)?.[1]||r.driver)}</p><p class="ai-muted-url">${esc(r.url||(r.driver==='codex_image'?'使用本机 Codex / ChatGPT 登录':'使用本机 Agent Reach'))}</p>`;foot=`<span>${r.enabled?'已配置 · 未实时检测':'已停用'} · ${r.driver==='codex_image'?'账号授权 · 无需 API Key':r.key_configured?'密钥已加密':'未配置密钥'}</span>`;
    }else{
      tag=r.kind==='mcp'?'MCP TOOL':r.kind==='builtin'?'BUILT-IN TOOL':'HTTP TOOL';body=`<p>${esc(r.description)}</p><span class="pill ${r.approval==='confirm'?'amber':'green'}">${r.approval==='confirm'?'执行前确认':'允许自动执行'}</span>`;foot=`<span>${r.enabled?'已启用':'已停用'}</span>`;
    }
    const art=tab==='agents'?artworkFor(r):null;
    return `<article data-resource-kind="${tab}" class="ai-resource-card${art?' ai-agent-card has-agent-art':''}"${art?` data-agent-art="${art.key}"`:''}>${artworkHTML(art)}<div class="ai-card-kicker"><span>${tag}</span><button class="icon-button" data-edit="${esc(r.id)}" aria-label="编辑 ${esc(r.name)}">↗</button></div><h4>${esc(r.name)}</h4>${body}<div class="ai-card-footer">${foot}<div>${tab==='agents'?`<button class="text-button" data-agent-memory="${esc(r.id)}">记忆与档案</button><button class="text-button" data-agent-schedules="${esc(r.id)}">自动任务</button><button class="text-button" data-test="${esc(r.id)}">测试 Agent</button>`:tab==='providers'?`<button class="text-button" data-provider-test="${esc(r.id)}">测试连接</button>`:tab==='services'?`<button class="text-button" data-service-test="${esc(r.id)}">测试能力</button>`:''}<button class="text-button" data-edit="${esc(r.id)}">编辑</button><button class="text-button ai-delete" data-delete="${esc(r.id)}">删除</button></div></div></article>`;
  }).join('');
}

async function libraryAction(e){
  const b=e.target.closest('button');if(!b)return;
  const kind=b.closest('[data-resource-kind]')?.dataset.resourceKind||tab;
  try{
    if(b.dataset.agentMemory||b.dataset.agentSchedules){ownerId=b.dataset.agentMemory||b.dataset.agentSchedules;tab=b.dataset.agentMemory?'memory':'schedules';renderLibrary();return;}
    if(b.dataset.mcpDiscover)await discoverMCP(b.dataset.mcpDiscover);
    if(b.dataset.hermesTest){b.disabled=true;await ctx.api(`/api/ai/agents/${b.dataset.hermesTest}/hermes-test`,{method:'POST'});ctx.toast('Hermes 引擎已连接，支持任务、审批和取消');}
    if(b.dataset.vibeTest){b.disabled=true;const result=await ctx.api(`/api/ai/agents/${b.dataset.vibeTest}/vibe-test`,{method:'POST'});ctx.toast(`Vibe-Trading ${result.revision.slice(0,12)}：${result.tools.filter(n=>n!=='load_skill').length} 项可用研究工具、${result.skills.length} 项技能${result.unavailable.length?'；暂不可用：'+result.unavailable.join('、'):''}`);}
    if(b.dataset.treeApi)await openEditor('tools',b.dataset.treeApi);
    if(b.dataset.edit)await openEditor(kind,b.dataset.edit);
    if(b.dataset.test){$('ai-test-agent').innerHTML=options('agents',b.dataset.test);$('ai-test-result').textContent='';testRun=null;$('ai-test-clear-image').click();const p=await ctx.api('/api/ai/companion?'+new URLSearchParams(scopeFor(b.dataset.test)));$('ai-test-adult').checked=p.profile.age_status==='adult';$('ai-test-dialog').showModal();}
    if(b.dataset.test)$('ai-hermes-test-note').hidden=find('agents',b.dataset.test)?.kind!=='hermes';
    if(b.dataset.serviceTest){b.disabled=true;const result=await ctx.api(`/api/ai/services/${b.dataset.serviceTest}/test`,{method:'POST'});ctx.toast(result.detail);if(result.media){$('ai-test-result').innerHTML=mediaHTML(result.media);$('ai-test-dialog').showModal();}}
    if(b.dataset.providerTest){b.disabled=true;const result=await ctx.api(`/api/ai/providers/${b.dataset.providerTest}/test`,{method:'POST'});ctx.toast(result.reply||'接口已连接');}
    if(b.dataset.run)await openRun(b.dataset.run);
    if(b.dataset.delete&&confirm(`删除“${find(kind,b.dataset.delete)?.name}”？正在使用的配置需要先解除绑定。`)){await ctx.api(`/api/ai/config/${kind}/${b.dataset.delete}`,{method:'DELETE'});await refreshAI();}
  }catch(error){ctx.toast(error.message,true);}finally{b.disabled=false;}
}

export function renderContactAI(){
  if(!ctx||!data)return;const c=ctx.current();if(!c)return;
  const el=$('contact-agent');
  if(document.activeElement!==el){
    const available=data.agents.filter(a=>c.chat_type!=='group'||a.kind==='workflow');
    const hasAgent=available.some(a=>a.id===c.ai_agent_id);
    const placeholder=hasAgent?'':`<option value="" disabled hidden>${available.length?'请选择 Agent':c.chat_type==='group'?'请先创建工作流 Agent':'请先创建 Agent'}</option>`;
    const html=placeholder+available.map(a=>`<option value="${esc(a.id)}" ${a.id===c.ai_agent_id?'selected':''}>${esc(a.name)}</option>`).join('');
    if(el.innerHTML!==html)el.innerHTML=html;
    el.value=hasAgent?c.ai_agent_id:'';
    el.disabled=!available.length;
  }
  $('ai-mode-hint').textContent={manual:'你直接回复。AI 不会处理这个会话的新消息。',listen_only:'只同步消息，不发送回复。',ai_assist:'AI 生成草稿，经你编辑确认后发送。',ai_auto:'新入站消息由所选 Agent 自动回复；人工发送会取消本轮 AI 回复。'}[c.mode];
  const agent=find('agents',c.ai_agent_id);
  syncContactArtwork(el.closest('.agent-placeholder'),agent);
  $('contact-agent-hint').textContent=agent?`${agent.name} · ${agent.kind==='vibe'?'Vibe-Trading 金融研究':agent.kind==='hermes'?'Hermes 电脑助手':agent.kind==='external'?'你开发的外部 Agent':'可配置流程'}${data.settings.enabled?'':' · AI 总开关尚未开启'}`:'每位联系人可绑定不同 Agent。先选择 Agent，再切换到 AI 模式。';
  const unauthorized=agent?.kind==='hermes'&&agent.hermes_contact_id!==c.id;
  if(agent?.kind==='hermes'){
    $('contact-agent-hint').textContent=unauthorized?'尚未授权电脑任务：请编辑此 Agent，将“允许下达电脑任务的微信联系人”设为当前联系人。':`${agent.name} · 电脑任务已授权 · ${agent.hermes_task_approval==='auto'?'任务自动开始':'每个任务先确认'}${data.settings.enabled?'':' · AI 总开关尚未开启'}`;
    const channel=agent.hermes_approval_channel==='wechat'?'在微信回复“确认”':'在面板批准';
    const full=agent.hermes_tool_policy==='full',once=agent.hermes_approval_scope==='task';
    const permission=full?'完全访问，无需审批':agent.hermes_tool_policy==='ai_review'?'AI 代审，敏感操作由你确认':agent.hermes_tool_policy==='sensitive'?'读取和搜索自动执行，敏感任务先确认':agent.hermes_tool_policy==='all'?'电脑任务先确认':'工具遵守 Hermes 原生审批';
    $('ai-mode-hint').textContent=unauthorized?'电脑任务当前被阻止，请先完成联系人授权。':`${agent.hermes_chat_routing!==false?'普通聊天直接回复。':''}${full||agent.hermes_task_approval==='auto'?permission:'电脑任务开始前先确认'}${full?'':`；${channel}${once?'，整项任务只确认一次':''}`}，完成后按当前会话模式回复。`;
  }
  $('contact-agent-hint').classList.toggle('form-error',!!unauthorized);
  const active=runs.filter(r=>r.contact_id===c.id&&['pending','running','waiting_approval','draft','queued'].includes(r.status));
  const current=active.find(r=>r.status!=='pending')||active.filter(r=>r.status==='pending').sort((a,b)=>(a.queue_position||0)-(b.queue_position||0)||a.created_at.localeCompare(b.created_at))[0];
  const latest=runs.find(r=>r.contact_id===c.id);
  const run=current||(latest&&['failed','uncertain'].includes(latest.status)?latest:null);
  const waiting=active.filter(r=>r.status==='pending'&&r.id!==run?.id).sort((a,b)=>(a.queue_position||0)-(b.queue_position||0));
  const box=$('ai-conversation');box.hidden=!run&&!unauthorized;
  if(unauthorized){box.innerHTML='<div class="ai-reply-notice"><div><strong>Hermes 电脑任务尚未授权</strong><p>仅选择 Agent 不会授予电脑权限。请在 AI 中心编辑该 Agent，指定当前联系人；保存后发送一条新指令。</p></div></div>';return;}
  if(run)box.innerHTML=`<div class="ai-reply-notice"><div><strong>${esc(find('agents',run.agent_id)?.name||'Agent')} · ${esc(runStatus(run))}</strong><p>${esc(run.error||run.reply?.slice(0,130)||run.input?.slice(0,130)||'回复和任务进度会在这里显示')}</p></div><button class="button secondary" data-run="${esc(run.id)}">${run.status==='draft'?'编辑并发送':run.status==='waiting_approval'?'确认工具':'查看详情'}</button></div>`;
  if(waiting.length)box.innerHTML+=`<div class="ai-task-queue"><strong>后续 ${waiting.length} 项排队</strong>${waiting.map((item,i)=>`<div><span>${i+1}. ${esc(item.input.slice(0,70))}</span><button class="text-button" data-run="${esc(item.id)}">查看</button><button class="text-button" data-cancel-run="${esc(item.id)}">取消</button></div>`).join('')}</div>`;
}

async function openEditor(kind,id=null,preset={}){
  cleanupEditor();cleanupEditor=()=>{};
  await refreshAI(true);
  const r=id?await ctx.api(`/api/ai/config/${kind}/${id}`):preset;
  editing={kind,id,resource:r};steps=structuredClone(r.steps||[{type:'knowledge',instruction:'',tool_id:'',arguments:{}},{type:'model',instruction:'',tool_id:'',arguments:{}},{type:'reply',instruction:'',tool_id:'',arguments:{}}]);documents=structuredClone(r.documents||[]);
  $('ai-editor-title').textContent=(id?'编辑':'新建')+labels[kind];$('ai-editor-error').hidden=true;
  let html=(kind==='providers'?providerGuide(r,!id,esc):'')+input('name',kind==='tools'?'工具名称（英文标识）':'名称',r.name||'','required maxlength="80"');
  if(kind==='providers')html+=`<div class="ai-form-grid">${input('base_url','接口地址（Base URL）',r.base_url||'','required type="url"')}${input('model','模型 ID',r.model||'','required list="ai-model-options"')}<datalist id="ai-model-options"></datalist></div>${keyField(r)}<p class="field-hint">支持 Chat Completions 兼容接口，包括你自己的本地模型服务。API Key 不会回显到浏览器。</p><div class="ai-form-grid">${input('max_tokens','最大回复 Token',r.max_tokens||1000,'type="number" min="64" max="8000"')}${input('timeout','请求超时（秒）',r.timeout||45,'type="number" min="5" max="120"')}${input('temperature','温度（可留空，采用模型默认值）',r.temperature??'','type="number" min="0" max="2" step="0.1"')}</div>${id?'<button type="button" class="button secondary" id="ai-load-models">读取可用模型</button>':''}`;
  if(kind==='providers')html+=select('thinking','DeepSeek 推理模式',choose([['disabled','关闭推理（适合即时聊天）'],['enabled','开启推理（需要更高 Token 上限）'],['default','使用服务默认值']],r.thinking||'disabled'))+'<p class="field-hint">仅 DeepSeek 官方接口使用此参数。记忆抽取和图片描述使用简短的非推理请求。</p>';
  if(kind==='mcp')html+=mcpFields(r,{input,select,choose,keyField});
  if(kind==='services')html+=serviceFields(r);
  if(kind==='skills')html+=skillFields(r);
  if(kind==='knowledge')html+=input('description','知识库说明',r.description||'')+'<div class="ai-import"><label class="button secondary">导入 TXT / Markdown<input type="file" id="ai-import-files" accept=".txt,.md,.markdown" multiple hidden></label><button class="button secondary" type="button" id="ai-add-document">添加文本资料</button><p class="field-hint">资料只分配给你选定的 Agent；检索结果可在运行详情中查看。</p></div><div id="ai-documents"></div>';
  if(kind==='tools'&&r.kind!=='mcp')html+=input('description','工具功能说明',r.description||'','required')+`<div class="ai-form-grid">${select('kind','工具类型',choose([['http','HTTP 工具'],['builtin','内置工具']],r.kind||'http'))}${select('approval','执行策略',choose([['confirm','执行前在面板确认'],['auto','预设工具，允许自动执行']],r.approval||'confirm'))}</div><div id="ai-http-tool">${select('capability','工具用途',choose([['general','通用工具'],['mindmap','TreeMind / 思维导图（明确请求时调用）']],r.capability||'general'))}${input('url','工具接口地址',r.url||'','type="url"')}${select('method','HTTP 方法',choose([['POST','POST / JSON 参数'],['GET','GET / 查询参数']],r.method||'POST'))}${keyField(r)}<div class="ai-form-grid">${input('auth_header','密钥 Header 名称',r.auth_header||'Authorization')}${input('auth_scheme','密钥前缀（可留空）',r.auth_scheme??'Bearer')}</div></div><div id="ai-builtin-tool">${select('builtin','内置工具',choose([...new Map(data.tools.filter(t=>t.kind==='builtin').map(t=>[t.builtin,[t.builtin,t.description]])).values()],r.builtin||'clock'))}</div>${area('parameters','输入参数 JSON Schema',JSON.stringify(r.parameters||{type:'object',properties:{query:{type:'string'}},required:['query'],additionalProperties:false},null,2),7)}<label class="ai-check"><input type="checkbox" name="enabled" ${r.enabled!==false?'checked':''}>启用工具</label>`;
  if(kind==='tools'&&r.capability==='mindmap'&&r.kind==='http')html+='<p class="field-hint">此工具尚需 TreeMind 官方 API 地址与参数说明。请按官方文档填写输入参数 JSON Schema 和认证；不要直接使用官网首页作为 API 地址。保存有效配置并启用后，可在 Agent 中绑定。成功响应需要包含可打开的导图链接。</p>';
  if(kind==='tools'&&r.kind==='mcp')html+=input('description','工具功能说明',r.description,'required')+select('approval','执行策略',choose([['confirm','执行前在面板确认'],['auto','预设工具，允许自动执行']],r.approval))+`<p class="field-hint">远程工具：${esc(r.remote_name)} · ${esc(find('mcp',r.connection_id)?.name||'连接未找到')}。参数来自服务端，请在 MCP 连接重新导入以更新。</p><pre>${esc(JSON.stringify(r.parameters,null,2))}</pre><label class="ai-check"><input type="checkbox" name="enabled" ${r.enabled?'checked':''}>启用工具</label>`;
  if(kind==='agents')html+=input('description','Agent 职责',r.description||'')+`<input type="hidden" name="kind" value="${esc(r.kind||'workflow')}">`+`<div id="ai-workflow-fields"><div class="ai-form-grid">${select('provider_id','模型连接',options('providers',r.provider_id))}</div>${area('prompt','系统提示词 · 此 Agent 的职责与规则',r.prompt||'',4)}${input('temperature','回复温度（留空沿用模型连接）',r.temperature??'','type="number" min="0" max="2" step="0.05"')}<p class="field-hint">陪伴聊天可设为 0.9–1.0。只影响此 Agent 的回复，不改变共享模型或记忆分析。系统提示词支持 {{user_name}}、{{current_time}}、{{weather}}；每日首次回复更新心情和天气缓存，时间实时注入，未知资料不编造。</p><label class="field-label">绑定知识库</label>${checks('knowledge',r.knowledge_ids||[])}${select('knowledge_mode','知识检索方式',choose([['on_demand','按需检索（推荐）'],['always','每轮检索'],['off','不检索']],r.knowledge_mode||'on_demand'))}<label class="field-label">绑定 Skills</label>${checks('skills',r.skill_ids||[])}<label class="field-label">允许调用的工具</label>${checks('tools',r.tool_ids||[])}${select('web_policy','联网检索策略',choose([['auto','自动查证（推荐）'],['model','由模型决定'],['off','关闭联网检索']],r.web_policy||'auto'))}<p class="field-hint">自动查证：近期事实与不确定的问题先查询，闲聊不搜索；仅调用已授权工具，仍遵守工具确认设置。</p>${select('source_policy','参考链接展示',choose([['auto','智能展示：长内容摘要或主动索要时'],['requested','仅在用户索要时'],['always','每次查询后附来源']],r.source_policy||'auto'))}<p class="field-hint">查询来源始终保留在运行记录。普通短问答默认不附链接，地图、音乐或生成文件的链接正常提供。</p>${input('max_tool_rounds','最多工具调用轮数',r.max_tool_rounds||4,'type="number" min="1" max="10"')}<div class="ai-workflow-heading"><h3>处理流程</h3><button class="button secondary" id="ai-add-step" type="button">添加步骤</button></div><p class="field-hint">按顺序执行。模型可自主调用「工具与技能」中已授权的工具；固定工具参数支持 {{message}} 和 {{previous}}。</p><div id="ai-steps"></div></div><div id="ai-external-fields">${input('url','外部 Agent 地址',r.url||'','type="url"')}${keyField(r)}${input('timeout','请求超时（秒）',r.timeout||60,'type="number" min="5" max="120"')}<p class="field-hint">接收 message、conversation、contact、run_id、dry_run，返回 {"reply":"回复正文"}。不同联系人上下文独立传入。外部服务内部的工具权限与测试行为由该服务管理。</p></div>`;
  if(kind==='agents')html+='<div id="ai-companion-fields">'+agentFields(r.companion)+'</div>';
  if(kind==='agents')html+=hermesFields(r,data.providers,ctx.state.contacts,data.skills,esc);
  if(kind==='agents')html+=vibeFields(r,data.providers,await ctx.api('/api/ai/vibe/catalog'),esc);
  $('ai-editor-fields').innerHTML=html;
  if(kind==='providers')bindProviderGuide(esc);
  if(kind==='mcp')bindMCPFields();
  if(kind==='services')bindServiceFields();
  if(kind==='skills')bindSkillFields(ctx);
  if(kind==='knowledge'){
    renderDocuments();$('ai-add-document').onclick=()=>{readDocuments();documents.push({title:'新资料',content:''});renderDocuments();};
    $('ai-import-files').onchange=async e=>{try{readDocuments();for(const file of e.target.files){if(file.size>1000000)throw new Error('单份文件最多 1 MB');documents.push({title:file.name,content:await file.text()});}renderDocuments();}catch(error){ctx.toast(error.message,true);}};
  }
  if(kind==='agents'){renderSteps();$('ai-add-step').onclick=()=>{readSteps();steps.splice(Math.max(0,steps.length-1),0,{type:'model',instruction:'',tool_id:'',arguments:{}});renderSteps();};}
  if(kind==='agents'||(kind==='tools'&&r.kind!=='mcp')){
    const toggle=()=>{const form=$('ai-editor-form');const value=form.elements.namedItem('kind').value;if(kind==='agents'){
      const managed=form.elements.namedItem('hermes_managed').value==='true';
      $('ai-workflow-fields').hidden=value!=='workflow';$('ai-external-fields').hidden=value!=='external'&&!(value==='hermes'&&!managed);
      $('ai-hermes-fields').hidden=value!=='hermes';$('ai-hermes-local').hidden=!managed;
      $('ai-hermes-skills').hidden=!managed;$('ai-companion-fields').hidden=['hermes','vibe'].includes(value);
      $('ai-vibe-fields').hidden=value!=='vibe';
      $('ai-external-fields').querySelector('.field-hint').textContent=value==='hermes'?'填写 Hermes API 根地址，例如 http://127.0.0.1:8642；密钥是 Hermes API_SERVER_KEY。引擎须支持 Runs API、审批和停止接口。':'接收 message、conversation、contact、run_id、dry_run，返回 {"reply":"回复正文"}。外部服务内部的工具权限与测试行为由该服务管理。';
      form.elements.namedItem('timeout').closest('label').hidden=value==='hermes';
    }else{$('ai-http-tool').hidden=value!=='http';$('ai-builtin-tool').hidden=value!=='builtin';}};
    $('ai-editor-form').elements.namedItem('kind').onchange=toggle;toggle();
    if(kind==='agents'){$('ai-editor-form').elements.namedItem('hermes_managed').onchange=toggle;bindHermes($('ai-editor-form'));}
  }
  if(kind==='tools'&&r.kind!=='mcp'){
    const form=$('ai-editor-form'),presets={clock:{},calculator:{expression:{type:'string',maxLength:300}},knowledge_search:{query:{type:'string',maxLength:500}}};
    const resetSchema=()=>{const preset=data.tools.find(t=>t.builtin===form.elements.namedItem('builtin').value);const properties=preset?.parameters.properties||presets[form.elements.namedItem('builtin').value]||{};form.elements.namedItem('parameters').value=JSON.stringify({type:'object',properties,required:Object.keys(properties),additionalProperties:false},null,2);};
    form.elements.namedItem('builtin').onchange=resetSchema;
    form.elements.namedItem('kind').addEventListener('change',()=>{if(form.elements.namedItem('kind').value==='builtin')resetSchema();});
  }
  if($('ai-load-models'))$('ai-load-models').onclick=async()=>{try{const result=await ctx.api(`/api/ai/providers/${id}/models`,{method:'POST'});$('ai-model-options').innerHTML=result.models.map(x=>`<option value="${esc(x)}">`).join('');ctx.toast('已读取模型，点击模型 ID 输入框选择');}catch(error){ctx.toast(error.message,true);}};
  if(kind==='agents'){
    const cleanLayout=organizeAgentEditor(data,esc),cleanFields=bindAgentFields();
    cleanupEditor=()=>{cleanLayout();cleanFields();};
  }
  $('ai-editor').showModal();
  $('ai-editor-fields').scrollTop=0;
}

function readDocuments(){document.querySelectorAll('[data-document]').forEach((box,i)=>{documents[i]={title:box.querySelector('[data-title]').value,content:box.querySelector('textarea').value};});}
function renderDocuments(){
  $('ai-documents').innerHTML=documents.map((d,i)=>`<div class="ai-document" data-document="${i}"><div><label>资料标题<input data-title value="${esc(d.title)}" required maxlength="160"></label><button type="button" class="icon-button" data-remove-doc="${i}" aria-label="删除资料 ${i+1}">×</button></div><label>资料正文<textarea rows="6" required>${esc(d.content)}</textarea></label></div>`).join('');
  $('ai-documents').onclick=e=>{const b=e.target.closest('[data-remove-doc]');if(b){readDocuments();documents.splice(Number(b.dataset.removeDoc),1);renderDocuments();}};
}
function readSteps(){
  document.querySelectorAll('[data-step]').forEach((box,i)=>{const type=box.querySelector('[data-step-type]').value;steps[i]={type,instruction:box.querySelector('[data-step-instruction]')?.value||'',tool_id:box.querySelector('[data-step-tool]')?.value||'',arguments:JSON.parse(box.querySelector('[data-step-args]')?.value||'{}')};});
}
function renderSteps(){
  $('ai-steps').innerHTML=steps.map((s,i)=>`<div class="ai-step" data-step="${i}"><div class="ai-step-line"><span class="ai-step-number">${String(i+1).padStart(2,'0')}</span><label><span class="sr-only">步骤 ${i+1} 类型</span><select data-step-type aria-label="步骤 ${i+1} 类型">${choose(Object.entries(stepNames),s.type)}</select></label><button class="icon-button" type="button" data-step-up="${i}" aria-label="上移步骤 ${i+1}">↑</button><button class="icon-button" type="button" data-step-down="${i}" aria-label="下移步骤 ${i+1}">↓</button><button class="icon-button" type="button" data-step-delete="${i}" aria-label="删除步骤 ${i+1}">×</button></div>${s.type==='model'?`<label class="ai-field"><span>这一步的处理要求</span><textarea data-step-instruction aria-label="这一步的处理要求" rows="3" placeholder="例如：先核对订单信息，再生成简洁回复">${esc(s.instruction)}</textarea></label>`:s.type==='tool'?`<label class="ai-field"><span>执行工具（需在工具与技能中授权）</span><select data-step-tool aria-label="执行工具（需在工具与技能中授权）">${options('tools',s.tool_id)}</select></label><label class="ai-field"><span>工具参数 JSON</span><textarea data-step-args aria-label="工具参数 JSON" rows="3">${esc(JSON.stringify(s.arguments||{},null,2))}</textarea></label>`:`<p class="field-hint">${s.type==='knowledge'?'从当前 Agent 绑定的知识库检索相关内容。':'将上一处理步骤的结果作为回复，按会话模式确认或发送。'}</p>`}</div>`).join('');
  $('ai-steps').onchange=e=>{if(e.target.matches('[data-step-type]')){try{readSteps();renderSteps();}catch(error){ctx.toast('工具参数 JSON 格式不正确',true);}}};
  $('ai-steps').onclick=e=>{const b=e.target.closest('button');if(!b)return;try{readSteps();if(b.dataset.stepDelete!==undefined)steps.splice(Number(b.dataset.stepDelete),1);for(const [attr,delta] of [['stepUp',-1],['stepDown',1]])if(b.dataset[attr]!==undefined){const i=Number(b.dataset[attr]),j=i+delta;if(j>=0&&j<steps.length)[steps[i],steps[j]]=[steps[j],steps[i]];}renderSteps();}catch(error){ctx.toast('请先修正工具参数 JSON',true);}};
}

async function saveEditor(e){
  e.preventDefault();const form=new FormData(e.target),get=n=>form.get(n)??'',kind=editing.kind;let body={name:get('name')};
  try{
    if(kind==='providers')body={...body,base_url:get('base_url'),model:get('model'),api_key:get('api_key')||null,clear_secret:form.has('clear_secret'),max_tokens:Number(get('max_tokens')),timeout:Number(get('timeout')),temperature:get('temperature')===''?null:Number(get('temperature')),thinking:get('thinking')||'disabled'};
    if(kind==='mcp')body={...body,...readMCP(form)};
    if(kind==='services')body={...body,...readService(form)};
    if(kind==='skills')body={...body,...readSkill(form)};
    if(kind==='knowledge'){readDocuments();body={...body,description:get('description'),documents};}
    if(kind==='tools'&&editing.resource.kind!=='mcp')body={...body,description:get('description'),kind:get('kind'),capability:get('capability')||'general',builtin:get('kind')==='builtin'?get('builtin'):null,approval:get('approval'),url:get('url'),method:get('method'),api_key:get('api_key')||null,clear_secret:form.has('clear_secret'),auth_header:get('auth_header'),auth_scheme:get('auth_scheme'),parameters:JSON.parse(get('parameters')),enabled:form.has('enabled')};
    if(kind==='tools'&&editing.resource.kind==='mcp'){const r=editing.resource;body={...body,kind:'mcp',description:get('description'),approval:get('approval'),enabled:form.has('enabled'),connection_id:r.connection_id,remote_name:r.remote_name,parameters:r.parameters};}
    if(kind==='agents'){
      if(get('kind')==='workflow')readSteps();
      body={...body,description:get('description'),kind:get('kind'),provider_id:get('provider_id'),knowledge_ids:form.getAll('knowledge_ids'),knowledge_mode:get('knowledge_mode')||'on_demand',web_policy:get('web_policy')||'auto',source_policy:get('source_policy')||'auto',skill_ids:form.getAll('skills_ids'),tool_ids:form.getAll('tools_ids'),prompt:get('prompt'),temperature:get('temperature')===''?null:Number(get('temperature')),max_tool_rounds:Number(get('max_tool_rounds')),steps,url:get('url'),api_key:get('api_key')||null,clear_secret:form.has('clear_secret'),timeout:Number(get('timeout')),enabled:editing.resource.enabled!==false};
      body.companion=mergeCompanion(editing.resource.companion,readAgentFields(form));
      if(get('kind')==='hermes')Object.assign(body,readHermes(form));
      if(get('kind')==='vibe')Object.assign(body,readVibe(form));
    }
    $('ai-save').disabled=true;
    await ctx.api(`/api/ai/config/${kind}${editing.id?'/'+editing.id:''}`,{method:editing.id?'PUT':'POST',body});
    await refreshAI(true);$('ai-editor').close();ctx.toast(labels[kind]+'已保存');
  }catch(error){$('ai-editor-error').textContent=error instanceof SyntaxError?'JSON 格式不正确，请检查参数输入。':error.message;$('ai-editor-error').hidden=false;}
  finally{$('ai-save').disabled=false;}
}

async function openRun(id){detailRun=id;await renderRun(await ctx.api('/api/ai/runs/'+id),'ai-run-detail');$('ai-run-dialog').showModal();}
async function renderRun(run,target){
  if(target==='ai-run-detail'&&run.id!==detailRun)return;
  const current=$(target).querySelector('#ai-draft-text');
  if(target==='ai-run-detail'&&$(target).dataset.runId===run.id&&current&&run.status==='draft'){
    if(current.value!==current.defaultValue)editedDrafts.set(run.id,current.value);
    if(document.activeElement===current)return;
  }
  $(target).dataset.runId=run.id;
  const prefix=target==='ai-run-detail'?'ai-draft-text':'ai-test-reply';
  $(target).innerHTML=`<div class="ai-run-heading"><span class="pill ${run.status==='waiting_approval'?'amber':'green'}">${esc(runStatus(run))}</span><span>${esc(find('agents',run.agent_id)?.name||'Agent')}${run.mode==='agent_task'?' · 定时任务生成结果':run.dry_run?' · 不发送微信':''}</span></div><p class="ai-run-input">${esc(run.input)}</p>${run.error?`<p class="form-error">${esc(run.error)}</p>`:''}${run.reply?`<label class="ai-field"><span>回复正文</span><textarea id="${prefix}" rows="5" ${run.status==='draft'?(find('agents',run.agent_id)?.vibe_engine==='sparkflow'?'maxlength="16000"':'maxlength="1999"'):'readonly'}>${esc(run.reply)}</textarea></label>`:''}${run.status==='draft'?`<button class="button primary" data-send-draft="${esc(run.id)}">确认并发送微信</button>`:''}${run.approvals.filter(a=>a.status==='pending').map(a=>`<div class="ai-approval"><h3>${a.tool_id.startsWith('hermes:')?'电脑任务确认':'工具请求 · '+esc(find('tools',a.tool_id)?.name||a.tool_id)}</h3>${a.tool_id.startsWith('hermes:')?`<p style="white-space:pre-line">${esc(a.summary||'准备完成：'+run.input)}</p><details><summary>查看技术详情</summary><pre>${esc(JSON.stringify(a.arguments,null,2))}</pre></details>`:`<pre>${esc(JSON.stringify(a.arguments,null,2))}</pre>`}<p>${a.tool_id.startsWith('hermes:')&&run.approval_scope==='task'?'确认后连续完成本次任务，后续步骤不再逐步确认。':'批准后执行这一次调用，并继续原有流程。'}</p><button class="button primary" data-approve="${esc(a.id)}">批准执行</button><button class="button secondary" data-reject="${esc(a.id)}">拒绝</button></div>`).join('')}<div class="ai-trace"><h3>处理过程</h3>${run.trace.map((t,i)=>`<details><summary><span>${String(i+1).padStart(2,'0')}</span>${esc(t.step)}<time>${new Date(t.created_at).toLocaleTimeString('zh-CN')}</time></summary><pre>${esc(JSON.stringify(t.detail,null,2))}</pre></details>`).join('')||'<p class="field-hint">正在等待处理…</p>'}</div>${['pending','running','waiting_approval','draft','queued'].includes(run.status)?`<button class="text-button ai-cancel-run" data-cancel-run="${esc(run.id)}">取消此任务</button>`:''}`;
  if(run.status==='draft'&&editedDrafts.has(run.id)&&target==='ai-run-detail')$('ai-draft-text').value=editedDrafts.get(run.id);
  if(run.pdf){
    $(target).insertAdjacentHTML('afterbegin',`<p><a class="button secondary" href="${esc(run.pdf.url)}" target="_blank" rel="noopener">下载 PDF · ${esc(run.pdf.name)}</a></p>`);
    if(run.pdf.preview_urls?.length)$(target).insertAdjacentHTML('beforeend',`<div class="pdf-previews">${run.pdf.preview_urls.map((url,i)=>`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="PDF 第 ${i+1} 页预览" loading="lazy"></a>`).join('')}</div>`);
    if(run.status==='draft')$(prefix).maxLength=16000;
  }
  const media=run.trace.filter(t=>t.step==='生成媒体').map(t=>t.detail);
  if(media.length)$(target).insertAdjacentHTML('beforeend',`<div class="companion-media">${media.map(m=>mediaHTML(m,run)).join('')}</div>`);
}
function mediaHTML(m,run){
  let delivery='生成结果可预览下载，尚未发送微信。';
  if(m.kind==='image'&&run&&!run.dry_run){
    delivery=({sent:'已核对微信图片发送成功。',queued:'正在排队发送到原微信会话。',draft:'确认草稿后发送图片到原微信会话。',uncertain:'发送结果待核对，不会自动重发。',failed:'发送未完成，请查看错误信息。',cancelled:'任务已取消。'})[run.status]||delivery;
  }
  return `<div class="media-result">${m.kind==='audio'?`<audio controls src="${esc(m.url)}"></audio>`:`<img src="${esc(m.url)}" alt="生成的图片">`}<a class="text-button" href="${esc(m.url)}" download>下载${m.kind==='audio'?'音频':'图片'}</a><small>${esc(delivery)}</small></div>`;
}
async function runAction(e){
  const b=e.target.closest('button');if(!b)return;
  try{
    if(b.dataset.run)await openRun(b.dataset.run);
    if(b.dataset.approve||b.dataset.reject)await ctx.api('/api/ai/approvals/'+(b.dataset.approve||b.dataset.reject),{method:'POST',body:{approved:!!b.dataset.approve}});
    if(b.dataset.sendDraft){b.disabled=true;await ctx.api(`/api/ai/runs/${b.dataset.sendDraft}/send`,{method:'POST',body:{text:$('ai-draft-text').value,client_key:'ai-draft-'+b.dataset.sendDraft}});ctx.toast('回复已进入微信发送队列');}
    if(b.dataset.cancelRun)await ctx.api(`/api/ai/runs/${b.dataset.cancelRun}/cancel`,{method:'POST'});
    await refreshAI();
  }catch(error){ctx.toast(error.message,true);}finally{b.disabled=false;}
}
