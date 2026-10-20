const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

test('群聊：只向指定群发布一次定时结果，并按群设置发送语音',async({page,request})=>{
 const http=require('http');let calls=0,provider,agent,sid;const contacts=[];
 const server=http.createServer(async(req,res)=>{for await(const chunk of req){}calls++;
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'功能测试，指定群定时语音发布成功。'},finish_reason:'stop'}]}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  expect((await (await request.get('/api/status')).json()).demo).toBe(true);
  provider=await (await request.post('/api/ai/config/providers',{headers,data:{name:'群发布假模型',model:'fixture',base_url:`http://127.0.0.1:${server.address().port}/v1`}})).json();
  agent=await (await request.post('/api/ai/config/agents',{headers,data:{name:'群发布 Agent',provider_id:provider.id,web_policy:'off',companion:{safety:{emotion_enabled:false,crisis_detection:false}}}})).json();
  for(const data of [{lookup:'指定发布群',chat_type:'group',group_self_name:'机器人'},
   {lookup:'未选择的群',chat_type:'group',group_self_name:'机器人'},
   {lookup:'未选择的私聊',wechat_id:'unselected-private'}]){
   const response=await request.post('/api/contacts',{headers,data});expect(response.ok(),await response.text()).toBeTruthy();
   const c=await response.json();contacts.push(c);
   const patched=await request.patch('/api/contacts/'+c.id,{headers,data:{listening:true,mode:'ai_auto',ai_agent_id:agent.id,reply_format:'voice'}});
   expect(patched.ok(),await patched.text()).toBeTruthy();
  }
  await request.put('/api/ai/settings',{headers,data:{enabled:true}});
  await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
  await page.locator(`[data-agent-schedules="${agent.id}"]`).click();
  await page.getByRole('button',{name:'新建定时任务',exact:true}).click();
  const d=page.locator('#agent-schedule-editor');
  await d.locator('[name="title"]').fill('指定群语音发布');
  await d.locator('[name="instruction"]').fill('返回简短的公共测试结果。');
  await d.locator('[name="frequency"]').selectOption('once');
  await d.locator('[name="at"]').fill(new Date(Date.now()+86400000).toISOString().slice(0,16));
  await d.locator('[name="audience_scope"]').selectOption('selected');
  await d.locator(`[name="recipient_id"][value="${contacts[0].id}"]`).check();
  await expect(d.locator('#schedule-audience')).toContainText('当前 1 个会话');
  await page.setViewportSize({width:390,height:844});
  expect(await d.evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
  await d.locator('[name="enabled"]').check();
  await d.getByRole('button',{name:'保存任务',exact:true}).click();
  await expect(d).toHaveCount(0);
  const schedules=await (await request.get('/api/ai/agent-schedules')).json();
  const saved=schedules.find(s=>s.title==='指定群语音发布');sid=saved.id;
  expect(saved.recipient_ids).toEqual([contacts[0].id]);expect(saved.eligible_count).toBe(1);
  await page.setViewportSize({width:1440,height:960});
  const card=page.locator('.agent-schedule-card').filter({hasText:'指定群语音发布'});
  await card.getByRole('button',{name:'立即运行',exact:true}).click();
  await expect.poll(async()=>{const history=await (await request.get('/api/ai/agent-schedules/'+sid+'/runs')).json();return history[0]?.status;},{timeout:20000}).toBe('completed');
  expect(calls).toBe(1);
  const delivered=await (await request.get('/api/contacts/'+contacts[0].id+'/messages')).json();
  expect(delivered.filter(m=>m.origin==='ai')).toMatchObject([{kind:'voice',status:'confirmed',text:'功能测试，指定群定时语音发布成功。'}]);
  for(const contact of contacts.slice(1))expect(await (await request.get('/api/contacts/'+contact.id+'/messages')).json()).toEqual([]);
  await expect(page.locator('#schedule-history-content')).toContainText('会话投递 · 1 个');
  await page.screenshot({path:test.info().outputPath('group-scheduled-voice.png'),fullPage:true});
 }finally{
  if(sid)await request.delete('/api/ai/agent-schedules/'+sid,{headers});
  for(const c of contacts)await request.delete('/api/contacts/'+c.id,{headers});
  await request.put('/api/ai/settings',{headers,data:{enabled:false}});
  if(agent?.id)await request.delete('/api/ai/config/agents/'+agent.id,{headers});
  if(provider?.id)await request.delete('/api/ai/config/providers/'+provider.id,{headers});
  await new Promise(resolve=>server.close(resolve));
 }
});

