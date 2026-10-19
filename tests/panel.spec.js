const { test, expect } = require('@playwright/test');
const headers={'X-Panel-Request':'1'};
async function editEngineFixture(page,request,data){
  const response=await request.post('/api/ai/config/agents',{headers,data});
  expect(response.ok(),await response.text()).toBeTruthy();const agent=await response.json();
  await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
  await page.locator(`.text-button[data-edit="${agent.id}"]`).click();return agent;
}

test('Vibe-Trading：保留原生引擎、保存工具技能、检查和测试回复',async({page,request})=>{
  test.setTimeout(90000);
  const http=require('http');
  const server=http.createServer(async(req,res)=>{
    for await(const chunk of req){}
    res.setHeader('Content-Type','text/event-stream');
    res.end('data: '+JSON.stringify({id:'ui',object:'chat.completion.chunk',model:'ui-model',choices:[{index:0,finish_reason:'stop',delta:{role:'assistant',content:'Vibe 原生引擎界面测试通过。'}}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const provider=await (await request.post('/api/ai/config/providers',{headers,data:{name:'Vibe UI 模型',base_url:`http://127.0.0.1:${server.address().port}/v1`,model:'ui-model',api_key:'local-test'}})).json();
  let aid;
  try{
    aid=(await editEngineFixture(page,request,{name:'Vibe UI 助手',kind:'vibe',provider_id:provider.id,vibe_engine:'sparkflow'})).id;
    const form=page.locator('#ai-editor-form');
    await expect(page.locator('#ai-workflow-fields')).not.toBeVisible();
    await expect(page.locator('#ai-companion-fields')).not.toBeVisible();
    await form.getByLabel('Vibe-Trading 使用的模型',{exact:true}).selectOption(provider.id);
    await page.locator('#agent-tab-advanced').click();
    await form.locator('[name="vibe_web_policy"]').selectOption('off');
    await expect(form.locator('[name="vibe_engine"]')).toHaveValue('sparkflow');
    await expect(form.getByText('金融助手回复要求',{exact:true})).toHaveCount(0);
    await expect(form.getByLabel('最大迭代次数（同步 SparkFlow）',{exact:true})).toHaveValue('50');
    await page.locator('#agent-tab-abilities').click();
    await expect(form.getByText(/原生工具包（\d+ 项）/)).toBeVisible();
    await form.getByRole('button',{name:'保存配置'}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const data=await (await request.get('/api/ai/bootstrap')).json();
    const a=data.agents.find(x=>x.name==='Vibe UI 助手');aid=a.id;
    expect(a.kind).toBe('vibe');expect(a.web_policy).toBe('off');expect(a.vibe_engine).toBe('sparkflow');expect(a.prompt).toBe('');
    expect(a.vibe_max_iterations).toBe(50);expect(a.vibe_task_timeout).toBeNull();
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'Vibe UI 助手',exact:true})});
    await expect(card).toContainText('VIBE-TRADING AGENT');
    await card.getByRole('button',{name:'检查引擎',exact:true}).click();
    await expect(card.getByRole('button',{name:'检查引擎',exact:true})).toBeEnabled({timeout:20000});
    await card.getByRole('button',{name:'编辑',exact:true}).click();
    await expect(page.locator('#ai-editor')).toBeVisible();
    await page.locator('#agent-tab-advanced').click();
    await expect(page.locator('#ai-vibe-fields')).toBeVisible();
    await expect(form.locator('[name="vibe_web_policy"]')).toHaveValue('off');
    await expect(form.locator('[name="vibe_engine"]')).toHaveValue('sparkflow');
    await page.screenshot({path:test.info().outputPath('vibe-agent-settings.png'),fullPage:true});
    await page.locator('#ai-editor-cancel').click();
    await card.getByRole('button',{name:'测试 Agent',exact:true}).click();
    await page.locator('#ai-test-text').fill('你好，简单打个招呼');
    await page.locator('#ai-test-submit').click();
    await expect(page.locator('#ai-test-result')).toContainText('Vibe 原生引擎界面测试通过。',{timeout:60000});
    await page.screenshot({path:test.info().outputPath('vibe-agent-reply.png'),fullPage:true});
      await page.locator('#ai-test-text').fill('请以 PDF 格式发给我');
      await page.locator('#ai-test-submit').click();
      const pdfLink=page.locator('#ai-test-result').getByRole('link',{name:/下载 PDF/});
      await expect(pdfLink).toBeVisible({timeout:20000});
      const pdfResponse=await request.get(await pdfLink.getAttribute('href'));
      expect(pdfResponse.status()).toBe(200);
      expect(pdfResponse.headers()['content-type']).toContain('application/pdf');
  }finally{
    if(aid)await request.delete('/api/ai/config/agents/'+aid,{headers});
    await request.delete('/api/ai/config/providers/'+provider.id,{headers});
    await new Promise(resolve=>server.close(resolve));
  }
});

test('Hermes：操作权限与微信确认配置可保存和重新编辑',async({page,request})=>{
  const provider=await (await request.post('/api/ai/config/providers',{headers,data:{name:'权限 UI 模型',base_url:'http://127.0.0.1:19999/v1',model:'ui-only'}})).json();
  let aid;
  try{
    aid=(await editEngineFixture(page,request,{name:'权限 UI Agent',kind:'hermes',provider_id:provider.id,hermes_managed:true,hermes_tool_policy:'sensitive'})).id;
    const form=page.locator('#ai-editor-form');
    await form.getByLabel('Hermes 使用的模型',{exact:true}).selectOption(provider.id);
    await page.locator('#agent-tab-advanced').click();
    await form.getByLabel('电脑操作权限',{exact:true}).selectOption('sensitive');
    await form.getByLabel('操作确认渠道',{exact:true}).selectOption('wechat');
    await form.getByLabel('确认有效期（秒）',{exact:true}).fill('300');
    await form.getByRole('button',{name:'保存配置'}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const config=await (await request.get('/api/ai/bootstrap')).json();
    const a=config.agents.find(x=>x.name==='权限 UI Agent');aid=a.id;
    expect(a).toMatchObject({hermes_chat_routing:true,hermes_task_approval:'auto',hermes_tool_policy:'sensitive',hermes_approval_scope:'task',hermes_approval_channel:'wechat',hermes_approval_timeout:300});
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'权限 UI Agent',exact:true})});
    await card.getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('#agent-tab-advanced').click();
    await expect(form.getByLabel('电脑操作权限',{exact:true})).toHaveValue('sensitive');
    await expect(form.getByLabel('操作确认渠道',{exact:true})).toHaveValue('wechat');
    expect(await form.getByLabel('电脑操作权限',{exact:true}).locator('option').count()).toBe(3);
    for(const mode of ['ai_review','full']){
      await form.getByLabel('电脑操作权限',{exact:true}).selectOption(mode);
      if(mode==='full'){
        await expect(form.locator('#ai-hermes-confirm-options')).not.toBeVisible();
        await expect(form.locator('#ai-hermes-full-note')).toBeVisible();
      }
      await form.getByRole('button',{name:'保存配置'}).click();
      await expect(page.locator('#ai-editor')).not.toBeVisible();
      const saved=await (await request.get('/api/ai/config/agents/'+aid)).json();
      expect(saved).toMatchObject({hermes_task_approval:'auto',hermes_tool_policy:mode,hermes_approval_scope:'task'});
      await card.getByRole('button',{name:'编辑',exact:true}).click();
      await expect(page.locator('#ai-editor')).toBeVisible();
      await page.locator('#agent-tab-advanced').click();
      await expect(form.getByLabel('电脑操作权限',{exact:true})).toHaveValue(mode);
    }
    await page.screenshot({path:'test-results/hermes-permissions.png',fullPage:true});
  }finally{
    if(aid)await request.delete('/api/ai/config/agents/'+aid,{headers});
    await request.delete('/api/ai/config/providers/'+provider.id,{headers});
  }
});

