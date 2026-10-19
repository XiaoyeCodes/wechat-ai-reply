// Organize the existing fields without duplicating controls or changing saved IDs.
export function organizeEditor(data,esc,{bundles,capabilityState,serviceFields}){
 const $=id=>document.getElementById(id),form=$('ai-editor-form'),root=$('ai-editor-fields');
 const field=name=>form.elements.namedItem(name),label=name=>field(name).closest('label');
 const kind=field('kind').value,workflow=kind==='workflow';
 const selected=name=>[...form.querySelectorAll(`input[name="${name}"]:checked`)].map(el=>el.value);
 const fold=(title,description,...nodes)=>{
   const box=document.createElement('details');box.className='ai-editor-section';
   box.innerHTML=`<summary><strong>${title}</strong><span>${description}</span></summary><div class="ai-editor-section-body"></div>`;
   box.lastElementChild.append(...nodes);return box;
 };
 const tabs=[['basics','角色与模型','先定义身份、职责和说话方式，再选择聊天模型。'],['abilities','工具与技能','选择能完成的任务，并授权实际使用的工具。'],['memory','知识与记忆','知识库提供参考资料，长期记忆保存每位用户的信息。'],['behavior','回复与关怀','设置图片理解、语音回复、聊天上下文与主动关怀。'],['advanced','运行设置','设置联网时机、处理流程和运行记录。']];
 const nav=document.createElement('div');nav.id='ai-editor-tabs';nav.className='agent-editor-tabs';nav.role='tablist';nav.setAttribute('aria-label','Agent 配置分类');
 const panes={};
 for(const [key,title,description] of tabs){
   const button=document.createElement('button');button.type='button';button.role='tab';button.id='agent-tab-'+key;button.dataset.editorTab=key;button.setAttribute('aria-controls','agent-pane-'+key);button.textContent=title;
   if(['memory','behavior'].includes(key)&&!workflow)button.hidden=true;
   nav.append(button);
   const pane=document.createElement('section');pane.id='agent-pane-'+key;pane.className='agent-editor-pane';pane.role='tabpanel';pane.setAttribute('aria-labelledby',button.id);
   pane.innerHTML=`<div class="agent-pane-heading"><h3>${title}</h3><p>${description}</p></div>`;panes[key]=pane;
 }
 root.before(nav);
 const switchTab=key=>{
   for(const [id,pane] of Object.entries(panes))pane.hidden=id!==key;
   for(const button of nav.children){const active=button.dataset.editorTab===key;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
   root.scrollTop=0;
 };
 nav.onclick=e=>{const button=e.target.closest('[data-editor-tab]');if(button)switchTab(button.dataset.editorTab);};
 nav.onkeydown=e=>{
   if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
   const buttons=[...nav.children].filter(b=>!b.hidden),index=buttons.indexOf(document.activeElement);if(index<0)return;
   e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
   buttons[next].click();buttons[next].focus();
 };

 const basics=document.createElement('div');basics.className='ai-form-grid';basics.append(label('name'),label('description'));panes.basics.append(basics);
 const model=label('provider_id'),prompt=label('prompt');
 if(workflow)panes.basics.append(model,prompt);
 if(kind==='hermes'){
   panes.basics.append($('ai-hermes-local'),label('hermes_prompt'));
 }
 if(kind==='vibe')panes.basics.append(label('vibe_provider_id'));
 const intro=document.createElement('p');intro.className='field-hint';
 intro.textContent={workflow:'提示词决定角色与表达；工具与技能决定能做什么。实际是否自动回复，在联系人的聊天设置中选择。',hermes:'此 Agent 由 Hermes 执行电脑任务。在工具与技能中选择技能，在运行设置中管理工作目录、联系人授权与审批。',vibe:'此 Agent 使用金融研究引擎。在工具与技能中查看研究能力，在运行设置中调整执行参数。',external:'任务由外部服务处理。在运行设置中填写服务地址与凭据。'}[kind];
 panes.basics.append(intro);

 const knowledge=label('knowledge_mode'),knowledgeGrid=knowledge.previousElementSibling,knowledgeHeading=knowledgeGrid.previousElementSibling;
 const knowledgeDetails=fold('参考资料','只检索分配给此 Agent 的知识库',knowledgeHeading,knowledgeGrid,knowledge);
 const skills=form.querySelector('[name="skills_ids"]')?.closest('.ai-check-grid')||[...$('ai-workflow-fields').children].find(el=>el.classList.contains('field-label')&&el.textContent.includes('Skill'))?.nextElementSibling;
 const tools=form.querySelector('[name="tools_ids"]')?.closest('.ai-check-grid')||[...$('ai-workflow-fields').children].find(el=>el.classList.contains('field-label')&&el.textContent.includes('工具'))?.nextElementSibling;
 const skillHeading=skills.previousElementSibling,toolHeading=tools.previousElementSibling;
 skillHeading.textContent='技能：完成任务的方法';toolHeading.textContent='工具：允许执行的动作';
 const rights=fold('技能与工具授权','查看用途、绑定技能、调整具体权限',skillHeading,skills,toolHeading,tools);
 rights.id='agent-rights';
 rights.open=true;
 const decorate=(grid,resources,isSkill)=>{
   grid.classList.add('agent-resource-choices');
   for(const input of grid.querySelectorAll('input')){
     const resource=resources.find(r=>r.id===input.value);if(!resource)continue;
     const parent=input.closest('label'),text=document.createElement('span');
     const bundle=isSkill?bundles.find(b=>b.skill===resource.name):null;
     const friendly={news_collect:'新闻采集',web_search:'搜索网页',read_web:'读取网页',weather:'查询天气',music_search:'查找音乐',places_search:'查找地点',movie_search:'电影资料',generate_image:'生成图片',speak:'合成音频文件',clock:'当前时间',calculator:'计算器',knowledge_search:'检索知识库',remember:'保存记忆',forget:'遗忘记忆',memory_search:'查找记忆',set_reminder:'设置提醒',platform_search:'平台内搜索',platform_read:'读取平台内容',platform_browse:'浏览平台热门',platform_transcribe:'视频语音转写'};
     const title=isSkill?(bundle?.name||(resource.name==='news-aggregator-skill'?'新闻汇总':resource.name)):(friendly[resource.builtin]||resource.name);
     input.setAttribute('aria-label',title);
     const description=(isSkill&&resource.name==='news-aggregator-skill'?'汇总多来源新闻，按主题筛选并整理要点，支持阅读原文。':'')||resource.description||(isSkill?resource.instructions?.split('\n').filter(Boolean).slice(0,2).join(' '):'')||'尚未填写功能说明，请在扩展中心补充后再启用。';
     text.innerHTML=`<strong>${esc(title)}</strong><small>${esc(description.slice(0,240))}</small><em>${esc(resource.name)} · ${resource.enabled===false?'已停用':isSkill?'按任务使用':resource.approval==='confirm'?'执行前需确认':'允许自动执行'}</em>`;
     parent.replaceChildren(input,text);

   }
 };
 decorate(skills,data.skills,true);decorate(tools,data.tools,false);
 const warning=document.createElement('p');warning.id='ai-skill-dependencies';warning.className='field-hint ai-dependency-warning';skills.after(warning);
 const roleHint=document.createElement('p');roleHint.className='field-hint';roleHint.textContent='勾选技能时自动选中所需工具，保存配置后生效。';rights.lastElementChild.prepend(roleHint);

 const companion=$('ai-companion-fields');
 companion.querySelector('.companion-config-heading')?.remove();
 const media=field('cp.media.image_service_id').closest('.companion-section'),network=field('cp.search_service_id').closest('.companion-section');
 // Keep saved media values and skill bindings in the form, without a second
 // image/audio settings entry. Native voice defaults have their own section.
 media.hidden=true;
 network.querySelector('summary strong').textContent='网页搜索工具连接';
 network.querySelector('summary span').textContent='选择搜索服务，授权搜索与原文读取';
 const policy=fold('联网与来源规则','这些规则共同约束已授权的联网工具');policy.id='agent-network-policy';
 // Move the same policy controls into their only visible home.
 for(const name of ['web_policy','source_policy']){const node=label(name),hint=node.nextElementSibling;policy.lastElementChild.append(node,hint);}
 const news=$('cp-news-status').closest('.cp-feature');
 const life=$('cp-life-tools').closest('.cp-feature');
 const weather=label('cp.weather_enabled'),weatherHint=weather.nextElementSibling;
 const daily=fold('聊天上下文','是否主动把用户所在城市的天气带入对话',weather,weatherHint);
 weatherHint.textContent='城市来自当前用户的档案；开启后自动获取天气作为聊天背景。用户主动询问天气，需在“工具与技能 → 生活查询”中授权查询天气工具。';
 life.remove();
 const legacy=$('cp-news-legacy');policy.lastElementChild.append(legacy);news.remove();
 const vision=field('cp.media.understand_images').closest('.cp-feature');
 const incoming=fold('图片理解','设置收到图片后的理解方式',vision);
 const voice=field('cp.voice.preset').closest('details');
 const memory=field('cp.memory.enabled').closest('.companion-section');
 const care=field('cp.care.enabled').closest('.companion-section');
 const safety=field('cp.safety.emotion_enabled').closest('.companion-section');
 const privacy=fold('运行记录与隐私','是否连接外部观测服务，是否传输对话正文',label('cp.safety.observability_service_id'),label('cp.safety.export_content'));
 privacy.lastElementChild.append(safety.querySelector('.field-hint'));
 safety.querySelector('summary strong').textContent='回复方式与安全关怀';
 safety.querySelector('summary span').textContent='情绪回应与关系边界';
 for(const group of [memory,care])group.querySelector('summary strong').textContent=group.querySelector('summary strong').textContent.replace(/^\d+ · /,'');
 const searchTitle=network.querySelector('.cp-feature h4');searchTitle.textContent='搜索与原文读取';
 // Each resource has one checkbox. Topic groups are presentation, never new grants.
 const toolCategory=resource=>{
   const name=resource.builtin;
   if(['web_search','read_web','news_collect','platform_search','platform_read','platform_browse','platform_transcribe'].includes(name))return '网页与内容';
   if(['weather','music_search','places_search','movie_search'].includes(name))return '生活查询';
   if(['remember','forget','memory_search','set_reminder'].includes(name))return '记忆与提醒';
   if(['clock','calculator','knowledge_search'].includes(name))return '计算与资料';
   if(resource.kind==='mcp')return '外部应用（MCP）';
   return '其他工具';
 };
 const skillCategory=resource=>resource.name==='codex-image-generation'?'图片创作':
   ['news-aggregator-skill','web-video-reader','agent-reach-platforms'].includes(resource.name)?'内容检索与阅读':'其他技能';
 const resourceGroups=[];
 for(const [grid,resources,category] of [[skills,data.skills,skillCategory],[tools,data.tools,toolCategory]]){
   const mapped=new Map();
   for(const input of [...grid.querySelectorAll('input')]){
     const resource=resources.find(r=>r.id===input.value);if(!resource)continue;
     // Media actions are configured through their skill/service entry. Keep
     // existing grants in the form so saving unrelated settings preserves them.
     if(grid===tools&&['generate_image','speak'].includes(resource.builtin)){
       input.closest('label').hidden=true;continue;
     }
     const name=category(resource);let group=mapped.get(name);
     if(!group){group=fold(name,'');group.classList.add('agent-resource-group');group.lastElementChild.classList.add('agent-resource-choices');mapped.set(name,group);}
     const choice=input.closest('label');
     choice.dataset.resourceChoice='';group.lastElementChild.append(choice);
     if(input.checked)group.open=true;
   }
   grid.classList.remove('agent-resource-choices');grid.classList.add('agent-resource-groups');
   for(const group of mapped.values()){grid.append(group);resourceGroups.push(group);}
 }
 const searchBox=document.createElement('label');searchBox.className='ai-field agent-resource-search';
 searchBox.innerHTML='<span>查找工具或技能</span><input type="search" placeholder="例如：新闻、天气、生图" aria-label="查找工具或技能">';
 const empty=document.createElement('p');empty.className='field-hint';empty.textContent='没有匹配的工具或技能。';empty.hidden=true;
 rights.lastElementChild.prepend(searchBox,empty);
 const filterResources=()=>{
   const query=searchBox.querySelector('input').value.trim().toLowerCase();let total=0;
   for(const group of resourceGroups){
     let count=0;
     for(const item of group.querySelectorAll('[data-resource-choice]')){
       item.hidden=!!query&&!item.textContent.toLowerCase().includes(query);if(!item.hidden)count++;
     }
     group.hidden=!count;if(query&&count)group.open=true;total+=count;
     const inputs=[...group.querySelectorAll('input[type=checkbox]')];
     group.querySelector('summary span').textContent=`${inputs.filter(input=>input.checked).length} / ${inputs.length} 已选`;
   }
   empty.hidden=total>0;
 };
 searchBox.querySelector('input').oninput=filterResources;
 if(workflow){
   panes.abilities.append(rights,network);
   root.append(media);
   panes.memory.append(knowledgeDetails,memory);
   panes.behavior.append(incoming,voice,daily,safety,care);
   panes.advanced.append(policy,privacy);
   companion.remove();
 }else{
   // Keep dormant configuration in the form for a lossless save.
   const dormant=document.createElement('div');dormant.hidden=true;dormant.append(network,media,knowledgeDetails,rights,companion,incoming,daily,memory,care,safety,privacy,policy);panes.advanced.append(dormant);
   const note=document.createElement('p');note.className='field-hint';note.textContent=kind==='hermes'?'Hermes 使用自己执行引擎中的工具和下方选定的技能；操作审批在运行设置中配置。':kind==='vibe'?'以下为金融研究引擎的工具与技能。具体调用由该引擎按问题选择。':'外部服务的技能和工具由它自己的配置管理。';panes.abilities.append(note);
   if(kind==='hermes')panes.abilities.append($('ai-hermes-skills'));
   if(kind==='vibe')for(const details of [...$('ai-vibe-fields').querySelectorAll(':scope > details')])panes.abilities.append(details);
 }
 // The workflow container now holds only temperature, tool rounds and steps.
 panes.advanced.append($('ai-workflow-fields'),$('ai-external-fields'),$('ai-hermes-fields'),$('ai-vibe-fields'));
 if(workflow){const caption=document.createElement('p');caption.className='field-hint';caption.textContent='一般保留默认值即可。这里调整回复温度、工具调用上限和处理顺序。';panes.advanced.prepend(caption);}
 root.append(...Object.values(panes));
 // For non-workflow engines the unused base model and prompt remain hidden.
 if(!workflow){model.hidden=true;prompt.hidden=true;}
 switchTab('basics');

 const render=()=>{
   filterResources();
   const toolIds=selected('tools_ids'),skillIds=selected('skills_ids');
   const warnings=[];
   for(const bundle of bundles){
     const state=capabilityState(bundle,data,toolIds,skillIds,field(serviceFields[bundle.service])?.value,field('web_policy').value);
     if(state.skill&&skillIds.includes(state.skill.id)&&(!state.bound||state.missing.length))warnings.push(`${bundle.name}：${!state.bound?'所需工具尚未全部授权；':''}${state.missing.join('；')}`);
   }
   warning.textContent=warnings.join('。');warning.hidden=!warnings.length;
 };
 // Track only dependencies added during this edit, preserving existing grants.
 const autoTools=new Set();
 const change=e=>{
   const target=e.target;
   if(target.matches?.('[name="tools_ids"]'))autoTools.delete(target.value);
   if(target.matches?.('[name="skills_ids"]')){
     const skill=data.skills.find(s=>s.id===target.value),bundle=bundles.find(b=>b.skill===skill?.name);
     if(target.checked&&bundle){
       const state=capabilityState(bundle,data,selected('tools_ids'),selected('skills_ids'));
       for(const el of form.querySelectorAll('[name="tools_ids"]')){
         if(state.needed.includes(el.value)&&!el.checked&&data.tools.find(t=>t.id===el.value)?.enabled){el.checked=true;autoTools.add(el.value);}
       }
       const serviceField=field(serviceFields[bundle.service]);
       if(serviceField&&!serviceField.value){const available=data.services.filter(s=>s.category===bundle.service&&s.enabled);if(available.length===1)serviceField.value=available[0].id;}
     }
     const selectedSkills=selected('skills_ids'),toolIds=selected('tools_ids');
     const needed=new Set(bundles.filter(b=>b.skill&&data.skills.some(s=>s.name===b.skill&&selectedSkills.includes(s.id))).flatMap(b=>capabilityState(b,data,toolIds,selectedSkills).needed));
     for(const el of form.querySelectorAll('[name="tools_ids"]'))if(autoTools.has(el.value)&&!needed.has(el.value)){el.checked=false;autoTools.delete(el.value);}
   }
   render();
 };
 const invalid=e=>{const pane=e.target.closest('.agent-editor-pane');if(pane){switchTab(Object.keys(panes).find(k=>panes[k]===pane));for(let node=e.target.parentElement;node&&node!==pane;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;}};
 form.addEventListener('change',change);form.addEventListener('invalid',invalid,true);render();
 return ()=>{nav.remove();form.removeEventListener('change',change);form.removeEventListener('invalid',invalid,true);};
}
