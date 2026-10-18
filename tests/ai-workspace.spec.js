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

test('工作空间：五个主入口、统一连接器、全局设置与窄屏导航',async({page,request})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const service=await create(request,'services',{name:'界面测试搜索',category:'search',driver:'agent_reach'});
  const mcp=await create(request,'mcp',{name:'界面测试 MCP',url:'http://127.0.0.1:19999/mcp',auth:'none'});
  await enter(page);
  await expect(page.locator('#ai-tabs [role="tab"]')).toHaveText(['我的 Agent','扩展中心','资料与记忆','自动任务','运行记录']);
  await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
  await page.getByRole('tab',{name:'连接器',exact:true}).click();
  await expect(page.getByRole('heading',{name:service.name,exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:mcp.name,exact:true})).toBeVisible();
  for(const [kind,resource] of [['services',service],['mcp',mcp]]){
    const card=page.locator(`[data-resource-kind="${kind}"]`).filter({has:page.getByRole('heading',{name:resource.name,exact:true})});
    await card.getByRole('button',{name:kind==='mcp'?'配置':'编辑',exact:true}).click();
    await expect(page.locator('#ai-editor-form').getByLabel('名称',{exact:true})).toHaveValue(resource.name);
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const result=await (await request.get(`/api/ai/config/${kind}/${resource.id}`)).json();
    expect(result.id).toBe(resource.id);
  }
  await page.getByRole('button',{name:'添加连接器',exact:true}).click();
  await page.locator('[data-connector-type="tts"]').click();
  await expect(page.getByLabel('能力类别',{exact:true})).toHaveValue('tts');
  await page.locator('#ai-editor-cancel').click();
  await page.locator('#ai-global-settings').click();
  await expect(page.getByRole('tab',{name:'模型管理',exact:true})).toHaveAttribute('aria-selected','true');
  await page.getByRole('tab',{name:'回复设置',exact:true}).click();
  await page.getByRole('button',{name:'调整回复设置',exact:true}).click();
  await expect(page.locator('#ai-context')).toBeVisible();await page.locator('#ai-settings-close').click();
  await page.getByRole('tab',{name:'资料与记忆',exact:true}).click();
  await page.getByRole('tab',{name:'记忆与档案',exact:true}).click();
  await expect(page.getByLabel('记忆所属 Agent')).toBeVisible();
  await page.getByRole('tab',{name:'自动任务',exact:true}).click();
  await expect(page.getByRole('button',{name:'添加关怀日程'})).toBeVisible();
  await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
  await expect(page.getByRole('tab',{name:'连接器',exact:true})).toHaveAttribute('aria-selected','true');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'运行记录',exact:true}).click();
  await page.getByRole('tab',{name:'我的 Agent',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:'test-results/workspace-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('tab',{name:'扩展中心',exact:true}).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab',{name:'资料与记忆',exact:true})).toBeFocused();
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
  await expect(page.locator('#ai-editor-tabs [role="tab"]')).toHaveText(['基本设定','能力与技能','记忆与关怀','高级设置']);
  await expect(page.getByLabel('Agent 来源',{exact:true})).toHaveCount(0);
  await page.locator('#agent-tab-abilities').click();
  await expect(page.locator('[name="tools_ids"]').first()).not.toBeVisible();
  await page.locator('#agent-rights > summary').click();
  await expect(page.locator('#ai-skill-dependencies')).toContainText('所需工具尚未全部授权');
  await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(page.locator('#ai-editor')).not.toBeVisible();
  const after=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
  for(const key of ['prompt','provider_id','tool_ids','skill_ids','web_policy','source_policy','temperature','max_tool_rounds','companion','steps'])expect(after[key],key).toEqual(before[key]);
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await page.locator('#agent-tab-abilities').click();
  await page.locator('summary').filter({hasText:'联网与生活'}).click();
  await page.locator('[data-cp-grant="web_search,read_web"]').click();
  await expect(page.locator('#cp-search-status')).toContainText('当前已关闭联网');
  await page.locator('#ai-editor-cancel').click();
  expect((await (await request.get('/api/ai/config/agents/'+agent.id)).json()).tool_ids).toEqual(before.tool_ids);
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  await page.locator('#agent-tab-abilities').click();
  await page.locator('summary').filter({hasText:'联网与生活'}).click();
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