test('Hermes：配置引擎、检查连接、先确认任务再执行',async({page,request})=>{
  const http=require('http');let submissions=0;
  const server=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    res.setHeader('Content-Type','application/json');
    if(req.headers.authorization!=='Bearer ui-hermes-key'){res.writeHead(401);res.end('{}');return;}
    if(req.url==='/v1/capabilities')res.end(JSON.stringify({platform:'hermes-agent',features:{run_submission:true,run_status:true,run_stop:true,run_approval_response:true}}));
    else if(req.url==='/v1/runs'){submissions++;res.end(JSON.stringify({run_id:'ui_run'}));}
    else if(req.url.endsWith('/events'))res.end('{}');
    else res.end(JSON.stringify({status:'completed',completed:true,output:'UI Hermes 测试完成'}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    await editEngineFixture(page,request,{name:'UI Hermes',kind:'hermes',hermes_managed:false,url:`http://127.0.0.1:${server.address().port}`,api_key:'ui-hermes-key'});
    const form=page.locator('#ai-editor-form');
    await page.locator('#agent-tab-advanced').click();
    await expect(page.locator('#ai-companion-fields')).not.toBeVisible();
    await form.getByLabel('引擎运行方式',{exact:true}).selectOption('false');
    await form.getByLabel('任务执行策略',{exact:true}).selectOption('confirm');
    await expect(page.locator('#ai-hermes-local')).not.toBeVisible();
    await form.getByLabel('外部 Agent 地址',{exact:true}).fill(`http://127.0.0.1:${server.address().port}`);
    await form.getByLabel('API Key',{exact:true}).fill('ui-hermes-key');
    await form.getByRole('button',{name:'保存配置'}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'UI Hermes',exact:true})});
    await expect(card).toContainText('仅测试台');
    await card.getByRole('button',{name:'检查引擎',exact:true}).click();
    await card.getByRole('button',{name:'测试 Agent',exact:true}).click();
    await expect(page.locator('#ai-hermes-test-note')).toBeVisible();
    await page.locator('#ai-test-text').fill('测试执行任务');
    await page.locator('#ai-test-submit').click();
    await expect(page.locator('#ai-test-result')).toContainText('电脑任务确认');
    expect(submissions).toBe(0);
    await page.locator('#ai-test-result').getByRole('button',{name:'批准执行',exact:true}).click();
    await expect(page.locator('#ai-test-result')).toContainText('UI Hermes 测试完成');
    expect(submissions).toBe(1);
    const result=await (await request.get('/api/ai/runs')).json();
    expect(result.find(r=>r.input==='测试执行任务').dry_run).toBeTruthy();
    await page.screenshot({path:'test-results/hermes-run.png',fullPage:true});
  }finally{
    const config=await (await request.get('/api/ai/bootstrap')).json();
    const fixture=config.agents.find(a=>a.name==='UI Hermes');
    if(fixture)await request.delete('/api/ai/config/agents/'+fixture.id,{headers});
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  }
});

