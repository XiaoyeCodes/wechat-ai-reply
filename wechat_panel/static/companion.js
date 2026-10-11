let ctx,library,activeTab='',selection={agent_id:'',contact_id:''};
const $=id=>document.getElementById(id),esc=v=>ctx.escape(v??'');
const field=(name,label,value='',type='text',extra='')=>`<label class="ai-field"><span>${esc(label)}</span><input name="${name}" aria-label="${esc(label)}" value="${esc(value)}" type="${type}" ${extra}></label>`;
const area=(name,label,value='',rows=3)=>`<label class="ai-field"><span>${esc(label)}</span><textarea name="${name}" aria-label="${esc(label)}" rows="${rows}">${esc(value)}</textarea></label>`;
const check=(name,label,value)=>`<label class="ai-check"><input name="${name}" type="checkbox" ${value?'checked':''}>${esc(label)}</label>`;
const select=(name,label,choices,value)=>`<label class="ai-field"><span>${esc(label)}</span><select name="${name}" aria-label="${esc(label)}">${choices.map(([id,title])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
const options=(kind,category)=>[['','暂不启用'],...(library?.[kind]||[]).filter(x=>!category||x.category===category).map(x=>[x.id,x.name])];
const group=(title,subtitle,content,open=false)=>`<details class="companion-section" ${open?'open':''}><summary><strong>${title}</strong><span>${subtitle}</span></summary><div class="companion-section-body">${content}</div></details>`;
const formGrid=content=>`<div class="ai-form-grid">${content}</div>`;
export function sessionFor(agent){const key='relay-companion-session:'+agent;let id=localStorage.getItem(key);if(!id){id=crypto.randomUUID();localStorage.setItem(key,id);}return id;}
export function scopeFor(agent,contact=''){return contact?{agent_id:agent,contact_id:contact}:{agent_id:agent,session_id:sessionFor(agent)};}
export function initCompanion(context){ctx=context;}
export function setCompanionData(data){library=data;}
export const serviceDrivers={
  memory:[['qdrant','Qdrant · 原生 REST + Embedding'],['mem0','mem0 · 原生 REST'],['memory_http','记忆 HTTP 网关 · pgvector / Chroma / Letta / Zep']],
  search:[['agent_reach','Agent Reach · Exa MCP'],['exa','Exa API'],['tavily','Tavily API']],
  tts:[['elevenlabs','ElevenLabs'],['minimax','MiniMax'],['openai_speech','OpenAI Speech 兼容 · 本地 CosyVoice 等'],['tts_http','语音 HTTP 网关 · 火山 / CosyVoice 等']],
  image:[['openai_image','Images 兼容接口 · 文字生图'],['image_http','图像 HTTP 网关 · Flux / SD / 参考图 / LoRA']],
  observability:[['langfuse','Langfuse'],['langsmith','LangSmith'],['trace_http','自建观测 HTTP 接口']]
};
export const serviceCategories=[['memory','外部记忆'],['search','联网搜索'],['tts','语音合成'],['image','图像生成'],['observability','可观测性']];

export function agentFields(c={}){
 const p=c.persona||{},m=c.memory||{},care=c.care||{},media=c.media||{},s=c.safety||{};
 return `<div class="companion-config-heading"><span class="eyebrow">COMPANION STUDIO</span><h3>让角色拥有记忆与生活感</h3><p>每种能力独立配置；用户档案与记忆按联系人隔离。</p></div>`+
 group('01 · 人设与表达','静态人设、说话方式与边界',check('cp.persona.enabled','启用陪伴角色',p.enabled)+formGrid(field('cp.persona.name','角色名字',p.name||'阿慧')+field('cp.persona.age','角色年龄',p.age||24,'number','min="18" max="100"')+field('cp.persona.occupation','角色职业',p.occupation||'设计师')+field('cp.persona.relationship','成年人关系设定',p.relationship||'恋人'))+area('cp.persona.personality','性格设定',p.personality||'温柔、有一点小傲娇，有自己的想法。')+area('cp.persona.appearance','固定外貌特征',p.appearance||'可爱的成年女性，保持固定人物特征。')+area('cp.persona.speaking_style','说话方式',p.speaking_style||'甜而自然、温柔俏皮，像熟悉的人发微信。简单接话一句就好，复杂事情再展开；不固定三段，不总以问题结尾。语气词、撒娇和表情偶尔用，不刻意卖萌。')+check('cp.persona.natural_rhythm','自然变化回复节奏',p.natural_rhythm!==false)+field('cp.persona.emoji_probability','Emoji 使用倾向（0～1）',p.emoji_probability??.25,'number','min="0" max="1" step="0.05"')+`<p class="field-hint">0 表示不用。按轮次选择表达倾向，近期已用表情或情绪严肃时会减少；允许使用也不强制添加。历史回复不再作为固定段式示范。</p>`+area('cp.persona.boundaries','情感行为准则',p.boundaries||'尊重现实关系，不制造愧疚，不诱导消费。认真问到身份时说明是 AI。'),true)+
 group('02 · 三层长期记忆','近期对话 → 摘要 → 用户事实',check('cp.memory.enabled','启用长期记忆',m.enabled)+check('cp.memory.auto_extract','自动提取明确事实并更新摘要',m.auto_extract!==false)+check('cp.memory.store_sensitive','允许保存用户主动提供的敏感事实',m.store_sensitive)+select('cp.memory.service_id','外部记忆连接',options('services','memory'),m.service_id||'')+`<p class="field-hint">留空使用本机加密记忆和关键词检索。Qdrant 使用语义向量检索；其他框架可接入记忆网关。记忆管理支持修改和遗忘。</p>`+formGrid(field('cp.memory.recent_messages','近期消息条数',m.recent_messages||20,'number','min="2" max="60"')+field('cp.memory.retrieve_limit','每次检索记忆条数',m.retrieve_limit||6,'number','min="1" max="20"')+field('cp.memory.retention_days','普通记忆保留天数',m.retention_days||180,'number','min="1" max="3650"')))+
 group('03 · 主动关怀','早安晚安、约定与事件跟进',check('cp.care.enabled','允许创建主动关怀任务',care.enabled)+formGrid(field('cp.care.timezone','关怀时区',care.timezone||'Asia/Shanghai')+field('cp.care.daily_limit','每日关怀上限',care.daily_limit||3,'number','min="1" max="12"')+field('cp.care.quiet_start','免打扰开始',care.quiet_start||'23:00','time')+field('cp.care.quiet_end','免打扰结束',care.quiet_end||'08:00','time')+field('cp.care.min_gap_minutes','最小间隔（分钟）',care.min_gap_minutes||120,'number','min="5" max="1440"'))+`<p class="field-hint">还需要在用户档案中开启该联系人的主动关怀，并添加日程。暂停或更换 Agent 后不会继续发送旧任务。</p>`)+
 group('04 · 声音与视觉','固定音色、理解照片与角色形象',select('cp.media.tts_service_id','语音服务',options('services','tts'),media.tts_service_id||'')+formGrid(field('cp.media.voice_id','固定音色 ID',media.voice_id||'')+field('cp.media.voice_speed','语速',media.voice_speed??1,'number','min="0.5" max="2" step="0.1"'))+check('cp.media.auto_voice','回复后自动生成语音，供面板试听与下载',media.auto_voice)+`<p class="field-hint">使用你有权使用的音色。当前输出音频文件，不伪装为微信原生语音气泡。</p>`+check('cp.media.understand_images','理解收到的照片',media.understand_images)+select('cp.media.vision_provider_id','图片理解模型连接',options('providers'),media.vision_provider_id||'')+select('cp.media.image_service_id','图像生成服务',options('services','image'),media.image_service_id||'')+formGrid(field('cp.media.reference_media_id','角色参考图 ID',media.reference_media_id||'')+field('cp.media.lora','LoRA 名称或地址',media.lora||'')+field('cp.media.lora_scale','LoRA 权重',media.lora_scale??.8,'number','min="0" max="2" step="0.1"'))+area('cp.media.image_prompt','固定形象补充提示词',media.image_prompt||'')+`<p class="field-hint">参考图可在测试台上传，复制图片 ID。参考图和 LoRA 需选择图像网关协议；文字生图接口无法保证角色一致。</p>`)+
 group('05 · 联网与生活工具','有来源的搜索、天气、音乐与地点',select('cp.search_service_id','联网搜索服务',options('services','search'),c.search_service_id||'')+check('cp.weather_enabled','每轮读取用户档案中的城市天气',c.weather_enabled)+`<p class="field-hint">在上方工具列表授权 web_search、weather、music_search、places_search、movie_search。音乐工具查找链接，不代替播放器授权或购买。</p>`)+
 group('06 · 情绪、安全与隐私','现实支持与可追踪的运行记录',check('cp.safety.emotion_enabled','分析情绪并调整回复方式',s.emotion_enabled!==false)+check('cp.safety.crisis_detection','出现紧急自伤信号时优先提供安全关怀',s.crisis_detection!==false)+check('cp.safety.require_adult','亲密关系设定需要确认成年',s.require_adult!==false)+select('cp.safety.observability_service_id','外部观测服务',options('services','observability'),s.observability_service_id||'')+check('cp.safety.export_content','允许向观测服务传输对话正文',s.export_content)+`<p class="field-hint">默认仅传运行指标。记忆、密钥和运行上下文在本机加密；微信聊天原始记录仍保存在本地数据库。情绪标签不是医学诊断。</p>`);
}

export function readAgentFields(form){
 const c={persona:{},memory:{},care:{},media:{},safety:{}};
 document.querySelectorAll('#ai-editor-fields [name^="cp."]').forEach(el=>{
   const parts=el.name.split('.').slice(1);let obj=c;
   while(parts.length>1){const k=parts.shift();obj=obj[k]??={};}
   obj[parts[0]]=el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value;
 });return c;
}

export function serviceFields(r={}){
 return select('category','能力类别',serviceCategories,r.category||'search')+select('driver','接入协议',serviceDrivers[r.category||'search'],r.driver||'agent_reach')+
 field('url','服务地址 / 网关完整地址',r.url||'','url')+field('model','服务模型 ID',r.model||'')+
 field('api_key','服务 API Key','','password',`autocomplete="new-password" placeholder="${r.key_configured?'已保存，留空不修改':'本机加密保存'}"`)+check('clear_secret','清除服务密钥',false)+formGrid(field('voice_id','默认音色 ID',r.voice_id||'')+field('public_key','Langfuse Public Key',r.public_key||'')+field('collection','Qdrant 集合名',r.collection||'relay_memories')+field('timeout','服务超时（秒）',r.timeout||45,'number','min="5" max="120"'))+select('embedding_provider_id','Embedding 模型连接（Qdrant）',options('providers'),r.embedding_provider_id||'')+check('enabled','启用服务',r.enabled!==false)+`<p class="field-hint" id="service-hint"></p>`;
}
export function bindServiceFields(){
 const f=$('ai-editor-form');const hints={agent_reach:'复用本机 mcporter 的 Exa 通道，服务地址与密钥可留空。',memory_http:'填写适配网关完整地址。接收 operation、namespace、query / content，按 namespace 隔离用户。详见项目接入文档。',tts_http:'填写网关完整地址，返回音频或 audio_base64。火山及 CosyVoice 的专有协议需由网关转换。',image_http:'网关接收 prompt、reference_image、lora、lora_scale，返回 image_base64 或公开图片 URL。',qdrant:'填写 Qdrant 根地址，单独选择可用的 Embedding 模型。连接测试会创建指定集合（如果不存在）。',elevenlabs:'根地址示例：https://api.elevenlabs.io；音色 ID 在 ElevenLabs 控制台获取。',minimax:'根地址示例：https://api.minimax.io；填写实际可用的语音模型与音色。',openai_speech:'填写兼容 API Base URL，将请求 /audio/speech。',openai_image:'填写兼容 API Base URL，将请求 /images/generations；此协议不传 LoRA 或参考图。'};
 const hint=()=>{$('service-hint').textContent=hints[f.elements.driver.value]||'填写服务商提供的地址与凭据。测试连接可能产生一次 API 调用费用。';};
 f.elements.category.onchange=()=>{f.elements.driver.innerHTML=serviceDrivers[f.elements.category.value].map(([k,v])=>`<option value="${k}">${v}</option>`).join('');hint();};f.elements.driver.onchange=hint;hint();
}
export function readService(form){const get=k=>form.get(k)||'';return {category:get('category'),driver:get('driver'),url:get('url'),model:get('model'),api_key:get('api_key')||null,clear_secret:form.has('clear_secret'),voice_id:get('voice_id'),public_key:get('public_key'),collection:get('collection'),timeout:Number(get('timeout')),embedding_provider_id:get('embedding_provider_id'),enabled:form.has('enabled')};}

export function renderExtraTab(tab){
 if(!['memory','schedules'].includes(tab)){activeTab='';return false;}
 if(activeTab===tab)return true;activeTab=tab;
 $('ai-library').className='companion-workspace';
 if(tab==='memory')renderMemory();else renderSchedules();return true;
}

function selectors(){return formGrid(select('agent_id','记忆所属 Agent',options('agents').filter(x=>x[0]),selection.agent_id)+select('contact_id','记忆所属用户',[['','测试台会话'],...ctx.state.contacts.map(c=>[c.id,c.lookup])],selection.contact_id));}
async function renderMemory(){
 selection.agent_id=selection.agent_id||library.agents[0]?.id||'';
 $('ai-library').innerHTML=`<div class="companion-banner"><span>MEMORY GARDEN</span><h3>记住重要的事，也允许忘记</h3><p>这里的每一条记忆只属于所选 Agent 与用户。</p></div><form id="memory-select">${selectors()}<button class="button secondary">读取记忆与档案</button></form><div id="memory-content"></div>`;
 $('memory-select').onsubmit=async e=>{e.preventDefault();selection=Object.fromEntries(new FormData(e.target));await loadMemory();};
 await loadMemory();
}
async function loadMemory(){
 if(!selection.agent_id){$('memory-content').textContent='先创建一位 Agent。';return;}
 try{
 const scope=scopeFor(selection.agent_id,selection.contact_id),data=await ctx.api('/api/ai/companion?'+new URLSearchParams(scope)),p=data.profile;
 $('memory-content').innerHTML=`<form id="companion-profile">${group('用户档案','只填写用户明确提供的信息',formGrid(field('display_name','用户名称',p.display_name)+select('age_status','用户年龄声明',[['unknown','未确认 · 普通陪伴'],['adult','已自我声明成年'],['minor','未成年 · 普通陪伴']],p.age_status)+field('preferred_name','喜欢的称呼',p.preferred_name)+field('city','所在城市（可选）',p.city)+field('birthday','生日（可选）',p.birthday)+field('relationship_start','关系起始日期（可选）',p.relationship_start,'date')+select('closeness','亲密度',[['初识','初识'],['熟悉','熟悉'],['亲密','亲密']],p.closeness))+check('memory_consent','允许此用户保存长期记忆',p.memory_consent)+check('care_consent','允许此联系人接收主动关怀',p.care_consent)+`<button class="button primary">保存用户档案</button>`,true)}</form><div class="companion-summary"><h4>近期摘要</h4><p>${esc(data.summary||'对话后自动整理，尚无摘要。')}</p><small>近期情绪：${esc(data.mood||'尚未分析')}</small></div><div class="companion-memory-actions"><button class="button secondary" id="add-memory">添加记忆</button><button class="text-button" id="preview-prompt">预览分层提示词</button><button class="text-button ai-delete" id="forget-all">遗忘此用户全部记忆</button></div><div class="memory-cards">${data.memories.map(m=>`<article class="memory-card"><span class="pill">${esc({fact:'事实',preference:'喜好',event:'事件',worry:'烦恼',summary:'摘要'}[m.category])}${m.pinned?' · 长期保留':''}</span><h4>${esc(m.key)}</h4><p>${esc(m.content)}</p><small>${m.pinned?'不自动过期':'到期 '+new Date(m.expires_at).toLocaleDateString('zh-CN')}</small><div><button class="text-button" data-memory-edit="${m.id}">编辑</button><button class="text-button" data-memory-forget="${m.id}">遗忘</button></div></article>`).join('')||'<p class="field-hint">还没有记忆。可以先在测试台聊聊喜好、生日或最近的计划。</p>'}</div><pre id="prompt-preview" hidden></pre>`;
 $('companion-profile').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target),profile=Object.fromEntries(f);profile.memory_consent=f.has('memory_consent');profile.care_consent=f.has('care_consent');await ctx.api('/api/ai/companion/profile',{method:'PUT',body:{...scope,profile}});ctx.toast('用户档案已保存');}catch(e){ctx.toast(e.message,true);}};
 const forget=async id=>{if(!confirm(id?'遗忘这条记忆，同时清理摘要及旧上下文，防止它再次出现？':'遗忘此用户全部记忆、摘要和测试对话？'))return;try{await ctx.api('/api/ai/companion/forget',{method:'POST',body:{...scope,memory_id:id}});await loadMemory();}catch(e){ctx.toast(e.message,true);}};
 $('forget-all').onclick=()=>forget(null);$('add-memory').onclick=()=>memoryEditor(scope);
 $('memory-content').onclick=e=>{const b=e.target.closest('button');if(b?.dataset.memoryForget)forget(b.dataset.memoryForget);if(b?.dataset.memoryEdit)memoryEditor(scope,data.memories.find(m=>m.id===b.dataset.memoryEdit));};
 $('preview-prompt').onclick=async()=>{const r=await ctx.api('/api/ai/companion/prompt',{method:'POST',body:scope});$('prompt-preview').textContent=r.prompt;$('prompt-preview').hidden=false;};
 }catch(error){ctx.toast(error.message,true);}
}
function modal(title,html,submit){
 $('companion-modal')?.remove();const d=document.createElement('dialog');d.id='companion-modal';d.className='ai-dialog';d.innerHTML=`<form><div class="modal-heading"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-close aria-label="关闭">×</button></div>${html}<p class="form-error" hidden></p><div class="modal-actions"><button class="button primary">保存</button></div></form>`;document.body.append(d);d.querySelector('[data-close]').onclick=()=>d.close();d.querySelector('form').onsubmit=async e=>{e.preventDefault();try{await submit(new FormData(e.target));d.close();}catch(error){const box=d.querySelector('.form-error');box.textContent=error.message;box.hidden=false;}};d.showModal();return d;
}
function memoryEditor(scope,m={}){modal('编辑记忆',field('key','记忆字段',m.key||'')+select('category','记忆类别',[['fact','事实'],['preference','喜好'],['event','事件'],['worry','烦恼']],m.category||'fact')+area('content','记忆内容',m.content||'')+check('pinned','长期保留（不自动过期）',m.pinned),async f=>{await ctx.api('/api/ai/companion/memories',{method:'POST',body:{...scope,memory_id:m.id,memory:{key:f.get('key'),category:f.get('category'),content:f.get('content'),pinned:f.has('pinned')}}});await loadMemory();});}

async function renderSchedules(){
 try{const rows=await ctx.api('/api/ai/schedules');$('ai-library').innerHTML=`<div class="companion-banner"><span>GENTLE ROUTINES</span><h3>在合适的时候，想起你</h3><p>关怀按时区执行，遵守免打扰时间。服务关闭或暂停期间的旧任务不会集中补发。</p></div><button class="button primary" id="new-care">添加关怀日程</button><div class="care-list">${rows.map(s=>`<article class="care-card"><div><span class="pill ${s.enabled?'green':''}">${s.enabled?'已启用':'已停用'}</span><h4>${esc(s.title)}</h4><p>${esc(s.instruction)}</p><small>${esc(ctx.state.contacts.find(c=>c.id===s.contact_id)?.lookup||'联系人已移除')} · ${esc(s.kind==='cron'?s.cron:s.at)} · ${esc(s.timezone)} · ${s.delivery==='auto'?'按会话模式发送':'先生成草稿'}</small><p>${esc(s.last_status)}${s.next_at?' · 下次 '+new Date(s.next_at).toLocaleString('zh-CN'):''}</p></div><div><button class="text-button" data-care-edit="${s.id}">编辑</button><button class="text-button ai-delete" data-care-delete="${s.id}">删除</button></div></article>`).join('')||'<p class="field-hint">可以添加早安、晚安，或某次面试后的关心。</p>'}</div>`;
 $('new-care').onclick=()=>scheduleEditor();$('ai-library').onclick=async e=>{const b=e.target.closest('button');if(b?.dataset.careEdit)scheduleEditor(rows.find(x=>x.id===b.dataset.careEdit));if(b?.dataset.careDelete&&confirm('删除这个关怀日程？')){await ctx.api('/api/ai/schedules/'+b.dataset.careDelete,{method:'DELETE'});await renderSchedules();}};
 }catch(e){ctx.toast(e.message,true);}
}
function scheduleEditor(s={}){const defaultDate=new Date(Date.now()+3600000);defaultDate.setMinutes(defaultDate.getMinutes()-defaultDate.getTimezoneOffset());const html=field('title','日程名称',s.title||'早安问候')+formGrid(select('agent_id','关怀 Agent',options('agents').filter(x=>x[0]),s.agent_id||library.agents[0]?.id)+select('contact_id','接收联系人',ctx.state.contacts.map(c=>[c.id,c.lookup]),s.contact_id||ctx.state.contacts[0]?.id))+select('kind','执行方式',[['once','仅一次'],['cron','Cron 重复执行']],s.kind||'once')+formGrid(field('at','执行日期时间（一次性）',s.at?s.at.slice(0,16):defaultDate.toISOString().slice(0,16),'datetime-local')+field('cron','Cron 表达式（重复）',s.cron||'0 9 * * *')+field('timezone','执行时区',s.timezone||'Asia/Shanghai'))+`<p class="field-hint">Cron 五段：分 时 日 月 星期。每天早上 9 点：0 9 * * *；每天晚 10 点：0 22 * * *。</p>`+area('instruction','她要关心的事情',s.instruction||'用自然、简短的语气说早安，不强求用户回复。')+select('delivery','发送方式',[['draft','生成草稿，在面板确认'],['auto','按会话模式自动发送']],s.delivery||'draft')+check('enabled','启用日程',s.enabled)+`<p class="field-hint">实际执行还需联系人已绑定该 Agent、开启监听及 AI 模式，并在用户档案同意主动关怀。</p>`;modal('关怀日程',html,async f=>{const body=Object.fromEntries(f);body.enabled=f.has('enabled');await ctx.api('/api/ai/schedules'+(s.id?'/'+s.id:''),{method:s.id?'PUT':'POST',body});await renderSchedules();});}

export async function templateAction(){if(!library.providers.length){ctx.toast('请先添加模型连接',true);return;}const d=modal('创建阿慧 · 甜心陪伴',`<p class="field-hint">24 岁的设计师，温柔、有一点小傲娇。预设三层记忆、简短表达和联网工具；主动关怀需按联系人开启。</p>`+select('provider_id','使用的文本模型',options('providers').filter(x=>x[0]),library.providers[0].id),async f=>{await ctx.api('/api/ai/companion/template',{method:'POST',body:{provider_id:f.get('provider_id')}});ctx.toast('阿慧已创建，可在测试台体验');await ctx.refresh();});d.querySelector('button.button.primary').textContent='创建 Agent';}
