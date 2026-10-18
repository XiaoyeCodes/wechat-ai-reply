const $=id=>document.getElementById(id);
let ctx,voices=[],contextKey='',audioURL='',controller=null,requestVersion=0,minimax={};
let sourceURL='',cloneRequest='',cloning=false,settingsBusy=false,pendingChanges=0;
let settingsDraft=null,saveError='',savedNotice='',contactSave=Promise.resolve();

function settingsDirty(){
  const c=ctx?.current();
  return !!$('minimax-key').value.trim()||!!(settingsDraft&&c?.id===settingsDraft.contactId&&
    (settingsDraft.voice!==c.voice_preset||settingsDraft.model!==(c.voice_model||'speech-2.8-turbo')));
}
function settingsTab(name){
  for(const tab of ['voices','setup']){
    const active=tab===name,button=$('minimax-tab-'+tab);
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    $('minimax-panel-'+tab).hidden=!active;
  }
  $('minimax-dialog').querySelector('.voice-dialog-body').scrollTop=0;
}
function changedSettings(){saveError='';savedNotice='';availability();}

function minimaxSelected(){return $('voice-preset').value.startsWith('minimax:');}
function availability(){
  $('voice-preview').disabled=!!controller||pendingChanges>0||settingsBusy||!voices.length||(minimaxSelected()&&!minimax.key_configured);
  $('minimax-key-status').textContent=minimax.key_configured?'Key 已保存':'尚未配置 Key';
  $('minimax-refresh').disabled=!minimax.key_configured||settingsBusy||cloning;
  $('minimax-clear').disabled=!minimax.key_configured||settingsBusy||cloning;
  const wrongContact=!!settingsDraft&&settingsDraft.contactId!==(ctx.current()?.id||null);
  $('minimax-save').disabled=settingsBusy||cloning||pendingChanges>0||wrongContact;
  $('minimax-save').textContent=settingsBusy?'正在保存…':'保存设置';
  $('minimax-close').disabled=settingsBusy||cloning;
  $('minimax-done').disabled=settingsBusy||cloning;
  $('minimax-done').textContent=settingsDirty()?'放弃更改':'关闭';
  $('minimax-key').disabled=settingsBusy||cloning;
  $('minimax-preview-model').disabled=settingsBusy||cloning;
  const saveState=$('minimax-save-state');
  saveState.textContent=wrongContact?'当前联系人已变化，请重新打开设置':saveError||
    (settingsBusy?'正在保存设置…':cloning?'正在创建音色…':settingsDirty()?'有未保存的修改':savedNotice||'设置已保存');
  saveState.dataset.error=!!saveError||wrongContact;
  $('minimax-save-hint').textContent=$('minimax-clone-file').files.length?
    '录音需单独点击“上传并创建音色”，保存不会上传录音。':
    settingsDraft?.contactId?'保存连接、所选音色与合成模型。':'保存 MiniMax 连接；选择联系人后可保存音色。';
  $('minimax-clone-submit').disabled=!minimax.key_configured||cloning||settingsBusy;
  $('minimax-clone-file').disabled=cloning;$('minimax-clone-name').disabled=cloning;
  for(const button of $('minimax-my-voices').querySelectorAll('[data-preview-voice]'))
    button.disabled=!!controller||pendingChanges>0||settingsBusy||cloning||!minimax.key_configured;
  for(const button of $('minimax-my-voices').querySelectorAll('[data-use-voice]')){
    const selected=settingsDraft?.voice===button.dataset.useVoice;
    button.disabled=!settingsDraft?.contactId||wrongContact||selected||pendingChanges>0||settingsBusy||cloning||!minimax.key_configured;
    button.textContent=selected?'已选择':settingsDraft?.contactId?'选择音色':'先选择聊天联系人';
    button.setAttribute('aria-pressed',String(selected));button.closest('article').dataset.selected=selected;
  }
}

