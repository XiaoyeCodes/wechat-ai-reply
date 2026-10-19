const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

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
