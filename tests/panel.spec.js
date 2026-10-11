const { test, expect } = require('@playwright/test');
const headers={'X-Panel-Request':'1'};

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
    await page.getByRole('tab',{name:'MCP 连接',exact:true}).click();
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
    await page.getByRole('button',{name:'新建MCP 连接',exact:true}).click();
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
    await page.getByRole('tab',{name:'工具',exact:true}).click();
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
  await page.getByRole('button',{name:'添加第一位联系人'}).click();
  await page.getByLabel('完整微信备注',{exact:true}).fill('林晓');
  await page.getByLabel('联系人微信号',{exact:true}).fill('mismatch');
  await page.getByRole('button',{name:'核验并添加'}).click();
  await expect(page.locator('#contact-error')).toContainText('微信号与填写的信息不一致');
  await page.getByLabel('联系人微信号',{exact:true}).fill('demo_linxiao');
  await page.getByRole('button',{name:'核验并添加'}).click();
  await expect(page.locator('#chat-name')).toHaveText('林晓');
  await expect(page.locator('#listener-detail')).toHaveText('正在监听新消息');
  const contacts=await (await request.get('/api/contacts')).json(),cid=contacts[0].id;
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
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
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
    await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();
    await page.getByRole('tab',{name:'模型连接',exact:true}).click();
    await page.locator('#ai-create').click();
    const form=page.locator('#ai-editor-form');
    await form.getByLabel('名称',{exact:true}).fill('测试模型连接');
    await form.getByLabel('接口地址（Base URL）').fill(base+'/v1');
    await form.getByLabel('模型 ID',{exact:true}).fill('local-test-model');
    await form.getByLabel('API Key',{exact:true}).fill('ui-secret-key');
    await form.getByRole('button',{name:'保存配置'}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    await expect(page.locator('#ai-library')).toContainText('密钥已加密保存');
    await page.getByRole('button',{name:'测试连接',exact:true}).click();
    await expect(page.locator('#toast')).toContainText('电商 Agent');
    await page.getByRole('tab',{name:'知识库',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('商品与售后');
    await page.locator('#ai-import-files').setInputFiles({name:'售后.md',mimeType:'text/markdown',buffer:Buffer.from('保温杯支持七天退货，运费规则以订单为准。')});
    await expect(page.locator('#ai-documents')).toContainText('资料正文');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.getByRole('tab',{name:'Agent',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('电商 Agent');
    await form.getByLabel('模型连接',{exact:true}).selectOption({label:'测试模型连接'});
    await form.getByLabel('回复风格',{exact:true}).selectOption({label:'电商客服'});
    await form.getByLabel('此 Agent 的职责与规则').fill('负责商品与售后咨询。');
    await form.getByLabel('商品与售后',{exact:true}).check();await form.getByLabel('calculate',{exact:true}).check();
    await form.getByLabel('知识检索方式').selectOption('always');
    await page.getByRole('button',{name:'添加步骤',exact:true}).click();
    await page.getByLabel('步骤 3 类型').selectOption('tool');
    await form.getByLabel('执行工具（需同时在上方勾选授权）').selectOption({label:'calculate'});
    await form.getByLabel('工具参数 JSON',{exact:true}).fill('{"expression":"6*7"}');
    await page.getByRole('button',{name:'上移步骤 3',exact:true}).click();
    await expect(page.getByLabel('步骤 2 类型')).toHaveValue('tool');
    await page.screenshot({path:'test-results/ai-agent-editor.png',fullPage:true});
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.getByRole('button',{name:'测试 Agent',exact:true}).click();
    await page.getByLabel('测试消息',{exact:true}).fill('保温杯可以退货吗？');await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result')).toContainText('测试完成');
    await expect(page.locator('#ai-test-result')).toContainText('知识检索');
    await expect(page.locator('#ai-test-result')).toContainText('工具结果');
    expect(modelRequests.at(-1).messages.some(m=>m.content.includes('七天退货'))).toBe(true);
    expect(modelRequests.at(-1).messages.at(-1).content).toContain('42');
    await page.getByRole('button',{name:'关闭 Agent 测试',exact:true}).click();
    await page.locator('#ai-create').click();await form.getByLabel('名称',{exact:true}).fill('财务 Agent');
    await form.getByLabel('Agent 来源').selectOption('external');await form.getByLabel('外部 Agent 地址').fill(base+'/agent');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.screenshot({path:'test-results/ai-center-desktop.png',fullPage:true});
    await page.getByRole('switch',{name:'启用 AI 回复',exact:true}).click();
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
    await page.getByRole('tab',{name:'Skill 技能',exact:true}).click();await page.locator('#ai-create').click();
    await page.locator('#skill-import-file').setInputFiles({name:'SKILL.md',mimeType:'text/markdown',buffer:Buffer.from('---\nname: ui-calculate-skill\ndescription: 使用工具完成乘法\n---\n请调用 calculate 计算 7*8，然后回复结果。')});
    const form=page.locator('#ai-editor-form');await expect(form.getByLabel('名称',{exact:true})).toHaveValue('ui-calculate-skill');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    await page.getByRole('tab',{name:'Agent',exact:true}).click();await page.locator('#ai-create').click();
    await form.getByLabel('名称',{exact:true}).fill('Skill 执行测试');await form.getByLabel('模型连接',{exact:true}).selectOption(p.id);
    await form.getByLabel('ui-calculate-skill',{exact:true}).check();await form.getByLabel('calculate',{exact:true}).check();
    await expect(form.getByLabel('知识检索方式')).toHaveValue('on_demand');
    await expect(form.getByLabel('联网检索策略')).toHaveValue('auto');
    await form.getByLabel('联网检索策略').selectOption('model');
    await form.getByRole('button',{name:'保存配置'}).click();await expect(page.locator('#ai-editor')).not.toBeVisible();
    const savedAgents=(await (await request.get('/api/ai/bootstrap')).json()).agents;
    expect(savedAgents.find(a=>a.name==='Skill 执行测试').web_policy).toBe('model');
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'Skill 执行测试',exact:true})});
    await card.getByRole('button',{name:'测试 Agent'}).click();await page.getByLabel('测试消息',{exact:true}).fill('使用乘法技能');await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result')).toContainText('测试完成',{timeout:15000});
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
    await page.getByRole('button',{name:'创建阿慧模板'}).click();
    await page.getByLabel('使用的文本模型').selectOption(p.id);
    await page.getByRole('button',{name:'创建 Agent',exact:true}).click();
    const card=page.locator('.ai-resource-card').filter({has:page.getByRole('heading',{name:'阿慧 · 甜心陪伴',exact:true})});
    await expect(card).toBeVisible();await card.getByRole('button',{name:'编辑',exact:true}).click();
    await page.getByLabel('角色名字',{exact:true}).fill('阿慧');
    await expect(page.getByLabel('自然变化回复节奏',{exact:true})).toBeChecked();
    await page.getByLabel('Emoji 使用倾向（0～1）',{exact:true}).fill('0.15');
    await page.getByLabel('参考链接展示',{exact:true}).selectOption('requested');
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await expect(page.locator('#ai-editor')).not.toBeVisible();
    const savedCompanion=(await (await request.get('/api/ai/bootstrap')).json()).agents.find(a=>a.name==='阿慧 · 甜心陪伴');
    expect(savedCompanion.companion.persona.emoji_probability).toBe(.15);
    expect(savedCompanion.source_policy).toBe('requested');
    await card.getByRole('button',{name:'测试 Agent'}).click();
    await page.getByLabel('测试用户已自我声明成年').check();
    await page.getByLabel('测试消息',{exact:true}).fill('我家猫叫芝麻');
    await page.getByRole('button',{name:'开始测试',exact:true}).click();
    await expect(page.locator('#ai-test-result')).toContainText('测试完成',{timeout:15000});
    await page.getByRole('button',{name:'关闭 Agent 测试'}).click();
    await page.getByRole('tab',{name:'记忆与档案'}).click();
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
    await page.getByRole('tab',{name:'主动关怀'}).click();
    await page.getByRole('button',{name:'添加关怀日程'}).click();
    await expect(page.getByLabel('Cron 表达式（重复）')).toHaveValue('0 9 * * *');
    await page.locator('#companion-modal').getByRole('button',{name:'关闭',exact:true}).click();
    await page.getByRole('tab',{name:'能力连接'}).click();
    await page.getByRole('button',{name:'新建能力连接'}).click();
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
