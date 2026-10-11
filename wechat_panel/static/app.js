import {initAI, refreshAI, scheduleAIRefresh, renderContactAI} from './ai.js';

const paths = {
  chat:'M4 4h16v12H10l-6 4V4Z M8 8h8 M8 12h5',
  users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z M20 3v4 M18 5h4',
  activity:'M3 12h4l3-8 4 16 3-8h4',
  plus:'M12 5v14 M5 12h14', pause:'M8 5v14 M16 5v14', play:'m8 5 11 7-11 7V5Z',
  search:'M21 21l-5-5 M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14',
  shield:'m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6l8-3Z m-4 9 3 3 5-6',
  lock:'M5 10h14v11H5V10Z M8 10V7a4 4 0 0 1 8 0v3',
  panel:'M3 4h18v16H3V4Z M15 4v16 M18 8h1 M18 12h1',
  check:'m5 12 4 4L19 6', send:'m21 3-6 18-4-8-8-4 18-6Z m0 0-10 10',
  arrow:'M5 12h14 m-5-5 5 5-5 5', refresh:'M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 3 M18 18A8 8 0 0 1 5 15',
  close:'M6 6l12 12 M6 18 18 6', info:'M12 8v.01 M12 11v6 M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20',
  trash:'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.querySelectorAll('[data-icon]').forEach(el => {el.innerHTML = icon(el.dataset.icon);});

const state = {contacts:[], status:{state:'starting'}, selected:null, messages:[], view:'chat', filter:'all',
  drafts:new Map(), sendKeys:new Map(), sending:new Set(), fingerprint:'', stream:null, deleteId:null,
  syncing:false, syncAgain:false, streamConnected:false, olderAvailable:false, mobileList:false};
const listenerLabels = {idle:'未开启监听', starting:'正在建立监听', reconnecting:'正在自动重新连接', listening:'正在监听新消息', background:'后台监听中 · 发送时自动还原', error:'监听已中断'};
const listenerText = c => state.status.paused?'全部运行已暂停':c.listening&&state.status.state!=='ready'?(['starting','hidden'].includes(state.status.state)?'正在自动恢复微信连接':'微信连接未就绪'):listenerLabels[c.listener_state];
const messageLabels = {queued:'等待发送', sending:'正在发送', confirmed:'已核对', uncertain:'待核对', failed:'发送失败', cancelled:'已取消'};
const modeLabels = {manual:'人工聊天',listen_only:'仅监听',ai_assist:'AI 辅助回复',ai_auto:'AI 自动回复'};
const current = () => state.contacts.find(c => c.id === state.selected);
const initials = name => /^[A-Za-z]/.test(name) ? name.slice(0,2).toUpperCase() : name.slice(0,1);
const tone = name => [...name].reduce((sum,c) => sum+c.charCodeAt(0),0)%5;
const failedAvatars = new Set();
const avatarContent = (name, url) => `${escape(initials(name))}${url&&!failedAvatars.has(url)?`<img src="${escape(url)}" alt="${escape(name)}的头像" decoding="async">`:''}`;
const avatar = (name, url, extra='') => `<div class="avatar ${extra}" data-tone="${tone(name)}">${avatarContent(name,url)}</div>`;
document.addEventListener('error',event=>{
  const img=event.target;
  if(img instanceof HTMLImageElement && img.closest('.avatar')){
    failedAvatars.add(img.getAttribute('src'));img.remove();
  }
},true);
function dateText(value, full=false) {
  if (!value) return '—';
  const date = new Date(value);
  return date.toLocaleString('zh-CN',full ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'} : {hour:'2-digit',minute:'2-digit'});
}
let toastTimer;
function toast(message, error=false) {
  $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(() => {$('toast').hidden=true;},error?7500:3500);
}
async function api(path, {method='GET', body, ...options}={}) {
  let response;
  try {response=await fetch(path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-Panel-Request':'1'},body:body===undefined?undefined:JSON.stringify(body),...options});}
  catch {throw new Error('与本机服务的连接中断，请检查服务是否正在运行');}
  if (response.status===204) return null;
  const data=await response.json();
  if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'请检查输入内容后重试');
  return data;
}
function setView(view) {
  state.view=view;
  for (const key of ['chat','contacts','status','agent','ai']) $('view-'+key).hidden=key!==view;
  document.querySelectorAll('.nav-item').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);b.setAttribute('aria-current',b.dataset.view===view?'page':'false');});
  $('page-title').textContent={chat:'聊天工作台',contacts:'联系人管理',status:'运行状态',agent:'Agent 接口',ai:'AI 中心'}[view];
  if(view==='status') renderStatus();
  if(view==='ai') refreshAI().catch(error=>toast(error.message,true));
}
document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
$('connection-button').onclick=$('banner-status').onclick=()=>setView('status');
$('agent-details').onclick=()=>setView('agent');
$('show-details').onclick=()=>$('detail-panel').classList.toggle('open');
$('chat-avatar').onclick=()=>{if(matchMedia('(max-width:760px)').matches){state.mobileList=true;$('view-chat').classList.add('mobile-list');}};
$('chat-avatar').title='窄屏点击返回会话列表';

