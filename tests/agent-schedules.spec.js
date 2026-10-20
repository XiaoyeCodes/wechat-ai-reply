const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

async function create(request,kind,data){
 const response=await request.post('/api/ai/config/'+kind,{headers,data});
 expect(response.ok(),await response.text()).toBeTruthy();return response.json();
}
async function openSchedules(page,agent){
 await page.goto('/');
 await page.getByRole('button',{name:'AI 中心',exact:true}).click();
 await page.locator(`[data-agent-schedules="${agent.id}"]`).click();
 await expect(page.getByRole('tab',{name:'Agent 定时任务',exact:true})).toBeVisible();
}

test('Agent 定时任务：创建每日任务、编辑每周规则、启停、历史与窄屏',async({page,request})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const provider=await create(request,'providers',{name:'定时界面假模型',model:'fixture',base_url:'http://127.0.0.1:19999/v1'});
 const skill=await create(request,'skills',{name:'daily-news-60s-ui',description:'仅用于隔离界面测试',instructions:'使用模拟新闻进行界面测试。'});
 const agent=await create(request,'agents',{name:'定时界面 Agent',provider_id:provider.id,skill_ids:[skill.id]});
 let sid;
 try{
  await openSchedules(page,agent);
  await page.getByRole('button',{name:'新建定时任务',exact:true}).click();
  const dialog=page.locator('#agent-schedule-editor');
  await dialog.locator('[name="agent_id"]').selectOption(agent.id);
  await dialog.locator('[name="title"]').fill('每日新闻测试');
  await dialog.locator('[name="frequency"]').selectOption('daily');
  await dialog.locator('[name="time"]').fill('09:15');
  await dialog.locator('[name="skill"]').selectOption(skill.id);
  await dialog.locator('#schedule-insert-skill').click();
  await expect(dialog.locator('[name="instruction"]')).toHaveValue(/daily-news-60s-ui/);
  await dialog.getByRole('button',{name:'保存任务',exact:true}).click();
  await expect(dialog).toHaveCount(0);
  const card=page.locator('.agent-schedule-card').filter({hasText:'每日新闻测试'});
  await expect(card).toContainText('每天 09:15');
  await expect(card).toContainText('已停用');
  await expect(card.getByRole('button',{name:'立即运行',exact:true})).toBeDisabled();
  const saved=(await (await request.get('/api/ai/agent-schedules')).json()).find(s=>s.title==='每日新闻测试');
  sid=saved.id;expect(saved).toMatchObject({enabled:false,kind:'cron',cron:'15 9 * * *',timezone:'Asia/Shanghai',eligible_count:0});
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await dialog.locator('[name="frequency"]').selectOption('weekly');
  await dialog.locator('[name="weekday"]').selectOption('1');
  await dialog.locator('[name="time"]').fill('08:30');
  await dialog.locator('[name="enabled"]').check();
  await dialog.getByRole('button',{name:'保存任务',exact:true}).click();
  await expect(card).toContainText('每周一 08:30');
  await expect(card).toContainText('暂无符合推送条件的联系人');
  await card.getByRole('button',{name:'执行记录',exact:true}).click();
  await expect(page.locator('#agent-schedule-history')).toContainText('还没有执行记录');
  await page.getByRole('button',{name:'关闭执行记录',exact:true}).click();
  await card.getByRole('button',{name:'停用',exact:true}).click();
  await expect(card).toContainText('已停用');
  await page.getByRole('tab',{name:'联系人关怀',exact:true}).click();
  await expect(page.getByRole('button',{name:'添加关怀日程',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'Agent 定时任务',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await expect(dialog.locator('[name="instruction"]')).toBeVisible();
  expect(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
  await expect(dialog.getByRole('button',{name:'保存任务',exact:true})).toBeInViewport();
  await page.screenshot({path:test.info().outputPath('agent-schedules-mobile.png')});
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  expect(errors).toEqual([]);
 }finally{
  if(sid)await request.delete('/api/ai/agent-schedules/'+sid,{headers});
  await request.delete('/api/ai/config/agents/'+agent.id,{headers});
  await request.delete('/api/ai/config/skills/'+skill.id,{headers});
  await request.delete('/api/ai/config/providers/'+provider.id,{headers});
 }
});

test('Agent 定时任务：本地假模型执行一次，向两位演示联系人投递同一结果',async({page,request})=>{
 const http=require('http');let calls=0;
 const server=http.createServer(async(req,res)=>{
  for await(const chunk of req){}
  calls++;res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'隔离演示的公共定时任务结果。'},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const provider=await create(request,'providers',{name:'定时模拟服务',model:'fixture',base_url:`http://127.0.0.1:${server.address().port}/v1`});
 const agent=await create(request,'agents',{name:'推送测试 Agent',provider_id:provider.id,web_policy:'off',companion:{safety:{emotion_enabled:false,crisis_detection:false}}});
 const contacts=[];let sid;
 try{
  const status=await (await request.get('/api/status')).json();expect(status.demo).toBe(true);
  for(const suffix of ['a','b']){
   const response=await request.post('/api/contacts',{headers,data:{lookup:'定时演示'+suffix,wechat_id:'schedule-ui-'+suffix}});
   expect(response.ok(),await response.text()).toBeTruthy();const contact=await response.json();contacts.push(contact);
   const configured=await request.patch('/api/contacts/'+contact.id,{headers,data:{listening:true,mode:'ai_auto',ai_agent_id:agent.id}});
   expect(configured.ok(),await configured.text()).toBeTruthy();
  }
  const enabled=await request.put('/api/ai/settings',{headers,data:{enabled:true}});expect(enabled.ok()).toBeTruthy();
  const response=await request.post('/api/ai/agent-schedules',{headers,data:{agent_id:agent.id,title:'共同结果推送',instruction:'返回一份简短公共结果。',kind:'cron',cron:'0 9 * * *',timezone:'Asia/Shanghai',enabled:true}});
  expect(response.ok(),await response.text()).toBeTruthy();sid=(await response.json()).id;
  await openSchedules(page,agent);
  const card=page.locator('.agent-schedule-card').filter({hasText:'共同结果推送'});
  await expect(card).toContainText('2 个符合条件的接收会话');
  await expect(card.getByRole('button',{name:'立即运行',exact:true})).toBeEnabled();
  await card.getByRole('button',{name:'立即运行',exact:true}).click();
  await expect(page.locator('#agent-schedule-history')).toBeVisible();
  await expect.poll(async()=>{
   const history=await (await request.get('/api/ai/agent-schedules/'+sid+'/runs')).json();return history[0]?.status;
  },{timeout:20000}).toBe('completed');
  await expect(page.locator('#schedule-history-content')).toContainText('会话投递 · 2 个',{timeout:10000});
  await expect(page.locator('#schedule-history-content').getByText('微信已核对',{exact:true})).toHaveCount(2);
  expect(calls).toBe(1);
  for(const contact of contacts){
   const history=await (await request.get('/api/ai/agent-schedules/'+sid+'/runs')).json();
   expect(history[0].recipients.find(r=>r.contact_id===contact.id).status).toBe('sent');
  }
  await page.screenshot({path:test.info().outputPath('agent-schedules-delivered.png'),fullPage:true});
 }finally{
  if(sid)await request.delete('/api/ai/agent-schedules/'+sid,{headers});
  for(const contact of contacts)await request.delete('/api/contacts/'+contact.id,{headers});
  await request.put('/api/ai/settings',{headers,data:{enabled:false}});
  await request.delete('/api/ai/config/agents/'+agent.id,{headers});
  await request.delete('/api/ai/config/providers/'+provider.id,{headers});
  await new Promise(resolve=>server.close(resolve));
 }
});
