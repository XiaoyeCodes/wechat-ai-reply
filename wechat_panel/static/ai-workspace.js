import {organizeEditor} from './agent-editor.js?v=skill-check-1';
// Presentation only: existing resource IDs and execution permissions stay authoritative.
export const sections = {
  agents: {label:'我的 Agent', tabs:['agents']},
  extensions: {label:'扩展中心', tabs:['skills','tools']},
  resources: {label:'知识库', tabs:['knowledge']},
  runs: {label:'运行记录', tabs:['runs']},
  settings: {label:'设置', tabs:['providers','services','mcp','preferences']},
};
export const sectionFor = tab => ['memory','schedules'].includes(tab)?'agents': Object.keys(sections).find(key=>sections[key].tabs.includes(tab)) || 'extensions';

export function renderNavigation(tab, labels) {
  const section=sectionFor(tab);
  document.querySelectorAll('[data-ai-section]').forEach(button=>{
    const active=button.dataset.aiSection===section;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
  });
  const subtabs=document.getElementById('ai-subtabs');
  if(section==='settings'){subtabs.hidden=true;return;}
  const tabs=sections[section].tabs;
  const html=tabs.length===1?'':tabs.map(key=>`<button type="button" role="tab" data-ai-tab="${key}" aria-selected="${key===tab}" tabindex="${key===tab?0:-1}"${key==='tools'?' class="ai-advanced-link"':''}>${labels[key]}</button>`).join('');
  if(subtabs.innerHTML!==html)subtabs.innerHTML=html;
  subtabs.hidden=tabs.length===1;
  subtabs.setAttribute('aria-label',sections[section].label+'分类');
}

export function bindTabKeys(element) {
  element.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const buttons=[...element.querySelectorAll('[role="tab"]')],index=buttons.indexOf(document.activeElement);
    if(index<0)return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    const key=buttons[next].dataset.aiTab,section=buttons[next].dataset.aiSection,ownerTab=buttons[next].dataset.agentPage;
    buttons[next].click();
    element.querySelector(ownerTab?`[data-agent-page="${ownerTab}"]`:key?`[data-ai-tab="${key}"]`:`[data-ai-section="${section}"]`)?.focus();
  });
}

const bundles = [
  {id:'news',name:'新闻汇总',description:'汇总多来源新闻',tools:['news_collect'],skill:'news-aggregator-skill'},
  {id:'search',name:'联网查证',description:'搜索网页，读取原文',tools:['web_search','read_web'],service:'search'},
  {id:'video',name:'视频总结',description:'读取字幕，提炼视频内容',tools:['read_web'],skill:'web-video-reader'},
  {id:'platforms',name:'多平台阅读',description:'平台搜索、正文与热门内容',tools:['platform_search','platform_read','platform_browse'],skill:'agent-reach-platforms'},
  {id:'voice',name:'音频文件工具',description:'生成面板试听、下载文件；微信语音在联系人中设置',tools:['speak'],service:'tts'},
  {id:'image',name:'图像生成',description:'文字生图、上传照片后修改',tools:['generate_image'],service:'image',skill:'codex-image-generation'},
];
const serviceFields={search:'cp.search_service_id',tts:'cp.media.tts_service_id',image:'cp.media.image_service_id'};

export function capabilityState(bundle, data, toolIds, skillIds, serviceId='', webPolicy='auto') {
  const missing=[];const needed=[];
  for(const builtin of bundle.tools){
    const candidates=data.tools.filter(tool=>tool.kind==='builtin'&&tool.builtin===builtin);
    const selected=candidates.find(tool=>toolIds.includes(tool.id));
    const tool=selected||candidates.find(tool=>tool.enabled)||candidates[0];
    if(!tool)missing.push(`缺少 ${builtin} 工具`);
    else if(!tool.enabled)missing.push(`${tool.name} 已停用`);
    if(tool)needed.push(tool.id);
  }
  const skill=bundle.skill?data.skills.find(skill=>skill.name===bundle.skill):null;
  if(bundle.skill&&!skill)missing.push(`缺少 ${bundle.skill} 技能`);
  if(skill&&!skill.enabled)missing.push('所需技能已停用');
  const bound=needed.length===bundle.tools.length&&needed.every(id=>toolIds.includes(id))&&(!bundle.skill||!!skill&&skillIds.includes(skill.id));
  if(bundle.service){
    const service=data.services.find(service=>service.id===serviceId&&service.category===bundle.service);
    if(!service)missing.push('未选择'+{search:'搜索',tts:'语音',image:'绘图'}[bundle.service]+'服务');
    else if(!service.enabled)missing.push('连接器已停用');
  }
  if(['search','video','platforms','news'].includes(bundle.id)&&webPolicy==='off')missing.push('当前已关闭联网');
  return {bound,needed,skill,missing};
}

export function agentCapabilityNames(agent,data) {
  if(agent.kind!=='workflow')return [];
  const names=bundles.filter(bundle=>capabilityState(bundle,data,agent.tool_ids||[],agent.skill_ids||[]).bound).map(bundle=>bundle.name);
  if(agent.companion?.memory?.enabled)names.push('长期记忆');
  return names;
}

export function organizeAgentEditor(data,esc){
  return organizeEditor(data,esc,{bundles,capabilityState,serviceFields});
}

export function mergeCompanion(original={},updated={}) {
  const merged={...original};
  for(const [key,value] of Object.entries(updated))merged[key]=value&&typeof value==='object'&&!Array.isArray(value)?mergeCompanion(original[key]||{},value):value;
  return merged;
}