test('MCP：待配置预设、发现工具、导入及编辑权限',async({page,request})=>{
  const http=require('http');
  const server=http.createServer(async(req,res)=>{
    if(req.method!=='POST'){res.writeHead(405);res.end();return;}
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw);
    if(body.id===undefined){res.writeHead(202);res.end();return;}
    const result=body.method==='initialize'?{protocolVersion:body.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'UI MCP fixture',version:'1'}}:
      {tools:[{name:'maps_weather',description:'读取指定城市天气',inputSchema:{type:'object',properties:{city:{type:'string'}},required:['city']}}]};
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({jsonrpc:'2.0',id:body.id,result}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.getByRole('button',{name:'设置',exact:true}).click();await page.getByRole('tab',{name:'MCP',exact:true}).click();
    const amap=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'高德地图 MCP',exact:true})});
    await expect(amap).toContainText('待填写 Key');
    await amap.getByRole('button',{name:'发现工具'}).click();
    await expect(page.locator('#mcp-error')).toContainText('尚未配置 MCP 密钥');
    await page.getByRole('button',{name:'关闭 MCP 工具'}).click();
    const tree=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'TreeMind 树图（待接入）',exact:true})});
    await expect(tree).toContainText('待提供官方接口');
    await tree.getByRole('button',{name:'配置',exact:true}).click();
    await expect(page.locator('#mcp-profile-hint')).toContainText('尚未核实公开 MCP 地址');
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByRole('button',{name:'添加 MCP',exact:true}).click();
    const form=page.locator('#ai-editor-form');
    await form.getByLabel('名称',{exact:true}).fill('UI 本地 MCP');
    await form.getByLabel('MCP 接入地址',{exact:true}).fill(`http://127.0.0.1:${server.address().port}/mcp`);
    await form.getByLabel('认证方式',{exact:true}).selectOption('none');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'UI 本地 MCP',exact:true})});
    await card.getByRole('button',{name:'发现工具'}).click();
    await expect(page.locator('#mcp-result-hint')).toContainText('发现 1 个工具');
    await page.locator('[name="mcp-tool"]').check();await page.getByRole('button',{name:'导入选中工具'}).click();
    await expect(page.locator('#mcp-discovery')).not.toBeVisible();await expect(card).toContainText('1 个已导入工具');
    const data=await (await request.get('/api/ai/bootstrap')).json();
    const connection=data.mcp.find(c=>c.name==='UI 本地 MCP'),tool=data.tools.find(t=>t.connection_id===connection.id);
    expect(tool.kind).toBe('mcp');expect(tool.approval).toBe('confirm');
    await page.screenshot({path:'test-results/mcp-connections.png',fullPage:true});
    await page.getByRole('button',{name:'AI 中心',exact:true}).click();await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
    await page.getByRole('tab',{name:'工具与权限',exact:true}).click();
    const imported=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:tool.name,exact:true})});
    await imported.getByRole('button',{name:'编辑',exact:true}).click();
    await expect(form).toContainText('远程工具：maps_weather');
    await form.getByLabel('执行策略',{exact:true}).selectOption('auto');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    const placeholder=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'treemind_mindmap',exact:true})});
    await placeholder.getByRole('button',{name:'编辑',exact:true}).click();
    await expect(form.getByLabel('工具用途',{exact:true})).toHaveValue('mindmap');
    await expect(form.getByLabel('启用工具',{exact:true})).not.toBeChecked();
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});

