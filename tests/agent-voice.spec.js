const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'},preset='minimax:Chinese (Mandarin)_ExplorativeGirl';

test('Agent 语音：试听、取消与保存、MiniMax 草稿及联系人跟随和覆盖',async({page,request})=>{
 const resources=[],contacts=[],errors=[],previews=[];
 const create=async(kind,data)=>{
  const res=await request.post('/api/ai/config/'+kind,{headers,data});expect(res.ok(),await res.text()).toBeTruthy();
  const value=await res.json();resources.push([kind,value.id]);return value;
 };
 const read=async id=>(await request.get('/api/ai/config/agents/'+id)).json();
 const open=async id=>{
  await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
  await page.locator(`.text-button[data-edit="${id}"]`).click();await page.locator('#agent-tab-behavior').click();
  await page.locator('#agent-voice-section > summary').click();
 };
 const wav=Buffer.alloc(44+24000*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/voice/voices',async route=>{
  const response=await route.fetch(),body=await response.json();
  body.voices.push({id:'minimax:FixtureClone',name:'测试专属声音',engine:'minimax',kind:'clone',description:'仅用于设置界面测试'});
  await route.fulfill({response,json:body});
 });
 await page.route('**/api/voice/preview',async route=>{previews.push(route.request().postDataJSON());await route.fulfill({contentType:'audio/wav',body:wav});});
 try{
  await request.put('/api/voice/minimax/config',{headers,data:{api_key:'agent-voice-ui-fixture'}});
  const provider=await create('providers',{name:'Agent 声音测试模型',model:'fixture',base_url:'http://127.0.0.1:19999/v1'});
  const a=await create('agents',{name:'阿慧语音配置测试',provider_id:provider.id});
  const b=await create('agents',{name:'另一位独立 Agent',provider_id:provider.id});
  for(const lookup of ['跟随声音用户','独立声音用户']){
   const c=await (await request.post('/api/contacts',{headers,data:{lookup,wechat_id:lookup}})).json();contacts.push(c.id);
   await request.patch('/api/contacts/'+c.id,{headers,data:{ai_agent_id:a.id,reply_format:lookup==='跟随声音用户'?'agent':'voice',voice_preset:'xiaoyi'}});
  }
  await open(a.id);const section=page.locator('#agent-voice-section');
  await section.getByLabel('默认回复方式',{exact:true}).selectOption('voice');
  await expect(section.getByLabel('回复音色',{exact:true}).locator('option[value="'+preset+'"]')).toHaveCount(1);
  await section.getByLabel('回复音色',{exact:true}).selectOption(preset);
  await section.getByLabel('MiniMax 合成模型',{exact:true}).selectOption('speech-2.8-hd');
  await section.getByRole('button',{name:/试听音色/}).click();
  await expect(page.locator('#agent-voice-audio')).toBeVisible();
  await expect.poll(()=>page.locator('#agent-voice-audio').evaluate(el=>el.readyState)).toBeGreaterThanOrEqual(2);
  expect(previews.at(-1)).toMatchObject({voice_id:preset,model:'speech-2.8-hd'});
  expect((await read(a.id)).companion.voice.reply_format).toBe('text');
  for(const id of contacts)expect((await (await request.get('/api/contacts/'+id+'/messages')).json())).toHaveLength(0);
  await page.locator('#ai-editor-cancel').click();
  expect((await read(a.id)).companion.voice).toEqual({reply_format:'text',preset:'xiaoxiao',model:'speech-2.8-turbo'});
  await open(a.id);await section.getByLabel('默认回复方式',{exact:true}).selectOption('voice');
  await section.getByLabel('回复音色',{exact:true}).selectOption(preset);
  await section.getByRole('button',{name:'MiniMax 设置与声音克隆',exact:true}).click();
  await expect(page.locator('#minimax-settings-context')).toContainText(a.name);
  await page.locator('#minimax-tab-voices').click();
  await page.getByText('试听模型与文本',{exact:true}).click();
  await page.locator('#minimax-preview-model').selectOption('speech-2.8-hd');
  await page.locator('#minimax-save').click();
  await expect(page.locator('#minimax-save-state')).toHaveText('全部设置已保存');
  expect((await read(a.id)).companion.voice.model).toBe('speech-2.8-turbo');
  await page.locator('#minimax-done').click();
  await expect(section.getByLabel('MiniMax 合成模型',{exact:true})).toHaveValue('speech-2.8-hd');
  await page.locator('#ai-editor-fields').evaluate(el=>el.scrollTop=0);
  await page.locator('#ai-editor').screenshot({path:test.info().outputPath('agent-voice-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  expect(await page.locator('#ai-editor').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  await expect(page.locator('#ai-save')).toBeInViewport();
  await section.screenshot({path:test.info().outputPath('agent-voice-mobile.png')});
  await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
  expect((await read(a.id)).companion.voice).toEqual({reply_format:'voice',preset,model:'speech-2.8-hd'});
  expect((await read(b.id)).companion.voice.reply_format).toBe('text');
  const list=await (await request.get('/api/contacts')).json();
  expect(list.find(c=>c.id===contacts[0]).voice_settings).toEqual({reply_format:'voice',voice_preset:preset,voice_model:'speech-2.8-hd'});
  expect(list.find(c=>c.id===contacts[1]).voice_settings.voice_preset).toBe('xiaoyi');
  await page.setViewportSize({width:1440,height:960});await page.reload();
  await page.getByRole('button',{name:'打开 跟随声音用户 的聊天',exact:true}).click();
  await expect(page.locator('#reply-format')).toHaveValue('agent');
  await expect(page.locator('#voice-preset')).toHaveValue(preset);
  await expect(page.locator('#voice-model')).toHaveValue('speech-2.8-hd');
  await page.locator('#voice-preset').selectOption('xiaoxiao');
  await expect(page.locator('#reply-format')).toHaveValue('voice');
  await expect.poll(async()=> (await (await request.get('/api/contacts')).json()).find(c=>c.id===contacts[0]).voice_preset).toBe('xiaoxiao');
  expect(errors).toEqual([]);
 }finally{
  for(const id of contacts)await request.delete('/api/contacts/'+id,{headers});
  for(const [kind,id] of resources.reverse())await request.delete('/api/ai/config/'+kind+'/'+id,{headers});
  await request.put('/api/voice/minimax/config',{headers,data:{clear_secret:true}});
 }
});
