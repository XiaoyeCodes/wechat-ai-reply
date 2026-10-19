import {voiceCatalog,onVoiceCatalog,openVoiceSettings} from './voice.js?v=voice-settings-3';

export function agentVoiceFields(voice={},esc){
 const format=voice.reply_format||'text',preset=voice.preset||'xiaoxiao',model=voice.model||'speech-2.8-turbo';
 return `<details class="ai-editor-section" id="agent-voice-section"><summary><strong>语音回复</strong><span>默认回复方式、音色与试听</span></summary><div class="ai-editor-section-body">
 <label class="ai-field"><span>默认回复方式</span><select name="cp.voice.reply_format" aria-label="默认回复方式"><option value="text" ${format==='text'?'selected':''}>文字回复</option><option value="voice" ${format==='voice'?'selected':''}>语音回复</option></select></label>
 <p class="field-hint">联系人选择“跟随 Agent”后，使用这里的回复方式和声音；联系人单独选择的设置优先。</p>
 <section class="voice-studio agent-voice-studio" aria-label="Agent 声音与试听"><div class="voice-studio-heading"><h4>声音与试听</h4><span>Agent 默认音色</span></div>
 <label class="ai-field"><span>回复音色</span><select name="cp.voice.preset" aria-label="回复音色"><option value="${esc(preset)}">正在读取音色…</option></select></label>
 <p class="field-hint" id="agent-voice-description"></p>
 <div id="agent-voice-model-row"><label class="ai-field"><span>MiniMax 合成模型</span><select name="cp.voice.model" aria-label="MiniMax 合成模型"><option value="speech-2.8-turbo" ${model==='speech-2.8-turbo'?'selected':''}>Turbo · 更快、更省</option><option value="speech-2.8-hd" ${model==='speech-2.8-hd'?'selected':''}>HD · 高清音质</option></select></label><p class="field-hint">MiniMax 试听与回复按实际合成量计费。</p></div>
 <button type="button" class="button secondary voice-manage-button" id="agent-minimax-open">MiniMax 设置与声音克隆</button>
 <details class="voice-sample-edit"><summary>调整试听文本</summary><label class="ai-field"><span>试听文本</span><textarea id="agent-voice-sample" aria-label="试听文本" rows="3" maxlength="120">晚上好呀，今天过得怎么样？我在这里陪你慢慢聊。</textarea></label></details>
 <button type="button" class="button secondary voice-preview-button" id="agent-voice-preview" disabled>▷ 试听音色</button>
 <audio id="agent-voice-audio" controls preload="none" hidden></audio>
 <p class="field-hint" id="agent-voice-status" role="status" aria-live="polite">试听只在浏览器播放。音色和模型点击“保存配置”后生效。</p>
 <p class="voice-network-note" id="agent-voice-network-note"></p>
 </section></div></details>`;
}

export function bindAgentVoice(form){
 if(form.elements.namedItem('kind').value!=='workflow')return ()=>{};
 const root=form.querySelector('#agent-voice-section'),get=id=>root.querySelector('#'+id),field=key=>form.elements.namedItem('cp.voice.'+key);
 const preset=field('preset'),model=field('model'),button=get('agent-voice-preview'),audio=get('agent-voice-audio'),status=get('agent-voice-status');
 let catalog=null,controller=null,url='',version=0,disposed=false;
 const active=()=>!disposed&&form.isConnected&&document.getElementById('ai-editor-form')===form;
 const stop=()=>{
   version++;controller?.abort();controller=null;audio.pause();audio.removeAttribute('src');audio.load();audio.hidden=true;
   if(url)URL.revokeObjectURL(url);url='';button.textContent='▷ 试听音色';
   status.textContent='试听只在浏览器播放。音色和模型点击“保存配置”后生效。';
 };
 const render=()=>{
   const voice=catalog?.voices.find(v=>v.id===preset.value),mini=preset.value.startsWith('minimax:');
   get('agent-voice-model-row').hidden=!mini;
   get('agent-voice-description').textContent=voice?.description||'请选择可用音色，或在 MiniMax 设置中同步专属声音。';
   get('agent-voice-network-note').textContent=mini?'回复与试听文字会提交给 MiniMax 合成；长文、链接或合成失败时保留文字。':'在线音色通过微软合成；长文、链接或合成失败时保留文字。';
   button.disabled=!!controller||!voice||(mini&&!catalog?.minimax.key_configured);
   if(mini&&!catalog?.minimax.key_configured)status.textContent='请先在“MiniMax 设置与声音克隆”中保存 API Key，再试听。';
 };
 const update=data=>{
   if(!active())return;catalog=data;const value=preset.value;
   const choices=data.voices.filter(v=>v.selectable!==false);
   const groups=[['MiniMax 专属音色',v=>v.engine==='minimax'&&v.kind!=='system'],['在线音色',v=>v.engine!=='minimax'],['MiniMax 预设音色',v=>v.engine==='minimax'&&v.kind==='system']];
   preset.replaceChildren(...groups.map(([name,filter])=>{
     const group=document.createElement('optgroup');group.label=name;
     group.append(...choices.filter(filter).map(v=>new Option(v.name,v.id)));return group;
   }));
   if(!choices.some(v=>v.id===value)){
     const option=new Option('原音色暂不可用，请同步或重新选择',value);option.disabled=true;option.hidden=true;preset.add(option);
   }
   preset.value=value;render();
 };
 const change=()=>{stop();render();};
 preset.addEventListener('change',change);model.addEventListener('change',change);field('reply_format').addEventListener('change',change);
 get('agent-voice-sample').oninput=()=>{stop();render();};
 button.onclick=async()=>{
   if(button.disabled||!active())return;
   const text=get('agent-voice-sample').value.trim();if(!text){status.textContent='请先填写试听文本。';return;}
   stop();const request=version;controller=new AbortController();render();button.textContent='正在生成…';status.textContent='正在生成试听…';
   const body={voice_id:preset.value,text};if(body.voice_id.startsWith('minimax:'))body.model=model.value;
   try{
     const response=await fetch('/api/voice/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Panel-Request':'1'},body:JSON.stringify(body),signal:controller.signal});
     if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:'试听生成失败，请稍后重试。');}
     const blob=await response.blob();if(!active()||request!==version)return;
     url=URL.createObjectURL(blob);audio.src=url;audio.hidden=false;status.textContent='试听已准备好，仅在浏览器播放。';
     try{await audio.play();}catch{status.textContent='试听已准备好，点击播放器播放。';}
   }catch(error){if(active()&&request===version&&error.name!=='AbortError')status.textContent=error.message;}
   finally{if(active()&&request===version){controller=null;render();button.textContent='▷ 试听音色';}}
 };
 const id='agent-voice:'+crypto.randomUUID();
 get('agent-minimax-open').onclick=()=>{
   stop();render();return openVoiceSettings({
     current:()=>active()?{id,lookup:form.elements.namedItem('name').value,voice_preset:preset.value,voice_model:model.value}:null,
     save:async changes=>{
       if(!active())throw Error('Agent 编辑页已关闭，请重新打开。');
       if(changes.voice_preset)preset.value=changes.voice_preset;
       if(changes.voice_model)model.value=changes.voice_model;
       change();form.dispatchEvent(new Event('change',{bubbles:true}));
     }
   });
 };
 const unsubscribe=onVoiceCatalog(update);
 voiceCatalog().then(update).catch(()=>{if(active())status.textContent='音色列表暂不可用，请重新打开设置或同步 MiniMax 音色。';});
 render();
 return ()=>{disposed=true;unsubscribe();stop();};
}