test('工作台：核验联系人、监听消息、手动发送、暂停和预留接口',async({page,request})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await expect.poll(async()=> (await request.get('/api/status')).json().then(s=>s.state)).toBe('ready');
  await page.goto('/');
  await expect(page.getByText('演示环境',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'添加联系人',exact:true}).click();
  await page.getByLabel('完整微信备注',{exact:true}).fill('林晓');
  await page.getByLabel('联系人微信号',{exact:true}).fill('mismatch');
  await page.getByRole('button',{name:'核验并添加'}).click();
  await expect(page.locator('#contact-error')).toContainText('微信号与填写的信息不一致');
  await page.getByLabel('联系人微信号',{exact:true}).fill('demo_linxiao');
  await page.getByRole('button',{name:'核验并添加'}).click();
  await expect(page.locator('#chat-name')).toHaveText('林晓');
  await expect(page.locator('#listener-detail')).toHaveText('正在监听新消息');
  const contacts=await (await request.get('/api/contacts')).json(),cid=contacts.find(c=>c.lookup==='林晓').id;
  for(const text of ['你好，今天的需求整理好了吗？','你好，今天的需求整理好了吗？','后面想通过微信提交一些简单任务。'])
    await request.post(`/api/demo/${cid}/incoming`,{headers,data:{text}});
  await expect(page.locator('.message-bubble')).toHaveCount(3);
  await page.getByLabel('消息内容').fill('可以的，我先把消息监听和聊天工作台准备好。');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('.message-meta').last()).toContainText('已核对');
  await page.getByLabel('消息内容').fill('收到');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('.message-row.outgoing')).toHaveCount(2);
  await page.getByLabel('消息内容').fill('收到');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('.message-row.outgoing')).toHaveCount(3);
  await page.getByLabel('会话模式',{exact:true}).selectOption('listen_only');
  await expect(page.getByLabel('消息内容')).toBeDisabled();
  await page.getByLabel('会话模式',{exact:true}).selectOption('manual');
  await page.getByRole('button',{name:'全部暂停',exact:true}).click();
  await expect(page.getByLabel('消息内容')).toBeDisabled();
  await page.getByRole('button',{name:'恢复运行',exact:true}).click();
  await expect(page.getByLabel('消息内容')).toBeEnabled();
  await expect.poll(async()=> (await request.get('/api/contacts')).json().then(cs=>cs.find(c=>c.id===cid).listener_state)).toBe('listening');
  const injected=await request.post(`/api/demo/${cid}/incoming`,{headers,data:{text:'<img src=x onerror="alert(1)">这是一条纯文本消息'}});
  expect(injected.ok()).toBe(true);
  await expect(page.locator('.message-bubble').last()).toContainText('<img src=x');
  expect(await page.locator('.message-bubble img').count()).toBe(0);
  const second=await (await request.post('/api/contacts',{headers,data:{lookup:'陈屿',wechat_id:'demo_chenyu'}})).json();
  await request.patch(`/api/contacts/${second.id}`,{headers,data:{listening:true}});
  await expect(page.getByRole('button',{name:'打开 陈屿 的聊天'})).toBeVisible();
  await page.screenshot({path:'test-results/workbench-desktop.png',fullPage:true});
  // A slow send response must not erase a NEW draft typed before switching chats.
  await page.route(`**/api/contacts/${cid}/messages`,async route=>{
    if(route.request().method()==='POST'){
      const response=await route.fetch();
      await new Promise(resolve=>setTimeout(resolve,500));
      await route.fulfill({response});
    }else await route.continue();
  });
  await page.getByLabel('消息内容').fill('发送请求仍在处理中');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.getByLabel('消息内容').fill('这条新草稿需要保留');
  await page.getByRole('button',{name:'打开 陈屿 的聊天'}).click();
  await expect.poll(async()=>await page.request.get(`/api/contacts/${cid}/messages`).then(r=>r.json()).then(xs=>xs.at(-1).status)).toBe('confirmed');
  await page.waitForTimeout(650);
  await page.getByRole('button',{name:'打开 林晓 的聊天'}).click();
  await expect(page.getByLabel('消息内容')).toHaveValue('这条新草稿需要保留');
  await page.unroute(`**/api/contacts/${cid}/messages`);
  await page.getByLabel('消息内容').fill('');
  await page.getByRole('button',{name:'Agent 接口',exact:true}).click();
  await expect(page.locator('.endpoint')).toContainText('/api/agent/events');
  await page.getByRole('button',{name:'联系人管理',exact:true}).click();
  await page.getByRole('button',{name:'移除 陈屿',exact:true}).click();
  await page.getByRole('button',{name:'移除联系人',exact:true}).click();
  await expect(page.locator('#contacts-table')).not.toContainText('陈屿');
  expect(errors).toEqual([]);
});

