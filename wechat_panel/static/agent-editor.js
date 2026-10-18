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
 const tabs=[['basics','基本设定','名称、职责与说话方式'],['abilities','能力与技能','能做什么、何时调用'],['memory','记忆与关怀','记住用户、安排关怀'],['advanced','高级设置','运行细节与权限']];
 const nav=document.createElement('div');nav.id='ai-editor-tabs';nav.className='agent-editor-tabs';nav.role='tablist';nav.setAttribute('aria-label','Agent 配置分类');
 const panes={};
 for(const [key,title,description] of tabs){
   const button=document.createElement('button');button.type='button';button.role='tab';button.id='agent-tab-'+key;button.dataset.editorTab=key;button.setAttribute('aria-controls','agent-pane-'+key);button.textContent=title;
   if(key==='memory'&&!workflow)button.hidden=true;
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
 const enabled=label('enabled');panes.basics.append(enabled);
 const model=label('provider_id'),prompt=label('prompt');
 if(workflow)panes.basics.append(model,prompt);
 if(kind==='hermes'){
   panes.basics.append($('ai-hermes-local'),label('hermes_prompt'));
 }
 if(kind==='vibe')panes.basics.append(label('vibe_provider_id'));
 const intro=document.createElement('p');intro.className='field-hint';
 intro.textContent={workflow:'完整系统提示词决定角色与表达。模型连接提供回复能力；知识、技能和工具在「能力与技能」中设置。',hermes:'此 Agent 由 Hermes 执行电脑任务。能力与技能中管理 Skill，高级设置中管理工作目录、联系人授权与审批。',vibe:'此 Agent 使用金融研究引擎。能力与技能中查看研究工具，高级设置中调整联网与运行参数。',external:'此 Agent 连接外部服务，任务由该服务处理。高级设置中填写服务地址与凭据。'}[kind];
 panes.basics.append(intro);

 const knowledge=label('knowledge_mode'),knowledgeGrid=knowledge.previousElementSibling,knowledgeHeading=knowledgeGrid.previousElementSibling;
 const knowledgeDetails=fold('参考资料','只检索分配给此 Agent 的知识库',knowledgeHeading,knowledgeGrid,knowledge);
 const skills=form.querySelector('[name="skills_ids"]')?.closest('.ai-check-grid')||[...$('ai-workflow-fields').children].find(el=>el.classList.contains('field-label')&&el.textContent.includes('Skill'))?.nextElementSibling;
 const tools=form.querySelector('[name="tools_ids"]')?.closest('.ai-check-grid')||[...$('ai-workflow-fields').children].find(el=>el.classList.contains('field-label')&&el.textContent.includes('工具'))?.nextElementSibling;
 const skillHeading=skills.previousElementSibling,toolHeading=tools.previousElementSibling;
 skillHeading.textContent='技能：完成任务的方法';toolHeading.textContent='工具：允许执行的动作';
 const rights=fold('技能与工具授权','查看用途、绑定技能、调整具体权限',skillHeading,skills,toolHeading,tools);
 rights.id='agent-rights';
 const decorate=(grid,resources,isSkill)=>{
   grid.classList.add('agent-resource-choices');
   for(const input of grid.querySelectorAll('input')){
     const resource=resources.find(r=>r.id===input.value);if(!resource)continue;
     const parent=input.closest('label'),text=document.createElement('span');
     const bundle=isSkill?bundles.find(b=>b.skill===resource.name):null;
     const friendly={news_collect:'新闻采集',web_search:'搜索网页',read_web:'读取网页',weather:'查询天气',music_search:'查找音乐',places_search:'查找地点',movie_search:'电影资料',generate_image:'生成图片',speak:'合成音频文件',clock:'当前时间',calculator:'计算器',knowledge_search:'检索知识库',remember:'保存记忆',forget:'遗忘记忆',memory_search:'查找记忆',set_reminder:'设置提醒',platform_search:'平台内搜索',platform_read:'读取平台内容',platform_browse:'浏览平台热门',platform_transcribe:'视频语音转写'};
     const title=isSkill?(bundle?.name||(resource.name==='news-aggregator-skill'?'新闻汇总':resource.name)):(friendly[resource.builtin]||resource.name);
     input.setAttribute('aria-label',title);
     const description=resource.description||(isSkill?resource.instructions?.split('\n').filter(Boolean).slice(0,2).join(' '):'')||'尚未填写功能说明，请在扩展中心补充后再启用。';
     text.innerHTML=`<strong>${esc(title)}</strong><small>${esc(description.slice(0,240))}</small><em>${esc(resource.name)} · ${resource.enabled===false?'已停用':isSkill?'按任务使用':resource.approval==='confirm'?'执行前需确认':'允许自动执行'}</em>`;
     parent.replaceChildren(input,text);
     if(bundle){
       const wrap=document.createElement('div');wrap.className='agent-choice-wrap';parent.before(wrap);wrap.append(parent);
       const action=document.createElement('button');action.type='button';action.className='button secondary';action.dataset.enableCapability=bundle.id;action.textContent='同时添加所需工具';wrap.append(action);
     }
   }
 };
 decorate(skills,data.skills,true);decorate(tools,data.tools,false);
 const warning=document.createElement('p');warning.id='ai-skill-dependencies';warning.className='field-hint ai-dependency-warning';skills.after(warning);
 const roleHint=document.createElement('p');roleHint.className='field-hint';roleHint.textContent='技能说明做事步骤，工具负责实际执行。勾选技能后还需授权它需要的工具；保存配置后生效。工具共享时，取消授权可能影响多个技能。';rights.lastElementChild.prepend(roleHint);

 const companion=$('ai-companion-fields'),groups=[...companion.querySelectorAll(':scope > .companion-section')];
 companion.querySelector('.companion-config-heading')?.remove();
 const media=field('cp.media.image_service_id').closest('.companion-section'),network=field('cp.search_service_id').closest('.companion-section');
 media.querySelector('summary strong').textContent='声音与图片';network.querySelector('summary strong').textContent='联网与生活';
 // Move the same policy controls into their only visible home.
 for(const name of ['web_policy','source_policy']){const node=label(name),hint=node.nextElementSibling;$('cp-web-policies').append(node,hint);}
 if(workflow){
   panes.abilities.append(network,media,knowledgeDetails,rights);
   panes.memory.append(companion);
   for(const group of groups.filter(g=>g!==media&&g!==network))group.querySelector('summary strong').textContent=group.querySelector('summary strong').textContent.replace(/^\d+ · /,'');
 }else{
   // Keep dormant configuration in the form for a lossless save.
   const dormant=document.createElement('div');dormant.hidden=true;dormant.append(network,media,knowledgeDetails,rights,companion);panes.advanced.append(dormant);
   const note=document.createElement('p');note.className='field-hint';note.textContent=kind==='hermes'?'Hermes 使用自己执行引擎中的工具和下方选定的技能；操作审批在高级设置中配置。':kind==='vibe'?'以下为金融研究引擎的工具与技能。具体调用由该引擎按问题选择。':'外部服务的技能和工具由它自己的配置管理。';panes.abilities.append(note);
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
   const toolIds=selected('tools_ids'),skillIds=selected('skills_ids');
   const warnings=[];
   for(const bundle of bundles){
     const state=capabilityState(bundle,data,toolIds,skillIds,field(serviceFields[bundle.service])?.value,field('web_policy').value);
     const button=form.querySelector(`[data-enable-capability="${bundle.id}"]`);
     if(button){button.disabled=state.bound;button.textContent=state.bound?'技能与工具已添加':'同时添加所需工具';}
     if(state.skill&&skillIds.includes(state.skill.id)&&(!state.bound||state.missing.length))warnings.push(`${bundle.name}：${!state.bound?'所需工具尚未全部授权；':''}${state.missing.join('；')}`);
   }
   warning.textContent=warnings.join('。');warning.hidden=!warnings.length;
 };
 const click=e=>{
   const button=e.target.closest('[data-enable-capability]');if(!button||button.disabled)return;
   const bundle=bundles.find(b=>b.id===button.dataset.enableCapability),state=capabilityState(bundle,data,selected('tools_ids'),selected('skills_ids'));
   for(const el of form.querySelectorAll('[name="tools_ids"]'))if(state.needed.includes(el.value))el.checked=true;
   for(const el of form.querySelectorAll('[name="skills_ids"]'))if(el.value===state.skill?.id)el.checked=true;
   if(bundle.service&&!field(serviceFields[bundle.service]).value){const available=data.services.filter(s=>s.category===bundle.service&&s.enabled);if(available.length===1)field(serviceFields[bundle.service]).value=available[0].id;}
   form.dispatchEvent(new Event('change',{bubbles:true}));
 };
 const invalid=e=>{const pane=e.target.closest('.agent-editor-pane');if(pane){switchTab(Object.keys(panes).find(k=>panes[k]===pane));for(let node=e.target.parentElement;node&&node!==pane;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;}};
 form.addEventListener('change',render);form.addEventListener('click',click);form.addEventListener('invalid',invalid,true);render();
 return ()=>{nav.remove();form.removeEventListener('change',render);form.removeEventListener('click',click);form.removeEventListener('invalid',invalid,true);};
}