function renderHeader() {
  const s=state.status, ready=s.state==='ready'&&!s.paused;
  const complete=!s.contacts_total||s.contacts_connected===s.contacts_total;
  $('demo-label').hidden=!s.demo;
  $('connection-button').classList.toggle('ready',ready&&complete);
  $('connection-button').classList.toggle('warning',!ready||!complete);
  $('connection-text').textContent=s.demo?'演示连接':s.paused?'全部已暂停':ready?(complete?'微信已连接':`${s.contacts_errors?'部分会话未连接':'正在连接会话'} ${s.contacts_connected}/${s.contacts_total}`):['starting','hidden'].includes(s.state)?'正在连接微信':'微信未就绪';
  $('pause-text').textContent=s.paused?'恢复运行':'全部暂停';
  $('pause-button').querySelector('[data-icon]').innerHTML=icon(s.paused?'play':'pause');
  $('runtime-banner').hidden=ready;
  $('runtime-detail').textContent=s.paused?'监听与发送已暂停。已经开始的微信操作可能仍会完成。':s.detail||'正在建立本机连接…';
  $('footer-state').textContent=!state.streamConnected?'同步连接正在建立…':`${s.state==='ready'&&!s.paused?state.contacts.filter(c=>c.listening&&['listening','background'].includes(c.listener_state)).length:0} 位联系人监听中 · ${s.queue_size||0} 条待发送`;
  const input=$('message-input'), c=current();
  input.disabled=!c||s.paused||!['ready','starting','hidden'].includes(s.state)||c.mode==='listen_only';
  $('composer-notice').hidden=!input.disabled;
  $('composer-notice').textContent=c?.mode==='listen_only'?'当前为仅监听模式，切换到人工聊天后可回复。':s.paused?'全部运行已暂停，恢复后可以发送。':'微信连接未就绪，草稿会保留在本次会话中。';
  updateSendButton();
}
function renderConversations() {
  const query=$('conversation-search').value.trim().toLowerCase();
  const contacts=state.contacts.filter(c=>(!query||`${c.lookup} ${c.nickname} ${c.wechat_id}`.toLowerCase().includes(query))&&
    (state.filter!=='unread'||c.unread>0)&&(state.filter!=='listening'||c.listening));
  $('contact-count').textContent=state.contacts.length;
  const unread=state.contacts.reduce((n,c)=>n+c.unread,0);
  $('unread-count').textContent=unread?unread:'';
  $('conversation-list').innerHTML=contacts.length?contacts.map(c=>`<button class="conversation-item ${c.id===state.selected?'selected':''}" data-contact="${escape(c.id)}" aria-label="打开 ${escape(c.lookup)} 的聊天">${avatar(c.lookup,c.avatar_url)}<div class="conversation-info"><div class="conversation-top"><span class="conversation-name">${escape(c.lookup)}</span><time class="conversation-time">${c.last_at?escape(dateText(c.last_at)):''}</time></div><div class="conversation-preview">${c.listening?`<i class="tiny-dot ${c.listener_state==='error'?'error':''}"></i>`:''}<p>${escape(c.last_message||listenerLabels[c.listener_state]||'尚无聊天记录')}</p>${c.unread?`<span class="unread-badge">${c.unread>99?'99+':c.unread}</span>`:''}</div></div></button>`).join(''):`<div class="list-empty">${state.contacts.length?'没有符合条件的会话':'尚未添加联系人<br>添加后即可开始聊天和监听'}</div>`;
}
$('conversation-list').onclick=e=>{const button=e.target.closest('[data-contact]');if(button)selectContact(button.dataset.contact);};
$('conversation-search').oninput=renderConversations;
$('sync-avatars').onclick=async()=>{
  const button=$('sync-avatars');button.disabled=true;
  try{await api('/api/contacts/sync-avatars',{method:'POST'});await sync();toast('头像已同步，暂不可读取的头像会保留原显示');}
  catch(error){toast(error.message,true);}
  finally{button.disabled=false;}
};
$('conversation-filters').onclick=e=>{const b=e.target.closest('[data-filter]');if(!b)return;state.filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));renderConversations();};