test('窄屏布局能返回会话列表并发送消息',async({page})=>{
  await page.goto('/');
  await page.getByRole('button',{name:'打开 林晓 的聊天',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#chat-name')).toHaveText('林晓');
  await page.locator('#chat-avatar').click();
  await expect(page.locator('.conversations')).toBeVisible();
  await page.getByRole('button',{name:'打开 林晓 的聊天'}).click();
  await page.getByLabel('消息内容').fill('窄屏操作验证');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('.message-meta').last()).toContainText('已核对');
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({path:'test-results/workbench-mobile.png',fullPage:true});
});

test('AI 中心：模型、知识、流程、辅助回复、外部 Agent 与独立联系人绑定',async({page,request})=>{
  test.setTimeout(90000);
  page.setDefaultTimeout(10000);
  const http=require('http'),modelRequests=[],externalRequests=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const server=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    let result;
    if(req.url==='/agent'){externalRequests.push(body);result={reply:'财务 Agent 已收到：'+body.message};}
    else{modelRequests.push(body);result={choices:[{message:{role:'assistant',content:'这是电商 Agent 的模拟回复'}}]};}
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  try{
    const contacts=await (await request.get('/api/contacts')).json();
    if(!contacts.some(c=>c.lookup==='林晓')){
      const contact=await (await request.post('/api/contacts',{headers,data:{lookup:'林晓',wechat_id:'ai-ui-test'}})).json();
      await request.patch('/api/contacts/'+contact.id,{headers,data:{listening:true,mode:'manual'}});
    }
    await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.locator('#ai-global-settings').click();
    await page.locator('#ai-create').click();
    const form=page.locator('#ai-editor-form');
    await form.getByLabel('名称',{exact:true}).fill('测试模型连接');
    await form.getByLabel('接口地址（Base URL）').fill(base+'/v1');
    await form.getByLabel('模型 ID',{exact:true}).fill('local-test-model');
    await form.getByLabel('API Key',{exact:true}).fill('ui-secret-key');
    await form.getByRole('button',{name:'保存配置'}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    await expect(page.locator('#ai-library')).toContainText('密钥已加密保存');
    await page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'测试模型连接',exact:true})}).getByRole('button',{name:'测试连接',exact:true}).click();
    await expect(page.locator('#toast')).toContainText('电商 Agent');
    await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.getByRole('tab',{name:'知识库',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('商品与售后');
    await page.locator('#ai-import-files').setInputFiles({name:'售后.md',mimeType:'text/markdown',buffer:Buffer.from('保温杯支持七天退货，运费规则以订单为准。')});
    await expect(page.locator('#ai-documents')).toContainText('资料正文');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.getByRole('tab',{name:'我的 Agent',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('电商 Agent');
    await form.getByLabel('模型连接',{exact:true}).selectOption({label:'测试模型连接'});
    await expect(page.getByRole('tab',{name:'回复风格',exact:true})).toHaveCount(0);
    await expect(form.getByLabel('回复风格',{exact:true})).toHaveCount(0);
    await form.getByLabel('此 Agent 的职责与规则').fill('负责商品与售后咨询。');
    await page.locator('#agent-tab-memory').click();await form.locator('summary').filter({hasText:'参考资料'}).click();
    await form.getByLabel('商品与售后',{exact:true}).check();
    await form.getByLabel('知识检索方式').selectOption('always');
    await page.locator('#agent-tab-abilities').click();await page.getByLabel('查找工具或技能',{exact:true}).fill('计算器');await form.getByLabel('计算器',{exact:true}).check();
    await page.locator('#agent-tab-advanced').click();
    await page.getByRole('button',{name:'添加步骤',exact:true}).click();
    await page.getByLabel('步骤 3 类型').selectOption('tool');
    await form.getByLabel('执行工具（需在工具与技能中授权）').selectOption({label:'calculate'});
    await form.getByLabel('工具参数 JSON',{exact:true}).fill('{"expression":"6*7"}');
    await page.getByRole('button',{name:'上移步骤 3',exact:true}).click();
    await expect(page.getByLabel('步骤 2 类型')).toHaveValue('tool');
    await page.screenshot({path:'test-results/ai-agent-editor.png',fullPage:true});
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'电商 Agent',exact:true})}).getByRole('button',{name:'测试 Agent',exact:true}).click();
    await page.getByLabel('测试消息',{exact:true}).fill('保温杯可以退货吗？');await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result .ai-run-heading .pill')).toHaveText('已完成',{timeout:20000});
    await expect(page.locator('#ai-test-result')).toContainText('知识检索');
    await expect(page.locator('#ai-test-result')).toContainText('工具结果');
    expect(modelRequests.at(-1).messages.some(m=>m.content.includes('七天退货'))).toBe(true);
    expect(modelRequests.at(-1).messages.some(message=>message.content.includes('42'))).toBe(true);
    await page.getByRole('button',{name:'关闭 Agent 测试',exact:true}).click();
    await editEngineFixture(page,request,{name:'财务 Agent',kind:'external',url:base+'/agent'});
    await page.locator('#agent-tab-advanced').click();await form.getByLabel('外部 Agent 地址').fill(base+'/agent');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.screenshot({path:'test-results/ai-center-desktop.png',fullPage:true});
    const settings=(await (await request.get('/api/ai/bootstrap')).json()).settings;
    await request.put('/api/ai/settings',{headers,data:{...settings,enabled:true}});
    await page.getByRole('button',{name:'聊天工作台',exact:true}).click();
    await page.getByLabel('选择联系人 Agent').selectOption({label:'电商 Agent'});
    await page.getByLabel('会话模式',{exact:true}).selectOption('ai_assist');
    const a=(await (await request.get('/api/contacts')).json()).find(c=>c.lookup==='林晓');
    await expect(page.locator('#chat-mode')).toHaveText('AI 辅助回复');
    await request.post(`/api/demo/${a.id}/incoming`,{headers,data:{text:'本次商品退货咨询'}});
    await expect(page.locator('#ai-conversation')).toContainText('回复待确认',{timeout:15000});
    await page.getByRole('button',{name:'编辑并发送',exact:true}).click();
    await page.locator('#ai-draft-text').fill('编辑后的客服回复');await page.locator('#ai-run-detail .ai-run-input').click();
    await page.waitForTimeout(2300);await expect(page.locator('#ai-draft-text')).toHaveValue('编辑后的客服回复');
    await page.getByRole('button',{name:'确认并发送微信',exact:true}).click();
    await expect(page.locator('#ai-run-detail')).toContainText('微信已核对');
    await page.getByRole('button',{name:'关闭运行详情',exact:true}).click();
    await expect(page.locator('.message-bubble').last()).toHaveText('编辑后的客服回复');
    await expect(page.locator('.message-meta').last()).toContainText('AI 回复');
    const config=await (await request.get('/api/ai/bootstrap')).json();
    expect(JSON.stringify(config)).not.toContain('ui-secret-key');
    const finance=config.agents.find(x=>x.name==='财务 Agent');
    const b=await (await request.post('/api/contacts',{headers,data:{lookup:'财务联系人',wechat_id:'finance-test'}})).json();
    await request.patch(`/api/contacts/${b.id}`,{headers,data:{ai_agent_id:finance.id,mode:'ai_auto',listening:true}});
    await expect.poll(async()=> (await request.get('/api/contacts')).json().then(cs=>cs.find(c=>c.id===b.id).listener_state)).toBe('listening');
    await request.post(`/api/demo/${b.id}/incoming`,{headers,data:{text:'本月账单核对'}});
    await expect.poll(async()=> (await request.get('/api/ai/runs?contact_id='+b.id)).json().then(rs=>rs[0]?.status),{timeout:15000}).toBe('sent');
    expect(externalRequests.at(-1).contact.wechat_id).toBe('finance-test');
    expect(JSON.stringify(externalRequests.at(-1).conversation)).not.toContain('商品退货咨询');
    await page.getByLabel('选择联系人 Agent').selectOption({label:'财务 Agent'});
    await expect(page.locator('#contact-agent-hint')).toContainText('财务 Agent');
    await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
    await page.screenshot({path:'test-results/ai-center-mobile.png',fullPage:true});
    expect(errors).toEqual([]);
    await request.put('/api/ai/settings',{headers,data:{...config.settings,enabled:false}});
    await request.patch(`/api/contacts/${a.id}`,{headers,data:{mode:'manual'}});
    await request.delete(`/api/contacts/${b.id}`,{headers});
  }finally{await new Promise(resolve=>server.close(resolve));}
});