async function loadVoices(){
  const data=await ctx.api('/api/voice/voices');voices=data.voices;minimax=data.minimax||{};
  const choices=voices.filter(v=>v.selectable!==false);
  const groups=[['MiniMax 专属音色',v=>v.engine==='minimax'&&v.kind!=='system'],['在线音色',v=>v.engine!=='minimax'],['MiniMax 预设音色',v=>v.engine==='minimax'&&v.kind==='system']];
  $('voice-preset').replaceChildren(...groups.map(([label,filter])=>{
    const group=document.createElement('optgroup');group.label=label;
    group.append(...choices.filter(filter).map(v=>new Option(v.name,v.id)));return group;
  }));
  $('voice-preset').disabled=false;
  if(!$('voice-sample').value)$('voice-sample').value=data.sample_text;
  if(!$('minimax-preview-text').value)$('minimax-preview-text').value=data.sample_text;
  renderMyVoices();
  renderVoice(ctx.current());availability();
}

function renderMyVoices(){
  const custom=voices.filter(v=>v.engine==='minimax'&&v.kind!=='system'),list=$('minimax-my-voices');
  list.replaceChildren();$('minimax-voice-count').textContent=String(custom.length);
  $('minimax-voices-empty').hidden=custom.length>0;
  $('minimax-preview-options').hidden=!custom.length;
  for(const voice of custom){
    const card=document.createElement('article');card.className='minimax-voice-card';
    const title=document.createElement('h4');title.textContent=voice.name;
    const note=document.createElement('p');note.textContent=voice.kind==='clone'?'已创建的克隆音色':'账号中的设计音色';
    const actions=document.createElement('div');actions.className='voice-config-actions';
    const preview=document.createElement('button');preview.type='button';preview.className='button secondary';preview.dataset.previewVoice=voice.id;preview.textContent='试听这个音色';
    const use=document.createElement('button');use.type='button';use.className='button primary';use.dataset.useVoice=voice.id;
    actions.append(preview,use);card.append(title,note,actions);list.append(card);
  }
}

function stopPreview(){
  requestVersion++;controller?.abort();controller=null;
  for(const id of ['voice-preview-audio','minimax-preview-audio']){
    const audio=$(id);audio.pause();audio.removeAttribute('src');audio.load();audio.hidden=true;
  }
  if(audioURL)URL.revokeObjectURL(audioURL);audioURL='';
  availability();$('voice-preview-label').textContent='试听音色';
  $('voice-preview-status').textContent='试听只在浏览器播放，不会发送到微信。';
  $('minimax-preview-status').textContent='试听只播放声音，不更改联系人的音色或发送微信。';
}

async function previewVoice(voice_id,model,inDialog=false){
  const text=$(inDialog?'minimax-preview-text':'voice-sample').value.trim();
  if(!text){ctx.toast('先写一句想试听的话',true);return;}
  const body={voice_id,text};if(voice_id.startsWith('minimax:'))body.model=model;
  stopPreview();const version=requestVersion;
  controller=new AbortController();availability();
  const status=$(inDialog?'minimax-preview-status':'voice-preview-status');
  if(!inDialog)$('voice-preview-label').textContent='正在生成…';
  status.textContent='正在生成「'+(voices.find(v=>v.id===voice_id)?.name||'所选音色')+'」的试听…';
  try{
    const res=await fetch('/api/voice/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Panel-Request':'1'},body:JSON.stringify(body),signal:controller.signal});
    if(!res.ok){const data=await res.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:'试听生成失败，请稍后再试');}
    const blob=await res.blob();if(version!==requestVersion)return;
    audioURL=URL.createObjectURL(blob);const audio=$(inDialog?'minimax-preview-audio':'voice-preview-audio');
    audio.src=audioURL;audio.hidden=false;
    status.textContent='试听已准备好。'+(inDialog?'选择喜欢的音色后，点击底部“保存设置”。':'更换音色会自动保存到当前联系人。');
    try{await audio.play();}catch{if(version===requestVersion)status.textContent='试听已准备好，点击播放器播放。';}
  }catch(error){if(error.name!=='AbortError'&&version===requestVersion)status.textContent=error.message;}
  finally{if(version===requestVersion){controller=null;availability();$('voice-preview-label').textContent='试听音色';}}
}