async function selectContact(id) {
  const previous=current();
  if(previous)state.drafts.set(previous.id,$('message-input').value);
  state.selected=id; state.messages=[]; state.fingerprint=''; state.olderAvailable=false;state.mobileList=false;
  $('message-input').value=state.drafts.get(id)||'';
  $('view-chat').classList.remove('mobile-list');$('detail-panel').classList.remove('open');
  setView('chat');render();
  await loadMessages(id,true);
}
function renderDetails() {
  const c=current();
  $('chat-empty').hidden=!!c;$('chat-content').hidden=!c;
  $('detail-empty').hidden=!!c;$('detail-content').hidden=!c;
  if(!c)return;
  for(const id of ['chat-avatar','profile-avatar']) {
    const element=$(id), key=`${c.lookup}:${c.avatar_url||''}`;
    if(element.dataset.avatarKey!==key){element.innerHTML=avatarContent(c.lookup,c.avatar_url);element.dataset.avatarKey=key;}
    element.dataset.tone=tone(c.lookup);
  }
  $('chat-name').textContent=$('profile-name').textContent=c.lookup;
  $('chat-subtitle').textContent=c.listening?listenerText(c):'已核验联系人 · 监听未开启';
  $('chat-mode').textContent=modeLabels[c.mode]||c.mode;
  $('profile-wechat-id').textContent=c.wechat_id;$('profile-account').textContent=c.account_id;
  $('profile-verified').textContent=dateText(c.verified_at,true);
  $('listen-toggle').setAttribute('aria-checked',!!c.listening);
  $('listener-detail').textContent=listenerText(c);
  $('listener-error').hidden=!c.error;$('listener-error').textContent=c.error;
  $('conversation-mode').value=c.mode;
  $('retry-listener').hidden=c.listener_state!=='error';
  $('demo-controls').hidden=!state.status.demo;
  renderContactAI();
}
function renderMessages(force=false) {
  if(!current())return;
  const fingerprint=JSON.stringify([current().avatar_url,current().self_avatar_url,state.messages.map(m=>[m.id,m.direction,m.kind,m.text,m.historical,m.status,m.error,m.origin,m.media_id])]);
  if(fingerprint===state.fingerprint&&!force)return;
  state.fingerprint=fingerprint;
  const box=$('messages'), nearBottom=box.scrollHeight-box.scrollTop-box.clientHeight<90, oldTop=box.scrollTop;
  let date='';
  box.innerHTML=(state.olderAvailable?'<button class="load-older" id="load-older">加载更早的记录</button>':'')+(state.messages.length?state.messages.map(m=>{
    const day=new Date(m.created_at).toLocaleDateString('zh-CN');
    const divider=day!==date?`<div class="date-divider">${escape(day)}</div>`:'';date=day;
    if(m.direction==='system')return divider+`<div class="system-message">${escape(m.text)}</div>`;
    const outgoing=m.direction==='outgoing';
    const labels=[dateText(m.created_at),m.origin==='ai'?'AI 回复':'',m.historical?'历史同步':'',m.direction==='unknown'?'方向待识别':'',messageLabels[m.status]||''].filter(Boolean);
    const body=m.kind==='image'&&m.media_id?`<a href="/api/ai/media/${encodeURIComponent(m.media_id)}" target="_blank" rel="noopener"><img class="message-photo" src="/api/ai/media/${encodeURIComponent(m.media_id)}" alt="收到的图片" loading="lazy"></a>`:escape(m.text);
    return divider+`<div class="message-row ${escape(m.direction)}" data-message-id="${m.id}">${avatar(outgoing?'我':m.direction==='incoming'?current().lookup:'?',outgoing?current().self_avatar_url:m.direction==='incoming'?current().avatar_url:null)}<div class="message-column"><div class="message-bubble">${body}</div><div class="message-meta ${['uncertain','failed'].includes(m.status)?'error':''}">${labels.map(escape).join(' · ')}</div>${m.error?`<p class="message-error">${escape(m.error)}</p>`:''}</div></div>`;
  }).join(''):'<div class="list-empty">这里还没有消息<br>开启监听后会同步当前窗口可见消息</div>');
  if(force||nearBottom)box.scrollTop=box.scrollHeight;else box.scrollTop=oldTop;
  if($('load-older'))$('load-older').onclick=loadOlder;
}
async function loadMessages(id,force=false) {
  try {
    const messages=await api(`/api/contacts/${id}/messages`);
    if(state.selected!==id)return;
    const oldest=messages[0]?.id??0;
    const merged=new Map(state.messages.filter(m=>m.id<oldest).map(m=>[m.id,m]));messages.forEach(m=>merged.set(m.id,m));
    state.messages=[...merged.values()].sort((a,b)=>a.id-b.id);
    if(!state.messages.length||force)state.olderAvailable=messages.length===100;
    renderMessages(force);
    if(current()?.unread&&document.visibilityState==='visible'&&state.view==='chat')await api(`/api/contacts/${id}/read`,{method:'POST'});
  }catch(error){toast(error.message,true);}
}
async function loadOlder() {
  const id=state.selected, box=$('messages'), height=box.scrollHeight;
  try {
    const older=await api(`/api/contacts/${id}/messages?before=${state.messages[0].id}`);
    if(id!==state.selected)return;
    state.messages=[...older,...state.messages];state.olderAvailable=older.length===100;
    renderMessages(true);box.scrollTop=box.scrollHeight-height;
  }catch(error){toast(error.message,true);}
}
function updateSendButton() {
  $('send-button').disabled=$('message-input').disabled||!$('message-input').value.trim()||state.sending.has(state.selected);
  $('character-count').textContent=$('message-input').value.length?`${$('message-input').value.length} / 1999`:'';
}
$('message-input').oninput=()=>{state.drafts.set(state.selected,$('message-input').value);updateSendButton();};
$('message-input').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();if(!$('send-button').disabled)$('composer').requestSubmit();}};
$('composer').onsubmit=async e=>{
  e.preventDefault();const id=state.selected,text=$('message-input').value;
  if($('send-button').disabled||!id)return;
  const prior=state.sendKeys.get(id),key=prior?.text===text?prior.key:crypto.randomUUID();
  state.sendKeys.set(id,{text,key});state.sending.add(id);updateSendButton();
  try {
    await api(`/api/contacts/${id}/messages`,{method:'POST',body:{text,client_key:key}});
    if(state.drafts.get(id)===text)state.drafts.delete(id);
    if(state.selected===id&&$('message-input').value===text)$('message-input').value='';
    state.sendKeys.delete(id); // A deliberate second identical message gets a new request key.
    await sync();
  }catch(error){toast(error.message,true);}
  finally{state.sending.delete(id);updateSendButton();}
};