test('标准 Skill：导入、绑定、加载并调用已授权工具',async({page,request})=>{
  test.setTimeout(90000);const http=require('http');
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const chunk of req)body+=chunk;
    const m=JSON.parse(body).messages,outputs=m.filter(x=>x.role==='tool');let reply;
    const call=(name,args,id)=>({role:'assistant',content:'',tool_calls:[{id,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
    if(!outputs.length)reply=call('use_skill',{name:'ui-calculate-skill'},'load-skill');
    else if(outputs.length===1)reply=call('calculate',{expression:'7*8'},'calculate');
    else reply={role:'assistant',content:'按照技能计算，结果是 56。'};
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:reply,finish_reason:'stop'}]}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const p=await (await request.post('/api/ai/config/providers',{headers,data:{name:'Skill 测试模型',base_url:`http://127.0.0.1:${server.address().port}`,model:'skill-test'}})).json();
    await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.getByRole('tab',{name:'扩展中心',exact:true}).click();await page.getByRole('tab',{name:'Skills',exact:true}).click();await page.locator('#ai-create').click();
    await page.locator('#skill-import-file').setInputFiles({name:'SKILL.md',mimeType:'text/markdown',buffer:Buffer.from('---\nname: ui-calculate-skill\ndescription: 使用工具完成乘法\n---\n请调用 calculate 计算 7*8，然后回复结果。')});
    const form=page.locator('#ai-editor-form');await expect(form.getByLabel('名称',{exact:true})).toHaveValue('ui-calculate-skill');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.getByRole('tab',{name:'我的 Agent',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('Skill 执行测试');await form.getByLabel('模型连接',{exact:true}).selectOption(p.id);
    await page.locator('#agent-tab-abilities').click();await page.getByLabel('查找工具或技能',{exact:true}).fill('ui-calculate-skill');
    await form.getByLabel('ui-calculate-skill',{exact:true}).check();await page.getByLabel('查找工具或技能',{exact:true}).fill('计算器');await form.getByLabel('计算器',{exact:true}).check();
    await expect(form.getByLabel('知识检索方式')).toHaveValue('on_demand');
    await expect(form.getByLabel('联网检索策略')).toHaveValue('auto');
    await page.locator('#agent-tab-advanced').click();await page.locator('#agent-network-policy > summary').click();
    await form.getByLabel('联网检索策略').selectOption('model');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    const savedAgents=(await (await request.get('/api/ai/bootstrap')).json()).agents;
    expect(savedAgents.find(a=>a.name==='Skill 执行测试').web_policy).toBe('model');
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'Skill 执行测试',exact:true})});
    await card.getByRole('button',{name:'测试 Agent'}).click();await page.getByLabel('测试消息',{exact:true}).fill('使用乘法技能');await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result .ai-run-heading .pill')).toHaveText('已完成',{timeout:15000});
    await expect(page.locator('#ai-test-result')).toContainText('加载技能');
    await expect(page.locator('#ai-test-result')).not.toContainText('知识检索');
    await expect(page.locator('#ai-test-reply')).toHaveValue('按照技能计算，结果是 56。');
    await page.screenshot({path:'test-results/skill-execution.png',fullPage:true});
  }finally{await new Promise(resolve=>server.close(resolve));}
});

