const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

test('音色选择自动保存，试听可播放且不发送微信，切换联系人停止旧试听',async({page,request})=>{
  const create=async(name)=>(await request.post('/api/contacts',{headers,data:{lookup:name,wechat_id:name}})).json();
  const a=await create('音色甲'),b=await create('音色乙');
  const wav=Buffer.alloc(44+24000*2*2);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  const previews=[];const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/voice/preview',async route=>{previews.push(route.request().postDataJSON());await route.fulfill({contentType:'audio/wav',body:wav});});
  await page.goto('/');await page.getByRole('button',{name:'打开 音色甲 的聊天',exact:true}).click();
  const studio=page.locator('#voice-studio'),format=page.locator('#reply-format');
  await expect(studio).toBeHidden();
  await format.selectOption('voice');await expect(studio).toBeVisible();
  const select=page.locator('#voice-preset');await expect(select).toBeEnabled();
  await select.selectOption('xiaoxiao');
  await expect.poll(async()=>(await (await request.get('/api/contacts')).json()).find(c=>c.id===a.id).voice_preset).toBe('xiaoxiao');
  await page.getByRole('button',{name:'试听音色',exact:true}).click();
  const audio=page.locator('#voice-preview-audio');await expect(audio).toBeVisible();
  await expect.poll(()=>audio.evaluate(el=>el.readyState)).toBeGreaterThanOrEqual(2);
  expect(previews[0].voice_id).toBe('xiaoxiao');
  expect((await (await request.get(`/api/contacts/${a.id}/messages`)).json()).length).toBe(0);
  await page.locator('.voice-studio').screenshot({path:test.info().outputPath('voice-chooser.png')});
  await page.getByRole('button',{name:'打开 音色乙 的聊天',exact:true}).click();
  await expect(studio).toBeHidden();
  await expect(audio).toBeHidden();await expect(select).toHaveValue('windows-huihui');
  await expect(select.locator('option[value="windows-huihui"]')).toBeDisabled();
  await expect(select.locator('option[value="windows-huihui"]')).toHaveAttribute('hidden','');
  await page.getByRole('button',{name:'打开 音色甲 的聊天',exact:true}).click();
  await expect(studio).toBeVisible();
  await expect(select).toHaveValue('xiaoxiao');
  await expect(select.locator('[data-current-voice]')).toHaveCount(0);
  await select.selectOption('xiaoyi');
  await page.getByText('调整试听文本',{exact:true}).click();await page.getByLabel('试听文本',{exact:true}).fill('嘿嘿，今天想和你聊聊。');
  await page.getByRole('button',{name:'试听音色',exact:true}).click();await expect(audio).toBeVisible();
  expect(previews.at(-1)).toEqual({voice_id:'xiaoyi',text:'嘿嘿，今天想和你聊聊。'});
  await format.selectOption('text');await expect(studio).toBeHidden();
  await expect.poll(()=>audio.evaluate(el=>el.paused&&!el.hasAttribute('src'))).toBe(true);
  await format.selectOption('voice');await expect(studio).toBeVisible();
  await expect(select).toHaveValue('xiaoyi');await expect(audio).toBeHidden();
  expect(errors).toEqual([]);
});

test('联系人独立切换文字和语音，微信指令同步面板并走自动回复队列',async({page,request})=>{
  const http=require('http');
  const server=http.createServer(async(req,res)=>{
    for await(const chunk of req){}
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'好呀，我在呢。'},finish_reason:'stop'}]}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const create=async(path,data)=>(await request.post(path,{headers,data})).json();
  try{
    const provider=await create('/api/ai/config/providers',{name:'语音测试模型',base_url:`http://127.0.0.1:${server.address().port}/v1`,model:'voice-test'});
    const agent=await create('/api/ai/config/agents',{name:'语音测试 Agent',provider_id:provider.id,prompt:'简短聊天',web_policy:'off'});
    const contact=await create('/api/contacts',{lookup:'语音测试联系人',wechat_id:'voice-test'});
    const other=await create('/api/contacts',{lookup:'其他联系人',wechat_id:'other-voice-test'});
    await request.patch('/api/contacts/'+contact.id,{headers,data:{listening:true,mode:'ai_auto',ai_agent_id:agent.id}});
    await request.put('/api/ai/settings',{headers,data:{enabled:true,debounce_seconds:.3,context_messages:20}});
    await page.goto('/');
    await page.getByRole('button',{name:'打开 语音测试联系人 的聊天',exact:true}).click();
    const select=page.locator('#reply-format');
    const studio=page.locator('#voice-studio');
    await expect(select).toHaveValue('text');
    await expect(studio).toBeHidden();
    await select.selectOption('voice');
    await expect.poll(async()=>(await (await request.get('/api/contacts')).json()).find(c=>c.id===contact.id).reply_format).toBe('voice');
    await page.reload();await page.getByRole('button',{name:'打开 语音测试联系人 的聊天',exact:true}).click();await expect(select).toHaveValue('voice');
    await expect(studio).toBeVisible();
    await select.selectOption('text');
    await expect.poll(async()=>(await (await request.get('/api/contacts')).json()).find(c=>c.id===contact.id).reply_format).toBe('text');
    const invalid=await request.patch('/api/contacts/'+contact.id,{headers,data:{reply_format:'anything'}});
    expect(invalid.status()).toBe(422);
    await expect.poll(async()=>(await (await request.get('/api/contacts')).json()).find(c=>c.id===contact.id).listener_state).toBe('listening');
    const messages=async()=>(await request.get(`/api/contacts/${contact.id}/messages`)).json();
    await create(`/api/demo/${contact.id}/incoming`,{text:'以后用语音回复我吧'});
    await expect.poll(async()=>(await messages()).filter(m=>m.origin==='ai'&&m.status==='confirmed'&&m.kind==='voice').length,{timeout:20000}).toBe(1);
    await expect(select).toHaveValue('voice');
    await expect(studio).toBeVisible();
    await expect(page.locator('#messages')).toContainText('语音消息');
    await page.screenshot({path:test.info().outputPath('voice-reply-settings.png'),fullPage:true});
    await create(`/api/demo/${contact.id}/incoming`,{text:'改回文字回复'});
    await expect.poll(async()=>(await messages()).filter(m=>m.origin==='ai'&&m.status==='confirmed'&&m.kind==='text').length,{timeout:20000}).toBe(1);
    await expect(select).toHaveValue('text');
    await expect(studio).toBeHidden();
    expect((await (await request.get('/api/contacts')).json()).find(c=>c.id===other.id).reply_format).toBe('text');
  }finally{await new Promise(resolve=>server.close(resolve));}
});