let contactPatchQueue=Promise.resolve();
function patchContact(id,changes) {
  contactPatchQueue=contactPatchQueue.then(async()=>{
    try {await api(`/api/contacts/${id}`,{method:'PATCH',body:changes});await sync();}
    catch(error){toast(error.message,true);renderDetails();}
  });
  return contactPatchQueue;
}
$('listen-toggle').onclick=()=>{const c=current();if(c)patchContact(c.id,{listening:!c.listening});};
$('conversation-mode').onchange=()=>{if(current())patchContact(state.selected,{mode:$('conversation-mode').value});};
$('retry-listener').onclick=()=>{if(current())patchContact(state.selected,{listening:true});};
$('pause-button').onclick=async()=>{try{state.status=await api('/api/runtime/pause',{method:'POST',body:{paused:!state.status.paused}});render();}catch(error){toast(error.message,true);}};
$('reconnect').onclick=async()=>{const b=$('reconnect');b.disabled=true;try{state.status=await api('/api/runtime/reconnect',{method:'POST'});render();toast('连接正在重新建立');}catch(error){toast(error.message,true);}finally{b.disabled=false;}};

function openAdd() {$('contact-error').hidden=true;$('contact-dialog').showModal();$('contact-lookup').focus();}
$('add-contact').onclick=$('empty-add').onclick=openAdd;
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('contact-form').onsubmit=async e=>{
  e.preventDefault();const submit=$('contact-submit');if(submit.disabled)return;
  submit.disabled=true;$('contact-progress').hidden=false;$('contact-error').hidden=true;
  try {
    const contact=await api('/api/contacts',{method:'POST',body:{lookup:$('contact-lookup').value,wechat_id:$('contact-wechat').value}});
    if($('contact-auto-listen').checked)await api(`/api/contacts/${contact.id}`,{method:'PATCH',body:{listening:true}});
    $('contact-dialog').close();$('contact-form').reset();await sync();await selectContact(contact.id);toast('联系人身份核验通过，已添加');
  }catch(error){$('contact-error').textContent=error.message;$('contact-error').hidden=false;}
  finally{submit.disabled=false;$('contact-progress').hidden=true;}
};
function renderContacts() {
  const query=$('contact-search').value.trim().toLowerCase();
  const rows=state.contacts.filter(c=>`${c.lookup} ${c.nickname} ${c.wechat_id}`.toLowerCase().includes(query));
  $('contacts-empty').hidden=rows.length>0;
  $('contacts-table').innerHTML=rows.map(c=>`<tr><td><div class="table-person">${avatar(c.lookup,c.avatar_url)}<div><strong>${escape(c.lookup)}</strong><p>${escape(c.nickname)}</p></div></div></td><td>${escape(c.wechat_id)}</td><td><button class="switch" role="switch" aria-label="切换 ${escape(c.lookup)} 的监听" aria-checked="${!!c.listening}" data-toggle="${escape(c.id)}"><span></span></button></td><td><span class="pill">${escape(modeLabels[c.mode]||c.mode)}</span></td><td><div class="table-actions"><button class="text-button" data-open="${escape(c.id)}">进入聊天</button><button class="icon-button" data-delete="${escape(c.id)}" aria-label="移除 ${escape(c.lookup)}">${icon('trash')}</button></div></td></tr>`).join('');
}
$('contact-search').oninput=renderContacts;
$('contacts-table').onclick=e=>{
  const open=e.target.closest('[data-open]'),toggle=e.target.closest('[data-toggle]'),del=e.target.closest('[data-delete]');
  if(open)selectContact(open.dataset.open);
  if(toggle){const c=state.contacts.find(c=>c.id===toggle.dataset.toggle);if(c)patchContact(c.id,{listening:!c.listening});}
  if(del){state.deleteId=del.dataset.delete;const c=state.contacts.find(c=>c.id===state.deleteId);$('delete-description').textContent=`确定从工作台移除“${c.lookup}”吗？微信中的好友关系不受影响。`;$('delete-dialog').showModal();}
};
$('delete-form').onsubmit=async e=>{e.preventDefault();try{await api(`/api/contacts/${state.deleteId}`,{method:'DELETE'});$('delete-dialog').close();await sync();toast('联系人已移除');}catch(error){toast(error.message,true);}};
$('demo-inject').onclick=async()=>{const id=state.selected,text=$('demo-message').value;if(!id||!text.trim())return;try{await api(`/api/demo/${id}/incoming`,{method:'POST',body:{text}});$('demo-message').value='';toast('演示消息已注入');}catch(error){toast(error.message,true);}};