test('群聊：表单添加、规则编辑及手机排版',async({page,request})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));let id;
 try{
  await page.goto('/');await page.locator('#add-contact').click();
  await page.locator('#contact-type').selectOption('group');
  await expect(page.locator('#contact-wechat')).toBeHidden();
  await page.locator('#contact-lookup').fill('UI 群聊测试');
  await page.locator('#contact-group-scope').selectOption('selected');
  await page.locator('#contact-group-members').fill('张三\n李四');
  await page.locator('#contact-group-self').fill('机器人');
  await page.locator('#contact-submit').click();
  await expect(page.locator('#contact-dialog')).not.toBeVisible();
  const contacts=await (await request.get('/api/contacts')).json();const group=contacts.find(c=>c.lookup==='UI 群聊测试');
  expect(group).toMatchObject({chat_type:'group',group_members:['张三','李四'],group_mention_only:true,group_self_name:'机器人'});id=group.id;
  await expect(page.locator('#group-listener-settings')).toBeVisible();
  await expect(page.locator('#group-detail-self')).toHaveValue('机器人');
  await expect(page.locator('#reply-format')).toBeEnabled();
  await page.locator('#reply-format').selectOption('voice');
  await expect(page.locator('#voice-studio')).toBeVisible();
  await page.locator('#reply-format').selectOption('text');
  await expect(page.locator('#voice-studio')).toBeHidden();
  await page.locator('#group-detail-trigger').selectOption('messages');
  await page.locator('#group-settings-save').click();
  await expect(page.locator('#group-settings-status')).toHaveText('已保存');
  expect((await (await request.get('/api/contacts')).json()).find(c=>c.id===id).group_mention_only).toBe(false);
  await page.locator('#add-contact').click();
  await page.locator('#contact-type').selectOption('group');
  await page.setViewportSize({width:390,height:844});
  expect(await page.locator('#contact-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
  await expect(page.locator('#contact-submit')).toBeInViewport();
  await page.screenshot({path:test.info().outputPath('group-add-mobile.png')});
  expect(errors).toEqual([]);
 }finally{if(id)await request.delete('/api/contacts/'+id,{headers});}
});

test('群聊：仅指定成员@触发一次，并通过演示队列回复到原群',async({page,request})=>{
 const http=require('http');let calls=0,gid,did,provider,agent;
 const server=http.createServer(async(req,res)=>{
  for await(const chunk of req){}calls++;
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'群内收到，这是原群回复。'},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  expect((await (await request.get('/api/status')).json()).demo).toBe(true);
  provider=await (await request.post('/api/ai/config/providers',{headers,data:{name:'群演示模型',model:'fixture',base_url:`http://127.0.0.1:${server.address().port}/v1`}})).json();
  agent=await (await request.post('/api/ai/config/agents',{headers,data:{name:'群演示 Agent',provider_id:provider.id,web_policy:'off',companion:{safety:{emotion_enabled:false,crisis_detection:false}}}})).json();
  const g=await request.post('/api/contacts',{headers,data:{chat_type:'group',lookup:'原群测试',group_members:['张三'],group_self_name:'机器人',group_mention_only:true}});
  expect(g.ok(),await g.text()).toBeTruthy();gid=(await g.json()).id;
  const d=await request.post('/api/contacts',{headers,data:{lookup:'私聊对照',wechat_id:'group-test-direct'}});did=(await d.json()).id;
  await request.patch('/api/contacts/'+gid,{headers,data:{listening:true,mode:'ai_auto',ai_agent_id:agent.id}});
  await request.put('/api/ai/settings',{headers,data:{enabled:true}});
  await page.goto('/');await page.locator(`[data-contact="${gid}"]`).click();
  await request.post('/api/demo/'+gid+'/incoming',{headers,data:{text:'@机器人 无关成员的消息',sender:'李四',mention_me:true}});
  await request.post('/api/demo/'+gid+'/incoming',{headers,data:{text:'指定成员普通消息',sender:'张三',mention_me:false}});
  await request.post('/api/demo/'+gid+'/incoming',{headers,data:{text:'@机器人 请回复到原群',sender:'张三',mention_me:true}});
  await expect.poll(async()=>{
   const messages=await (await request.get('/api/contacts/'+gid+'/messages')).json();
   return messages.filter(m=>m.origin==='ai'&&m.status==='confirmed').map(m=>m.text);
  },{timeout:20000}).toEqual(['群内收到，这是原群回复。']);
  expect(calls).toBe(1);
  expect(await (await request.get('/api/contacts/'+did+'/messages')).json()).toEqual([]);
  await expect(page.locator('#messages')).toContainText('群内收到，这是原群回复。');
  await expect(page.locator('#messages')).toContainText('张三');
  await page.screenshot({path:test.info().outputPath('group-reply-demo.png'),fullPage:true});
 }finally{
  if(gid)await request.delete('/api/contacts/'+gid,{headers});if(did)await request.delete('/api/contacts/'+did,{headers});
  await request.put('/api/ai/settings',{headers,data:{enabled:false}});
  if(agent?.id)await request.delete('/api/ai/config/agents/'+agent.id,{headers});
  if(provider?.id)await request.delete('/api/ai/config/providers/'+provider.id,{headers});
  await new Promise(resolve=>server.close(resolve));
 }
});
