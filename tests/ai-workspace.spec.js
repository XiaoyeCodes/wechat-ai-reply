const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};
let created=[];
test.beforeEach(()=>{created=[];});
test.afterEach(async({request})=>{
  for(const [kind,id] of created.reverse())await request.delete(`/api/ai/config/${kind}/${id}`,{headers});
});
async function create(request,kind,data){
  const response=await request.post('/api/ai/config/'+kind,{headers,data});
  expect(response.ok(),await response.text()).toBeTruthy();const resource=await response.json();created.push([kind,resource.id]);return resource;
}
async function enter(page){await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();}

test('工作空间：共享资源直接列出，Skills 与 MCP 分开管理',async({page,request})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const service=await create(request,'services',{name:'界面测试搜索',category:'search',driver:'agent_reach'});
  const mcp=await create(request,'mcp',{name:'界面测试 MCP',url:'http://127.0.0.1:19999/mcp',auth:'none'});
  await enter(page);
  await expect(page.locator('#ai-tabs [role="tab"]')).toHaveText(['我的 Agent','扩展中心','知识库','运行记录']);
  await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
  await expect(page.locator('#ai-subtabs [role="tab"]')).toHaveText(['Skills','工具与权限']);
  await page.getByRole('button',{name:'设置',exact:true}).click();
  for(const [kind,resource,title] of [['services',service,'服务配置'],['mcp',mcp,'MCP']]){
    await page.getByRole('tab',{name:title,exact:true}).click();
    const card=page.locator(`[data-resource-kind="${kind}"]`).filter({has:page.getByRole('heading',{name:resource.name,exact:true})});
    await expect(card).toBeVisible();
    await expect(page.locator(`[data-resource-kind="${kind==='mcp'?'services':'mcp'}"]`)).toHaveCount(0);
    await card.getByRole('button',{name:kind==='mcp'?'配置':'编辑',exact:true}).click();
    await expect(page.locator('#ai-editor-form').getByLabel('名称',{exact:true})).toHaveValue(resource.name);
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    expect((await (await request.get(`/api/ai/config/${kind}/${resource.id}`)).json()).id).toBe(resource.id);
  }
  await page.getByRole('button',{name:'添加 MCP',exact:true}).click();
  await expect(page.getByLabel('MCP 接入地址',{exact:true})).toBeVisible();
  await page.locator('#ai-editor-cancel').click();
  await expect(page.locator('#ai-connector-picker')).toHaveCount(0);
  await page.getByRole('tab',{name:'模型与 API',exact:true}).click();
  await expect(page.getByRole('tab',{name:'模型与 API',exact:true})).toHaveAttribute('aria-selected','true');
  await page.getByRole('tab',{name:'回复设置',exact:true}).click();
  await page.getByRole('button',{name:'调整回复设置',exact:true}).click();
  await expect(page.locator('#ai-context')).toBeVisible();await page.locator('#ai-settings-close').click();
  await page.getByRole('button',{name:'AI 中心',exact:true}).click();
  await page.getByRole('tab',{name:'知识库',exact:true}).click();
  await expect(page.locator('#ai-section-description')).toContainText('共享资源');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:test.info().outputPath('shared-resources-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('tab',{name:'扩展中心',exact:true}).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab',{name:'知识库',exact:true})).toBeFocused();
  expect(errors).toEqual([]);
});