async function renderStatus() {
  const s=state.status;
  $('status-cards').innerHTML=`<article class="status-card"><h3>${icon('chat')}微信连接</h3><strong>${s.demo?'演示环境':s.state==='ready'?'已连接':'尚未就绪'}</strong><p>${escape(s.detail||'正在连接')}<br>${s.version?'版本 '+escape(s.version):'等待读取微信版本'}</p></article><article class="status-card"><h3>${icon('activity')}消息监听</h3><strong>${state.contacts.filter(c=>c.listening&&['listening','background'].includes(c.listener_state)).length} <small>位联系人</small></strong><p>${s.paused?'全部监听已暂停':'仅同步你主动添加的联系人'}<br>${state.contacts.filter(c=>c.listener_state==='error').length} 个监听异常</p></article><article class="status-card"><h3>${icon('send')}发送队列</h3><strong>${s.queue_size||0} <small>条待发送</small></strong><p>${s.busy?'正在执行发送操作':'当前没有发送中的消息'}<br>结果不确定时停止自动重发</p></article>`;
  try {
    const events=await api('/api/activity');
    const names=Object.fromEntries(state.contacts.map(c=>[c.id,c.lookup]));
    const labels={'contact.added':'添加了已核验联系人','contact.updated':'更新了会话设置','contact.avatar':'同步了联系人头像','contact.removed':'移除了联系人','message.updated':'同步了消息状态','runtime.paused':'更新了全局运行状态','listener.gap':'检测到监听历史变化','agent.message':'记录了新入站文本事件'};
    $('activity-list').innerHTML=events.slice(-25).reverse().map(event=>`<div class="activity-row"><span>${icon(event.type==='listener.gap'?'info':'check')}</span><p>${escape(names[event.contact_id]||'工作空间')} · ${escape(labels[event.type]||event.type)}${event.payload.detail?'：'+escape(event.payload.detail):''}</p><time>${escape(dateText(event.created_at,true))}</time></div>`).join('')||'<p class="field-hint">操作记录将在这里显示。</p>';
  }catch(error){toast(error.message,true);}
}
function render() {renderHeader();renderConversations();renderDetails();renderContacts();renderMessages();if(state.view==='status')renderStatus();}
let syncTimer;
function scheduleSync(){clearTimeout(syncTimer);syncTimer=setTimeout(sync,180);}
async function sync() {
  if(state.syncing){state.syncAgain=true;return;}
  state.syncing=true;
  try {
    const data=await api('/api/bootstrap');state.contacts=data.contacts;state.status=data.status;
    if(state.selected&&!current()){state.selected=null;state.messages=[];state.fingerprint='';}
    if(!state.selected&&state.contacts.length){state.selected=state.contacts[0].id;$('message-input').value=state.drafts.get(state.selected)||'';}
    $('view-chat').classList.toggle('mobile-list',state.mobileList||!state.selected);
    render();if(state.selected)await loadMessages(state.selected);
    if(!state.stream)connectStream(data.cursor);
  }catch(error){$('runtime-banner').hidden=false;$('runtime-detail').textContent=error.message;}
  finally{state.syncing=false;if(state.syncAgain){state.syncAgain=false;scheduleSync();}}
}
function connectStream(cursor) {
  const stream=new EventSource(`/api/events?after=${cursor}`);state.stream=stream;
  stream.onopen=()=>{state.streamConnected=true;renderHeader();scheduleSync();};
  stream.addEventListener('change',event=>{
    const events=JSON.parse(event.data);
    const removed=new Set(events.filter(e=>e.type==='message.removed').map(e=>e.payload.message_id));
    if(removed.size)state.messages=state.messages.filter(m=>!removed.has(m.id));
    if(events.some(e=>e.type.startsWith('ai.')||e.type==='contact.updated'))scheduleAIRefresh();
    scheduleSync();
  });
  stream.addEventListener('status',e=>{state.status=JSON.parse(e.data);renderHeader();renderDetails();});
  stream.onerror=()=>{state.streamConnected=false;renderHeader();};
}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')sync();});
window.addEventListener('beforeunload',()=>state.stream?.close());
setInterval(()=>{if(document.visibilityState==='visible')sync();},15000);
initAI({api,escape,toast,state,current,patchContact,setView});
sync();