export function renderVoice(contact){
  $('voice-studio').hidden=contact?.reply_format!=='voice';
  const key=contact?[contact.id,contact.voice_preset||'windows-huihui',contact.reply_format||'text',contact.voice_model||''].join(':'):'';
  if(key!==contextKey){stopPreview();contextKey=key;}
  for(const option of $('voice-preset').querySelectorAll('[data-current-voice]'))option.remove();
  if(!contact)return;
  const id=contact.voice_preset||'windows-huihui';
  const voice=voices.find(v=>v.id===id);
  if(voices.length&&!Array.from($('voice-preset').options).some(o=>o.value===id)){
    const option=new Option(voice?'当前：'+voice.name+'（已从候选移除）':'原音色暂不可用，请同步或重新选择',id);
    option.disabled=true;option.hidden=true;option.dataset.currentVoice='true';
    $('voice-preset').add(option);
  }
  $('voice-preset').value=id;
  $('voice-model').value=contact.voice_model||'speech-2.8-turbo';
  $('voice-model-row').hidden=!id.startsWith('minimax:');
  $('voice-description').textContent=voice?.description||'正在读取音色列表…';
  const hd=$('voice-model').value.endsWith('-hd');
  $('voice-cost').textContent=(hd?'HD：约 0.07 元／100 汉字。':'Turbo：约 0.04 元／100 汉字。')+'试听按量收费，选择自动保存。';
  $('voice-network-note').textContent=id.startsWith('minimax:')?
    (minimax.key_configured?'回复或试听文字将提交给 MiniMax 合成；合成不可用时改发文字。':'先点击“MiniMax 设置与声音克隆”保存 Key，才能试听和生成语音。'):
    voice?.engine==='windows'?'本机离线合成，不上传回复文字。':'在线音色会将本条回复文字提交给微软合成；不可用时改发文字。';
  if(!controller)availability();
}

export async function openVoiceSettings(){
  await contactSave;
  const contact=ctx.current();
  settingsDraft={contactId:contact?.id||null,voice:contact?.voice_preset||'windows-huihui',model:contact?.voice_model||'speech-2.8-turbo'};
  saveError='';savedNotice='';
  stopPreview();$('minimax-key').value='';$('minimax-dialog').showModal();
  $('minimax-settings-context').textContent=contact?'为 '+contact.lookup+' 选择声音，保存后用于微信语音回复。':'管理 MiniMax 连接与专属音色。选择联系人后可设置回复声音。';
  settingsTab(voices.some(v=>v.engine==='minimax'&&v.kind!=='system')?'voices':'setup');
  $('minimax-preview-model').value=ctx.current()?.voice_model||'speech-2.8-turbo';
  $('minimax-preview-text').value=$('voice-sample').value;
  try{await loadVoices();}
  catch(e){$('minimax-config-status').textContent=e.message;$('minimax-config-status').dataset.error=true;}
}