test('陪伴 Agent：角色配置、连续记忆、遗忘和关怀日程',async({page,request})=>{
  test.setTimeout(90000);
  const http=require('http');
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const chunk of req)body+=chunk;
    const payload=JSON.parse(body),analysis=payload.messages[0].content.includes('只输出 JSON');
    const reply=analysis?JSON.stringify({mood:'开心',risk:'none',summary:'用户的猫叫芝麻',facts:[{key:'宠物名',content:'猫叫芝麻',category:'fact',evidence:'芝麻'}]}):'芝麻这个名字好可爱，我记住啦。';
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{role:'assistant',content:reply},finish_reason:'stop'}]}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const p=await (await request.post('/api/ai/config/providers',{headers,data:{name:'陪伴测试模型',base_url:`http://127.0.0.1:${server.address().port}`,model:'companion-test'}})).json();
    await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.locator('#ai-create').click();
    const form=page.locator('#ai-editor-form');
    await form.getByLabel('名称',{exact:true}).fill('阿慧 · 甜心陪伴');
    await form.getByLabel('模型连接',{exact:true}).selectOption(p.id);
    await form.getByLabel('此 Agent 的职责与规则').fill('你是阿慧，温柔自然地聊天，记住用户明确告诉你的事实。');
    await page.locator('#agent-tab-memory').click();
    await form.locator('summary').filter({hasText:'三层长期记忆'}).click();
    await form.getByLabel('启用长期记忆',{exact:true}).check();
    await page.locator('#agent-tab-advanced').click();await page.locator('#agent-network-policy > summary').click();
    await form.getByLabel('参考链接展示',{exact:true}).selectOption('requested');
    await form.getByRole('button',{name:'保存配置',exact:true}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const savedCompanion=(await (await request.get('/api/ai/bootstrap')).json()).agents.find(a=>a.name==='阿慧 · 甜心陪伴');
    expect(savedCompanion.companion.memory.enabled).toBe(true);
    expect(savedCompanion.source_policy).toBe('requested');
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'阿慧 · 甜心陪伴',exact:true})});
    await card.getByRole('button',{name:'测试 Agent'}).click();
    await page.getByLabel('测试用户已自我声明成年').check();
    await page.getByLabel('测试消息',{exact:true}).fill('我家猫叫芝麻');
    await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result .ai-run-heading .pill')).toHaveText('已完成',{timeout:15000});
    await page.getByRole('button',{name:'关闭 Agent 测试'}).click();
    await card.getByRole('button',{name:'记忆与档案',exact:true}).click();
    const agents=(await (await request.get('/api/ai/bootstrap')).json()).agents;
    await page.getByLabel('记忆所属 Agent').selectOption(agents.find(a=>a.name==='阿慧 · 甜心陪伴').id);
    await page.getByRole('button',{name:'读取记忆与档案'}).click();
    await expect(page.locator('.memory-card')).toContainText('猫叫芝麻');
    await page.screenshot({path:'test-results/companion-memory.png',fullPage:true});
    await page.getByLabel('喜欢的称呼').fill('小星');
    await page.getByRole('button',{name:'保存用户档案'}).click();
    await expect(page.locator('#toast')).toContainText('用户档案已保存');
    page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'遗忘此用户全部记忆'}).click();
    await expect(page.locator('.memory-card')).toHaveCount(0);
    await page.getByRole('tab',{name:'自动任务'}).click();
    await page.getByRole('tab',{name:'联系人关怀',exact:true}).click();
    await page.getByRole('button',{name:'添加关怀日程'}).click();
    await expect(page.getByLabel('Cron 表达式（重复）')).toHaveValue('0 9 * * *');
    await page.locator('#companion-modal').getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('button',{name:'设置',exact:true}).click();await page.getByRole('tab',{name:'MCP',exact:true}).click();
    await page.getByRole('tab',{name:'服务配置',exact:true}).click();await page.getByRole('button',{name:'添加服务',exact:true}).click();
    await page.getByLabel('能力类别',{exact:true}).selectOption('tts');
    await page.getByLabel('接入协议',{exact:true}).selectOption('minimax');
    await page.getByLabel('名称',{exact:true}).fill('MiniMax 待配置');
    await page.getByLabel('服务地址 / 网关完整地址').fill('http://127.0.0.1:9900');
    await page.getByLabel('默认音色 ID').fill('fixed-voice');
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await expect(page.locator('.ai-resource-card').filter({hasText:'MiniMax 待配置'})).toBeVisible();
    await page.screenshot({path:'test-results/companion-services.png',fullPage:true});
  }finally{await new Promise(resolve=>server.close(resolve));}
});