test('Agent：折叠保存保持配置、能力只在保存后授权、技能缺项提示',async({page,request})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const provider=await create(request,'providers',{name:'整理测试模型',base_url:'http://127.0.0.1:19999/v1',model:'fixture',api_key:'unchanged-secret'});
  const search=await create(request,'services',{name:'整理测试搜索',category:'search',driver:'agent_reach'});
  const skill=await create(request,'skills',{name:'web-video-reader',description:'视频总结',instructions:'读取视频字幕再总结'});
  const bootstrap=await (await request.get('/api/ai/bootstrap')).json();
  const clock=bootstrap.tools.find(tool=>tool.builtin==='clock');
  const agent=await create(request,'agents',{
    name:'配置保留测试',provider_id:provider.id,prompt:'保持我的原提示词。',kind:'workflow',tool_ids:[clock.id],skill_ids:[skill.id],
    web_policy:'off',source_policy:'requested',temperature:.85,max_tool_rounds:7,
    companion:{persona:{name:'原有名称',emoji_probability:.17},memory:{enabled:true,recent_messages:31,retention_days:321},care:{quiet_start:'22:17',min_gap_minutes:155},media:{lora_scale:0,voice_speed:1.2},search_service_id:search.id},
  });
  const before=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
  await enter(page);
  const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:agent.name,exact:true})});
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await expect(page.locator('#ai-editor-tabs [role="tab"]')).toHaveText(['角色与模型','工具与技能','知识与记忆','回复与关怀','运行设置']);
  await expect(page.getByLabel('Agent 来源',{exact:true})).toHaveCount(0);
  await page.locator('#agent-tab-abilities').click();
  await expect(page.locator('#agent-rights')).toHaveAttribute('open','');
  await expect(page.locator('#ai-skill-dependencies')).toContainText('所需工具尚未全部授权');
  await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(page.locator('#ai-editor')).not.toBeVisible();
  const after=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
  for(const key of ['prompt','provider_id','tool_ids','skill_ids','web_policy','source_policy','temperature','max_tool_rounds','companion','steps'])expect(after[key],key).toEqual(before[key]);
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await page.locator('#agent-tab-abilities').click();
  await page.locator('summary').filter({hasText:'网页搜索工具连接'}).click();
  await page.locator('[data-cp-grant="web_search,read_web"]').click();
  await expect(page.locator('#cp-search-status')).toContainText('当前已关闭联网');
  await page.locator('#ai-editor-cancel').click();
  expect((await (await request.get('/api/ai/config/agents/'+agent.id)).json()).tool_ids).toEqual(before.tool_ids);
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await page.locator('#agent-tab-abilities').click();
  await page.locator('summary').filter({hasText:'网页搜索工具连接'}).click();
  await page.locator('[data-cp-grant="web_search,read_web"]').click();
  await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(page.locator('#ai-editor')).not.toBeVisible();
  const enabled=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
  const expected=['web_search','read_web'].map(name=>bootstrap.tools.find(tool=>tool.builtin===name).id);
  expect(enabled.tool_ids).toEqual(expect.arrayContaining([clock.id,...expected]));
  expect(enabled.web_policy).toBe('off');
  expect(enabled.companion).toEqual(before.companion);
  expect((await (await request.get('/api/ai/config/providers/'+provider.id)).json()).key_configured).toBe(true);
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await expect(page.locator('#ai-editor-tabs')).toBeVisible();
  await page.screenshot({path:'test-results/workspace-agent-editor.png',fullPage:true});
  await page.locator('#ai-editor-cancel').click();
  await page.screenshot({path:'test-results/workspace-desktop.png',fullPage:true});
  expect(errors).toEqual([]);
});