export function initVoice(context){
  ctx=context;
  async function saveVoice(changes){
    const c=ctx.current();if(!c)return;
    pendingChanges++;stopPreview();
    try{await ctx.patchContact(c.id,changes);}
    finally{pendingChanges--;renderVoice(ctx.current());availability();}
  };
  $('voice-preset').onchange=()=>{contactSave=saveVoice({voice_preset:$('voice-preset').value});};
  $('voice-model').onchange=()=>{contactSave=saveVoice({voice_model:$('voice-model').value});};
  $('voice-sample').oninput=stopPreview;
  $('voice-preview').onclick=()=>{
    if(!ctx.current()||$('voice-preview').disabled)return;
    return previewVoice($('voice-preset').value,$('voice-model').value);
  };
  $('minimax-preview-model').onchange=()=>{if(settingsDraft)settingsDraft.model=$('minimax-preview-model').value;stopPreview();changedSettings();};$('minimax-preview-text').oninput=stopPreview;
  $('minimax-key').oninput=changedSettings;
  for(const name of ['voices','setup']){
    const button=$('minimax-tab-'+name);
    button.onclick=()=>settingsTab(name);
    button.onkeydown=e=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
      e.preventDefault();const target=e.key==='Home'?'voices':e.key==='End'?'setup':name==='voices'?'setup':'voices';
      settingsTab(target);$('minimax-tab-'+target).focus();
    };
  }
  $('minimax-my-voices').onclick=async e=>{
    const button=e.target.closest('button');if(!button||button.disabled)return;
    if(button.dataset.previewVoice)return previewVoice(button.dataset.previewVoice,$('minimax-preview-model').value,true);
    if(button.dataset.useVoice){
      if(!settingsDraft||settingsDraft.contactId!==ctx.current()?.id)return;
      settingsDraft.voice=button.dataset.useVoice;changedSettings();
      $('minimax-preview-status').textContent='已选择此音色，点击底部“保存设置”后生效。';
    }
  };
  const status=(id,text,error=false)=>{$(id).textContent=text;$(id).dataset.error=error;};
  $('minimax-open').onclick=openVoiceSettings;
  const close=(discard=false)=>{
    if(settingsBusy||cloning)return;
    if(settingsDirty()&&!discard){saveError='还有未保存的修改，请保存或点击“放弃更改”。';availability();return;}
    $('minimax-dialog').close();
  };
  $('minimax-close').onclick=()=>close();$('minimax-done').onclick=()=>close(true);
  $('minimax-dialog').addEventListener('cancel',e=>{e.preventDefault();close();});
  $('minimax-dialog').addEventListener('close',()=>{settingsDraft=null;saveError='';savedNotice='';stopPreview();$('minimax-key').value='';$('minimax-source-audio').pause();});
  async function configAction(action,message){
    if(settingsBusy||cloning)return;
    settingsBusy=true;stopPreview();availability();status('minimax-config-status','正在处理…');
    try{await action();await loadVoices();status('minimax-config-status',message);}
    catch(error){status('minimax-config-status',error.message,true);}
    finally{settingsBusy=false;availability();}
  }
  async function saveSettings(){
    if(settingsBusy||cloning||!settingsDraft||settingsDraft.contactId!==(ctx.current()?.id||null))return;
    const draft={...settingsDraft},key=$('minimax-key').value.trim();let keySaved=false;
    settingsBusy=true;saveError='';savedNotice='';stopPreview();availability();
    try{
      if(key){
        minimax=await ctx.api('/api/voice/minimax/config',{method:'PUT',body:{api_key:key}});
        keySaved=true;$('minimax-key').value='';
      }
      const contact=ctx.current();
      if(draft.contactId){
        if(contact?.id!==draft.contactId)throw Error('联系人已变化，请重新打开设置');
        const changes={};
        if(draft.voice!==contact.voice_preset)changes.voice_preset=draft.voice;
        if(draft.model!==(contact.voice_model||'speech-2.8-turbo'))changes.voice_model=draft.model;
        if(Object.keys(changes).length){
          await ctx.patchContact(draft.contactId,changes);
          if(ctx.current()?.id!==draft.contactId||Object.entries(changes).some(([k,v])=>ctx.current()?.[k]!==v))
            throw Error('联系人音色设置未保存，请重试');
        }
      }
      savedNotice='全部设置已保存';
      status('minimax-config-status',keySaved?'Key 已加密保存。':'连接配置保持不变。');
      try{await loadVoices();}catch{savedNotice='设置已保存，音色列表刷新失败，请重新打开';}
    }catch(error){saveError=(keySaved?'Key 已保存；其余设置保存失败：':'保存失败：')+error.message;}
    finally{settingsBusy=false;availability();}
  }
  $('minimax-save').onclick=saveSettings;
  $('minimax-key-form').onsubmit=e=>{e.preventDefault();saveSettings();};
  $('minimax-clear').onclick=()=>configAction(async()=>{await ctx.api('/api/voice/minimax/config',{method:'PUT',body:{clear_secret:true}});$('minimax-key').value='';},'Key 已清除。MiniMax 暂不可用，可以选择其他音色。');
  $('minimax-refresh').onclick=()=>configAction(()=>ctx.api('/api/voice/minimax/refresh',{method:'POST'}),'连接成功，账号下的音色已同步到列表。');
  $('minimax-clone-file').onchange=()=>{
    cloneRequest=crypto.randomUUID();const file=$('minimax-clone-file').files[0];
    $('minimax-file-name').textContent=file?file.name:'添加参考录音';
    $('minimax-file-meta').textContent=file?(file.size/1024/1024).toFixed(2)+' MB · 点击右侧可重新选择':'WAV / MP3 / M4A · 10 秒–5 分钟 · 最大 20 MB';
    $('minimax-file-action').textContent=file?'更换文件':'选择文件';
    const audio=$('minimax-source-audio');audio.pause();audio.removeAttribute('src');audio.load();audio.hidden=true;
    if(sourceURL)URL.revokeObjectURL(sourceURL);sourceURL='';
    if(file){sourceURL=URL.createObjectURL(file);audio.src=sourceURL;audio.hidden=false;}
    status('minimax-clone-status',file?'可先播放原录音，确认后点击“上传并创建音色”。':'创建成功后，会显示在本窗口的“我的专属音色”。');
    availability();
  };
  $('minimax-clone-name').oninput=()=>{cloneRequest=crypto.randomUUID();};
  $('minimax-clone-form').onsubmit=async e=>{
    e.preventDefault();if(cloning||settingsBusy)return;
    const file=$('minimax-clone-file').files[0],name=$('minimax-clone-name').value.trim();
    if(!file||!name)return;
    const format=file.name.split('.').pop().toLowerCase();
    if(!['wav','mp3','m4a'].includes(format)||file.size>20000000){status('minimax-clone-status','请选择不超过 20 MB 的 WAV、MP3 或 M4A 文件',true);return;}
    cloning=true;availability();$('minimax-clone-submit').textContent='正在上传并创建…';
    status('minimax-clone-status','正在验证样音并创建音色，可能需要一两分钟。不会自动发送微信。');
    cloneRequest=cloneRequest||crypto.randomUUID();
    try{
      const query=new URLSearchParams({name,format});
      const res=await fetch('/api/voice/minimax/clones/'+cloneRequest+'?'+query,{method:'POST',headers:{'X-Panel-Request':'1','Content-Type':'application/octet-stream'},body:file});
      const data=await res.json().catch(()=>({}));
      if(!res.ok)throw Error(typeof data.detail==='string'?data.detail:'创建失败，请核对服务状态');
      $('minimax-clone-file').value='';$('minimax-clone-file').onchange();
      status('minimax-clone-status','「'+data.name+'」已创建，正在更新“我的专属音色”…');
      try{
        await loadVoices();status('minimax-clone-status','「'+data.name+'」已创建，在上方“我的专属音色”中试听或选用。');
        settingsTab('voices');
      }catch{status('minimax-clone-status','「'+data.name+'」已创建，但列表刷新失败。请重新打开本窗口查看，无需再次上传。',true);}
    }catch(error){status('minimax-clone-status',error.message,true);}
    finally{cloning=false;$('minimax-clone-submit').textContent='上传并创建音色';availability();}
  };
  loadVoices().catch(()=>{$('voice-description').textContent='音色列表暂不可用，请刷新重试。';});
  window.addEventListener('beforeunload',()=>{stopPreview();if(sourceURL)URL.revokeObjectURL(sourceURL);});
}
