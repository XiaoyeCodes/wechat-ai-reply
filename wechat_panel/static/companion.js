import {openVoiceSettings} from './voice.js?v=voice-settings-3';
import {renderAgentSchedules,updateAgentSchedules} from './agent-schedules.js?v=1';
let ctx,library,activeTab='',selection={agent_id:'',contact_id:''};
const $=id=>document.getElementById(id),esc=v=>ctx.escape(v??'');
const field=(name,label,value='',type='text',extra='')=>`<label class="ai-field"><span>${esc(label)}</span><input name="${name}" aria-label="${esc(label)}" value="${esc(value)}" type="${type}" ${extra}></label>`;
const area=(name,label,value='',rows=3)=>`<label class="ai-field"><span>${esc(label)}</span><textarea name="${name}" aria-label="${esc(label)}" rows="${rows}">${esc(value)}</textarea></label>`;
const check=(name,label,value)=>`<label class="ai-check"><input name="${name}" type="checkbox" ${value?'checked':''}>${esc(label)}</label>`;
const select=(name,label,choices,value)=>`<label class="ai-field"><span>${esc(label)}</span><select name="${name}" aria-label="${esc(label)}">${(value&&!choices.some(([id])=>id===value)?[...choices,[value,'原连接已不可用，请重新选择']]:choices).map(([id,title])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
const options=(kind,category,empty='暂不启用')=>[['',empty],...(library?.[kind]||[]).filter(x=>!category||x.category===category).map(x=>[x.id,x.name+(x.enabled===false?'（已停用）':'')])];
const group=(title,subtitle,content,open=false)=>`<details class="companion-section" ${open?'open':''}><summary><strong>${title}</strong><span>${subtitle}</span></summary><div class="companion-section-body">${content}</div></details>`;
const formGrid=content=>`<div class="ai-form-grid">${content}</div>`;
export function sessionFor(agent){const key='relay-companion-session:'+agent;let id=localStorage.getItem(key);if(!id){id=crypto.randomUUID();localStorage.setItem(key,id);}return id;}
export function scopeFor(agent,contact=''){return contact?{agent_id:agent,contact_id:contact}:{agent_id:agent,session_id:sessionFor(agent)};}
export function initCompanion(context){ctx=context;}
export function setCompanionData(data){library=data;updateAgentSchedules(data);}
export const serviceDrivers={
  memory:[['qdrant','Qdrant · 原生 REST + Embedding'],['mem0','mem0 · 原生 REST'],['memory_http','记忆 HTTP 网关 · pgvector / Chroma / Letta / Zep']],
  search:[['deepseek','DeepSeek · 官方 Search / Fetch'],['agent_reach','Agent Reach · Exa MCP'],['exa','Exa API'],['tavily','Tavily API']],
  tts:[['elevenlabs','ElevenLabs'],['minimax','MiniMax'],['openai_speech','OpenAI Speech 兼容 · 本地 CosyVoice 等'],['tts_http','语音 HTTP 网关 · 火山 / CosyVoice 等']],
  image:[['codex_image','Codex / ChatGPT 账号 · 文生图与图生图'],['openai_image','Images 兼容接口 · 文字生图'],['image_http','图像 HTTP 网关 · Flux / SD / 参考图 / LoRA']],
  observability:[['langfuse','Langfuse'],['langsmith','LangSmith'],['trace_http','自建观测 HTTP 接口']]
};
export const serviceCategories=[['memory','外部记忆'],['search','联网搜索'],['tts','语音合成'],['image','图像生成'],['observability','可观测性']];

function mediaFields(media){
 return `<section class="cp-feature cp-voice-feature"><span class="eyebrow">WECHAT VOICE</span><h4>微信语音回复</h4><p>音色与回复形式按联系人保存。同一个 Agent 可以为不同的人使用不同的声音。</p><button type="button" class="button secondary" id="cp-minimax-open">MiniMax 设置与声音克隆</button><p class="field-hint">在聊天右侧选择「语音回复 → 声音与试听」，可选在线女声、本机女声或 MiniMax 音色，并切换 Turbo / HD。这里打开的是同一份 MiniMax 配置。</p></section>`+
 `<section class="cp-feature"><h4>收到图片时</h4>${check('cp.media.understand_images','理解收到的照片',media.understand_images)}<div id="cp-vision-options">${select('cp.media.vision_provider_id','图片理解模型连接',options('providers',null,'沿用当前聊天模型'),media.vision_provider_id||'')}<p class="field-hint">需要支持图片输入的模型；聊天模型不支持时，单独选择视觉模型。适用于微信收图和测试台上传，不控制图片生成。</p></div></section>`+
 `<section class="cp-feature"><h4>生成与修改图片</h4>${select('cp.media.image_service_id','图像生成服务',options('services','image'),media.image_service_id||'')}<p class="field-hint" id="cp-image-status" role="status"></p><div id="cp-image-options">${area('cp.media.image_prompt','形象补充提示词（可选）',media.image_prompt||'')}<details class="cp-advanced" id="cp-image-reference"><summary>固定角色参考图（高级）</summary>${field('cp.media.reference_media_id','角色参考图 ID',media.reference_media_id||'')}<p class="field-hint">填写本工作台已有图片的 ID。一般图生图直接使用本轮收到或上传的图片，无需填写。</p></details><div id="cp-image-lora">${formGrid(field('cp.media.lora','LoRA 名称或地址',media.lora||'')+field('cp.media.lora_scale','LoRA 权重',media.lora_scale??.8,'number','min="0" max="2" step="0.1"'))}<p class="field-hint">仅图像 HTTP 网关支持，名称与权重由你的网关解释。</p></div><div id="cp-image-incompatible" class="cp-notice" hidden><p>保存的参考图或 LoRA 不适用于当前服务，调用会失败。可以清除不适用的值，或切回支持它们的服务。</p><button type="button" class="button secondary" id="cp-image-clear">清除不适用的图像配置</button></div></div><button type="button" class="button secondary" data-cp-grant="generate_image">授权图片生成</button><p class="field-hint">授权后模型可以按请求生成或修改图片。结果在工作台预览与下载。</p></section>`+
 `<details class="cp-advanced" id="cp-file-voice"><summary>音频文件生成（高级）<span>用于面板试听与下载</span></summary><p class="field-hint">这是独立的音频文件工具。微信原生语音使用上面的联系人设置，MiniMax 无需在这里重复填写。</p>${select('cp.media.tts_service_id','音频文件服务',options('services','tts'),media.tts_service_id||'')}${formGrid(field('cp.media.voice_id','文件合成音色 ID',media.voice_id||'')+field('cp.media.voice_speed','文件合成语速',media.voice_speed??1,'number','min="0.5" max="2" step="0.1"'))}${check('cp.media.auto_voice','回复后额外生成音频文件',media.auto_voice)}<p class="field-hint">原生语音回复开启时不会重复自动生成文件。模型主动调用文件合成还需要授权 speak 工具。</p></details>`;
}

function networkFields(c){
 return `<section class="cp-feature"><h4>什么时候联网</h4><div id="cp-web-policies"></div>${select('cp.search_service_id','联网搜索服务',options('services','search','选择搜索连接器'),c.search_service_id||'')}<p class="field-hint" id="cp-search-status" role="status"></p><button type="button" class="button secondary" data-cp-grant="web_search,read_web">授权搜索与原文读取</button></section>`+
 `<section class="cp-feature"><h4>新闻汇总</h4><p>使用原生 news-aggregator-skill 多来源采集与深读。可直接说「今日国际新闻」「经济新闻 5 条」，按需求选择类别和来源。</p><p class="field-hint" id="cp-news-status" role="status"></p><button type="button" class="button secondary" data-cp-news>启用原生新闻技能</button><details class="cp-advanced" id="cp-news-legacy"><summary>网页新闻核验（兼容选项）</summary>${check('cp.news_verify','未使用原生新闻 Skill 时，启用网页新闻核验',c.news_verify)}<p class="field-hint">依赖搜索与原文读取工具；仅用于未绑定原生新闻 Skill 的旧流程，不影响原生技能的新闻来源与采集方式。</p></details></section>`+
 `<section class="cp-feature"><h4>天气与生活查询</h4>${check('cp.weather_enabled','把用户所在城市的天气加入聊天上下文',c.weather_enabled)}<p class="field-hint">城市来自该 Agent × 用户的档案。含天气变量的提示词每日缓存；缺少城市或获取失败时不编造。这个开关会独立获取天气，按需问天气则使用下方工具。</p><div id="cp-life-tools" class="cp-tool-list"></div><p class="field-hint">音乐、电影和地点查询返回网页资料与链接；高德路线等能力需另行绑定对应 MCP 工具，不提供下单或付款。</p></section>`;
}

export function agentFields(c={}){
 const m=c.memory||{},care=c.care||{},media=c.media||{},s=c.safety||{};
 return `<div class="companion-config-heading"><span class="eyebrow">COMPANION STUDIO</span><h3>让角色拥有记忆与生活感</h3><p>角色与表达统一由上方系统提示词控制。用户档案、记忆、摘要按 Agent × 联系人独立保存。</p></div>`+
 group('01 · 三层长期记忆','近期对话 → 摘要 → 用户事实',check('cp.memory.enabled','启用长期记忆',m.enabled)+check('cp.memory.auto_extract','自动提取明确事实并更新摘要',m.auto_extract!==false)+check('cp.memory.store_sensitive','允许保存用户主动提供的敏感事实',m.store_sensitive)+select('cp.memory.service_id','外部记忆连接',options('services','memory'),m.service_id||'')+`<p class="field-hint">每个 Agent 与每位联系人各有独立记忆。切回时恢复该组合的记忆；切换前最多带入 4 条背景，每条最多 300 字，不沿用旧角色风格。留空使用本机加密记忆和关键词检索。Qdrant 使用语义向量检索；其他框架可接入记忆网关。记忆管理支持修改和遗忘。</p>`+formGrid(field('cp.memory.recent_messages','近期消息条数',m.recent_messages||20,'number','min="2" max="60"')+field('cp.memory.retrieve_limit','每次检索记忆条数',m.retrieve_limit||6,'number','min="1" max="20"')+field('cp.memory.retention_days','普通记忆保留天数',m.retention_days||180,'number','min="1" max="3650"')))+
 group('02 · 主动关怀','早安晚安、约定与事件跟进',check('cp.care.enabled','允许创建主动关怀任务',care.enabled)+formGrid(field('cp.care.timezone','关怀时区',care.timezone||'Asia/Shanghai')+field('cp.care.daily_limit','每日关怀上限',care.daily_limit||3,'number','min="1" max="12"')+field('cp.care.quiet_start','免打扰开始',care.quiet_start||'23:00','time')+field('cp.care.quiet_end','免打扰结束',care.quiet_end||'08:00','time')+field('cp.care.min_gap_minutes','最小间隔（分钟）',care.min_gap_minutes||120,'number','min="5" max="1440"'))+`<p class="field-hint">还需要在用户档案中开启该联系人的主动关怀，并添加日程。暂停或更换 Agent 后不会继续发送旧任务。</p>`)+
 group('03 · 声音与视觉','微信语音、照片理解与图片生成',mediaFields(media))+
 group('04 · 联网与生活工具','调用时机、新闻技能与生活查询',networkFields(c))+
 group('05 · 情绪、安全与隐私','现实支持与可追踪的运行记录',check('cp.safety.emotion_enabled','分析情绪并调整回复方式',s.emotion_enabled!==false)+check('cp.safety.crisis_detection','出现紧急自伤信号时优先提供安全关怀',s.crisis_detection!==false)+check('cp.safety.require_adult','亲密关系设定需要确认成年',s.require_adult!==false)+select('cp.safety.observability_service_id','外部观测服务',options('services','observability'),s.observability_service_id||'')+check('cp.safety.export_content','允许向观测服务传输对话正文',s.export_content)+`<p class="field-hint">默认仅传运行指标。记忆、密钥和运行上下文在本机加密；微信聊天原始记录仍保存在本地数据库。情绪标签不是医学诊断。</p>`);
}

export function bindAgentFields(){
 const form=$('ai-editor-form'),field=name=>form.elements.namedItem(name);
 const checked=(name,id)=>[...form.querySelectorAll(`input[name="${name}"]`)].some(el=>el.value===id&&el.checked);
 const toolState=names=>names.map(name=>{
   const choices=(library.tools||[]).filter(t=>t.builtin===name&&t.kind==='builtin');
   return {name,tool:choices.find(t=>t.enabled&&checked('tools_ids',t.id))||choices.find(t=>t.enabled),bound:choices.some(t=>t.enabled&&checked('tools_ids',t.id))};
 });
 const selectedService=category=>(library.services||[]).find(s=>s.id===field(category==='search'?'cp.search_service_id':'cp.media.image_service_id').value&&s.category===category);
 const newsSkill=()=>library.skills.find(s=>s.name==='news-aggregator-skill'&&s.enabled);
 const grant=(names,skill)=>{
   for(const state of toolState(names))if(state.tool)for(const el of form.querySelectorAll('[name="tools_ids"]'))if(el.value===state.tool.id)el.checked=true;
   if(skill)for(const el of form.querySelectorAll('[name="skills_ids"]'))if(el.value===skill.id)el.checked=true;
   form.dispatchEvent(new Event('change',{bubbles:true}));
 };
 const life=[['weather','按需查天气','城市天气与未来预报，无需搜索连接器'],['music_search','音乐链接','查找网易云、Spotify 等音乐资料'],['places_search','餐厅与地点','通过网页查找地点资料'],['movie_search','电影资料','查询简介、评分与推荐来源']];
 $('cp-life-tools').innerHTML=life.map(([name,title,description])=>`<div class="cp-tool-row"><div><strong>${title}</strong><p>${description}</p><small data-cp-tool-status="${name}"></small></div><button type="button" class="button secondary" data-cp-grant="${name}">授权工具</button></div>`).join('');
 const render=()=>{
   $('cp-vision-options').hidden=!field('cp.media.understand_images').checked;
   const image=selectedService('image'),driver=image?.driver,hasImage=!!field('cp.media.image_service_id').value;
   const reference=['codex_image','image_http'].includes(driver),lora=driver==='image_http';
   $('cp-image-options').hidden=!hasImage;
   $('cp-image-reference').hidden=!reference;$('cp-image-lora').hidden=!lora;
   $('cp-image-incompatible').hidden=!hasImage||!image||(!(!reference&&field('cp.media.reference_media_id').value)&&!(!lora&&field('cp.media.lora').value));
   $('cp-image-status').textContent=!hasImage?'未开启图片生成。':!image?'所选图像服务已不存在，请重新选择。':!image.enabled?'所选图像服务已停用，请在扩展中心启用。':({codex_image:'支持文生图与图生图，使用本机 Codex / ChatGPT 账号；不支持 LoRA。',openai_image:'当前 Images 兼容接口仅支持文字生图，不支持参考图与 LoRA。',image_http:'可向网关传递参考图和 LoRA，实际能力取决于网关。'}[driver]||'请核对所选服务能力。');
   const off=field('web_policy').value==='off',search=selectedService('search');
   const searchReady=!!search?.enabled;
   const searchTools=toolState(['web_search','read_web']);
   $('cp-search-status').textContent=(off?'当前已关闭联网检索。':'')+(!searchReady?'搜索连接器未选择或已停用。':`当前使用：${search.name}。`)+(searchTools.every(t=>t.bound)?'搜索与原文读取已授权，调用遵守工具审批策略。':'搜索与原文读取尚未全部授权。');
   const skill=newsSkill(),bound=!!skill&&checked('skills_ids',skill.id),newsTool=toolState(['news_collect'])[0];
   $('cp-news-status').textContent=(off?'联网关闭，新闻采集不会运行。':'')+(!skill?'尚未安装或启用 news-aggregator-skill，请先在扩展中心添加。':!bound?'原生新闻技能尚未绑定此 Agent。':!newsTool.bound?'技能已绑定，还需授权新闻采集工具。':'原生新闻技能与采集工具已绑定；按实际采集结果汇总，不使用旧版新闻适配。');
   $('cp-news-legacy').hidden=bound;
   const newsButton=form.querySelector('[data-cp-news]');
   newsButton.disabled=!skill||!newsTool.tool||(bound&&newsTool.bound);newsButton.textContent=bound&&newsTool.bound?'原生新闻已配置':'启用原生新闻技能';
   for(const [name] of life){
     const state=toolState([name])[0];
     form.querySelector(`[data-cp-tool-status="${name}"]`).textContent=!state.tool?'工具未安装或已停用':!state.bound?'未授权':off?'已授权 · 联网检索关闭':name!=='weather'&&!searchReady?'已授权 · 缺少可用搜索连接器':'已授权 · 按需调用';
   }
   for(const button of form.querySelectorAll('[data-cp-grant]')){
     const states=toolState(button.dataset.cpGrant.split(','));
     button.disabled=states.every(t=>t.bound)||states.some(t=>!t.tool);
     if(states.length===1)button.textContent=states[0].bound?'已授权':states[0].name==='generate_image'?'授权图片生成':'授权工具';
   }
 };
 $('cp-minimax-open').onclick=openVoiceSettings;
 $('cp-image-clear').onclick=()=>{
   const driver=selectedService('image')?.driver;
   if(!['codex_image','image_http'].includes(driver))field('cp.media.reference_media_id').value='';
   if(driver!=='image_http')field('cp.media.lora').value='';
   form.dispatchEvent(new Event('change',{bubbles:true}));
 };
 const click=e=>{
   const button=e.target.closest('[data-cp-grant],[data-cp-news]');if(!button||button.disabled)return;
   if(button.hasAttribute('data-cp-news'))grant(['news_collect'],newsSkill());
   else grant(button.dataset.cpGrant.split(','));
 };
 form.addEventListener('click',click);form.addEventListener('change',render);render();
 return ()=>{form.removeEventListener('click',click);form.removeEventListener('change',render);};
}

export function readAgentFields(form){
 const c={memory:{},care:{},media:{},safety:{}};
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
 const f=$('ai-editor-form');const hints={deepseek:'官方 Search / Fetch：地址 https://api.deepseek.com，填写 DeepSeek API Key。新闻核验需同时授权 web_search 和 read_web；按实际成功查询计费。',agent_reach:'复用本机 mcporter 的 Exa 通道，服务地址与密钥可留空。',memory_http:'填写适配网关完整地址。接收 operation、namespace、query / content，按 namespace 隔离用户。详见项目接入文档。',tts_http:'填写网关完整地址，返回音频或 audio_base64。火山及 CosyVoice 的专有协议需由网关转换。',image_http:'网关接收 prompt、reference_image、lora、lora_scale，返回 image_base64 或公开图片 URL。',qdrant:'填写 Qdrant 根地址，单独选择可用的 Embedding 模型。连接测试会创建指定集合（如果不存在）。',elevenlabs:'根地址示例：https://api.elevenlabs.io；音色 ID 在 ElevenLabs 控制台获取。',minimax:'根地址示例：https://api.minimax.io；填写实际可用的语音模型与音色。',openai_speech:'填写兼容 API Base URL，将请求 /audio/speech。',openai_image:'填写兼容 API Base URL，将请求 /images/generations；此协议不传 LoRA 或参考图。'};
 hints.codex_image='使用本机已登录的 Codex / ChatGPT 账号，支持文生图和图生图，无需 API Key。测试能力会真实生成一张图片并消耗账号额度，通常需要几分钟；结果可预览下载。';
 const visibility=()=>{
  const category=f.elements.category.value,driver=f.elements.driver.value;
  const codex=driver==='codex_image';
  for(const [name,visible] of Object.entries({url:!codex,api_key:!codex,clear_secret:!codex,voice_id:category==='tts',public_key:driver==='langfuse',collection:driver==='qdrant',embedding_provider_id:driver==='qdrant',model:!codex&&['tts','image'].includes(category)}))f.elements.namedItem(name).closest('label').hidden=!visible;
  f.elements.timeout.max=codex?'900':'120';
  if(codex&&Number(f.elements.timeout.value)<180)f.elements.timeout.value='300';
  if(!codex&&Number(f.elements.timeout.value)>120)f.elements.timeout.value='45';
 };
 const hint=()=>{visibility();if(f.elements.driver.value==='deepseek'&&!f.elements.url.value)f.elements.url.value='https://api.deepseek.com';$('service-hint').textContent=hints[f.elements.driver.value]||'填写服务商提供的地址与凭据。测试连接可能产生一次 API 调用费用。';};
 f.elements.category.onchange=()=>{f.elements.driver.innerHTML=serviceDrivers[f.elements.category.value].map(([k,v])=>`<option value="${k}">${v}</option>`).join('');hint();};f.elements.driver.onchange=hint;hint();
}
export function readService(form){const get=k=>form.get(k)||'',codex=get('driver')==='codex_image';return {category:get('category'),driver:get('driver'),url:codex?'':get('url'),model:codex?'':get('model'),api_key:codex?null:get('api_key')||null,clear_secret:codex||form.has('clear_secret'),voice_id:get('voice_id'),public_key:get('public_key'),collection:get('collection'),timeout:Number(get('timeout')),embedding_provider_id:get('embedding_provider_id'),enabled:form.has('enabled')};}

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
 $('memory-content').innerHTML=`<form id="companion-profile">${group('用户档案','只填写用户明确提供的信息',formGrid(field('display_name','用户名称',p.display_name)+select('age_status','用户年龄声明',[['unknown','未确认 · 普通陪伴'],['adult','已自我声明成年'],['minor','未成年 · 普通陪伴']],p.age_status)+field('preferred_name','喜欢的称呼',p.preferred_name)+field('city','所在城市（可选）',p.city)+field('birthday','生日（可选）',p.birthday)+field('relationship_start','关系起始日期（可选）',p.relationship_start,'date')+select('closeness','亲密度',[['初识','初识'],['熟悉','熟悉'],['亲密','亲密']],p.closeness))+check('memory_consent','允许此用户保存长期记忆',p.memory_consent)+check('care_consent','允许此联系人接收主动关怀',p.care_consent)+`<button class="button primary">保存用户档案</button>`,true)}</form><div class="companion-summary"><h4>近期摘要</h4><p>${esc(data.summary||'对话后自动整理，尚无摘要。')}</p><small>近期情绪：${esc(data.mood||'尚未分析')}</small></div><div class="companion-memory-actions"><button class="button secondary" id="add-memory">添加记忆</button><button class="text-button" id="preview-prompt">预览实际提示词</button><button class="text-button ai-delete" id="forget-all">遗忘此用户全部记忆</button></div><div class="memory-cards">${data.memories.map(m=>`<article class="memory-card"><span class="pill">${esc({fact:'事实',preference:'喜好',event:'事件',worry:'烦恼',summary:'摘要'}[m.category])}${m.pinned?' · 长期保留':''}</span><h4>${esc(m.key)}</h4><p>${esc(m.content)}</p><small>${m.pinned?'不自动过期':'到期 '+new Date(m.expires_at).toLocaleDateString('zh-CN')}</small><div><button class="text-button" data-memory-edit="${m.id}">编辑</button><button class="text-button" data-memory-forget="${m.id}">遗忘</button></div></article>`).join('')||'<p class="field-hint">还没有记忆。可以先在测试台聊聊喜好、生日或最近的计划。</p>'}</div><pre id="prompt-preview" hidden></pre>`;
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
 renderAgentSchedules(ctx,library,renderCareSchedules);
}
async function renderCareSchedules(){
 try{const rows=await ctx.api('/api/ai/schedules');const host=$('care-schedule-content');if(!host)return;host.innerHTML=`<div class="companion-banner"><span>GENTLE ROUTINES</span><h3>在合适的时候，想起你</h3><p>关怀按时区执行，遵守免打扰时间。服务关闭或暂停期间的旧任务不会集中补发。</p></div><button class="button primary" id="new-care">添加关怀日程</button><div class="care-list">${rows.map(s=>`<article class="care-card"><div><span class="pill ${s.enabled?'green':''}">${s.enabled?'已启用':'已停用'}</span><h4>${esc(s.title)}</h4><p>${esc(s.instruction)}</p><small>${esc(ctx.state.contacts.find(c=>c.id===s.contact_id)?.lookup||'联系人已移除')} · ${esc(s.kind==='cron'?s.cron:s.at)} · ${esc(s.timezone)} · ${s.delivery==='auto'?'按会话模式发送':'先生成草稿'}</small><p>${esc(s.last_status)}${s.next_at?' · 下次 '+new Date(s.next_at).toLocaleString('zh-CN'):''}</p></div><div><button class="text-button" data-care-edit="${s.id}">编辑</button><button class="text-button ai-delete" data-care-delete="${s.id}">删除</button></div></article>`).join('')||'<p class="field-hint">可以添加早安、晚安，或某次面试后的关心。</p>'}</div>`;
 $('new-care').onclick=()=>scheduleEditor();host.onclick=async e=>{const b=e.target.closest('button');try{if(b?.dataset.careEdit)scheduleEditor(rows.find(x=>x.id===b.dataset.careEdit));if(b?.dataset.careDelete&&confirm('删除这个关怀日程？')){await ctx.api('/api/ai/schedules/'+b.dataset.careDelete,{method:'DELETE'});await renderCareSchedules();}}catch(error){ctx.toast(error.message,true);}};
 }catch(e){ctx.toast(e.message,true);}
}
function scheduleEditor(s={}){const defaultDate=new Date(Date.now()+3600000);defaultDate.setMinutes(defaultDate.getMinutes()-defaultDate.getTimezoneOffset());const html=field('title','日程名称',s.title||'早安问候')+formGrid(select('agent_id','关怀 Agent',options('agents').filter(x=>x[0]),s.agent_id||library.agents[0]?.id)+select('contact_id','接收联系人',ctx.state.contacts.map(c=>[c.id,c.lookup]),s.contact_id||ctx.state.contacts[0]?.id))+select('kind','执行方式',[['once','仅一次'],['cron','Cron 重复执行']],s.kind||'once')+formGrid(field('at','执行日期时间（一次性）',s.at?s.at.slice(0,16):defaultDate.toISOString().slice(0,16),'datetime-local')+field('cron','Cron 表达式（重复）',s.cron||'0 9 * * *')+field('timezone','执行时区',s.timezone||'Asia/Shanghai'))+`<p class="field-hint">Cron 五段：分 时 日 月 星期。每天早上 9 点：0 9 * * *；每天晚 10 点：0 22 * * *。</p>`+area('instruction','她要关心的事情',s.instruction||'用自然、简短的语气说早安，不强求用户回复。')+select('delivery','发送方式',[['draft','生成草稿，在面板确认'],['auto','按会话模式自动发送']],s.delivery||'draft')+check('enabled','启用日程',s.enabled)+`<p class="field-hint">实际执行还需联系人已绑定该 Agent、开启监听及 AI 模式，并在用户档案同意主动关怀。</p>`;modal('关怀日程',html,async f=>{const body=Object.fromEntries(f);body.enabled=f.has('enabled');await ctx.api('/api/ai/schedules'+(s.id?'/'+s.id:''),{method:s.id?'PUT':'POST',body});await renderCareSchedules();});}