test('Agent 所属范围：记忆迟到响应不串页，任务只显示与保存到当前 Agent',async({page,request})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const provider=await create(request,'providers',{name:'隔离测试模型',model:'fixture',base_url:'http://127.0.0.1:19999/v1'});
  const a=await create(request,'agents',{name:'独立助手 A',provider_id:provider.id}),b=await create(request,'agents',{name:'独立助手 B',provider_id:provider.id});
  const schedules=[],care=[];
  const contactResponse=await request.post('/api/contacts',{headers,data:{lookup:'林晓',wechat_id:'owner_test'}});
  expect(contactResponse.ok(),await contactResponse.text()).toBeTruthy();const contact=await contactResponse.json();
  for(const item of [a,b]){
    const result=await request.post('/api/ai/agent-schedules',{headers,data:{agent_id:item.id,title:item.name+' 的任务',instruction:'测试，不启用',kind:'cron',cron:'0 9 * * *',timezone:'Asia/Shanghai',enabled:false}});
    expect(result.ok(),await result.text()).toBeTruthy();schedules.push((await result.json()).id);
    const careResult=await request.post('/api/ai/schedules',{headers,data:{agent_id:item.id,contact_id:contact.id,title:item.name+' 的关怀',instruction:'测试，不启用',kind:'cron',cron:'0 9 * * *',timezone:'Asia/Shanghai',delivery:'draft',enabled:false}});
    expect(careResult.ok(),await careResult.text()).toBeTruthy();care.push((await careResult.json()).id);
  }
  let release,arrived;
  const held=new Promise(resolve=>release=resolve),started=new Promise(resolve=>arrived=resolve);
  await page.route('**/api/ai/companion?*',async route=>{
    const response=await route.fetch();const body=await response.json();
    const id=new URL(route.request().url()).searchParams.get('agent_id');
    body.profile.preferred_name=id===a.id?'只属于 A':'只属于 B';
    if(id===a.id){arrived();await held;}
    await route.fulfill({response,json:body});
  });
  try{
    await enter(page);
    await page.locator(`[data-agent-memory="${a.id}"]`).click();await started;
    await page.locator('[data-agent-back]').click();
    await page.locator(`[data-agent-memory="${b.id}"]`).click();
    await expect(page.getByLabel('喜欢的称呼')).toHaveValue('只属于 B');
    const delivered=page.waitForResponse(r=>r.url().includes('/api/ai/companion?')&&r.url().includes(a.id));release();await delivered;
    await expect(page.getByLabel('喜欢的称呼')).toHaveValue('只属于 B');
    await expect(page.locator('#ai-agent-context')).toContainText(b.name);
    await expect(page.getByLabel('记忆所属 Agent').locator('option')).toHaveCount(1);
    await page.getByLabel('喜欢的称呼').fill('B 的新称呼');
    const saved=page.waitForRequest(r=>r.url().endsWith('/api/ai/companion/profile')&&r.method()==='PUT');
    await page.getByRole('button',{name:'保存用户档案'}).click();
    expect((await saved).postDataJSON().agent_id).toBe(b.id);
    await page.getByRole('tab',{name:'自动任务',exact:true}).click();
    await expect(page.locator('.agent-schedule-card')).toHaveCount(1);
    await expect(page.locator('.agent-schedule-card')).toContainText(b.name+' 的任务');
    await page.getByRole('button',{name:'新建定时任务',exact:true}).click();
    const dialog=page.locator('#agent-schedule-editor');
    await expect(dialog.getByLabel('执行 Agent').locator('option')).toHaveCount(1);
    await expect(dialog.getByLabel('执行 Agent')).toHaveValue(b.id);
    await dialog.getByLabel('任务名称',{exact:true}).fill('B 新任务');
    await dialog.getByLabel('任务内容',{exact:true}).fill('验证所属 Agent，不启用');
    await dialog.getByRole('button',{name:'保存任务',exact:true}).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('.agent-schedule-card')).toHaveCount(2);
    const rows=await (await request.get('/api/ai/agent-schedules')).json();
    const added=rows.find(s=>s.title==='B 新任务');schedules.push(added.id);expect(added.agent_id).toBe(b.id);
    await page.getByRole('tab',{name:'联系人关怀',exact:true}).click();
    await expect(page.locator('.care-card')).toHaveCount(1);
    await expect(page.locator('.care-card')).toContainText(b.name+' 的关怀');
    await page.getByRole('button',{name:'添加关怀日程'}).click();
    await expect(page.getByLabel('关怀 Agent').locator('option')).toHaveCount(1);
    await expect(page.getByLabel('关怀 Agent')).toHaveValue(b.id);
    await page.getByLabel('日程名称',{exact:true}).fill('B 新关怀');
    await page.locator('#companion-modal').getByRole('button',{name:'保存',exact:true}).click();
    await expect(page.locator('#companion-modal')).not.toBeVisible();
    await expect(page.locator('.care-card')).toHaveCount(2);
    const newCare=(await (await request.get('/api/ai/schedules')).json()).find(s=>s.title==='B 新关怀');care.push(newCare.id);expect(newCare.agent_id).toBe(b.id);
    await page.locator('[data-agent-back]').click();await page.locator(`[data-agent-schedules="${a.id}"]`).click();
    await expect(page.locator('.agent-schedule-card')).toHaveCount(1);
    await expect(page.locator('.agent-schedule-card')).toContainText(a.name+' 的任务');
    await page.screenshot({path:test.info().outputPath('agent-owned-settings.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await page.screenshot({path:test.info().outputPath('agent-owned-settings-mobile.png'),fullPage:true});
    expect(errors).toEqual([]);
  }finally{release();for(const id of schedules)await request.delete('/api/ai/agent-schedules/'+id,{headers});for(const id of care)await request.delete('/api/ai/schedules/'+id,{headers});await request.delete('/api/contacts/'+contact.id,{headers});}
});
