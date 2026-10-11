/* Capture real UI with synthetic data in an isolated demo process. */
const {chromium}=require('@playwright/test');
const {spawn}=require('node:child_process');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const port=8781,base=`http://127.0.0.1:${port}`;
const output=path.join(root,'docs','assets');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(fn,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){const value=await fn();if(value)return value;await pause(200);}throw Error('Demo operation timed out');}
async function api(route,body,method=body===undefined?'GET':'POST'){
  const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json','X-Panel-Request':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok)throw Error(`${route}: ${response.status} ${await response.text()}`);
  return response.status===204?null:response.json();
}

(async()=>{
  fs.mkdirSync(output,{recursive:true});fs.mkdirSync(path.join(root,'.panel-demo-data'),{recursive:true});
  const dataDir=fs.mkdtempSync(path.join(root,'.panel-demo-data','readme-'));
  const model=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw),analysis=body.messages[0].content.includes('只输出 JSON');
    const content=analysis?JSON.stringify({mood:'期待',summary:'林晓的猫叫芝麻，周末想去公园散步。',risk:'none',needs_web:false,facts:[{key:'宠物名',content:'猫叫芝麻',category:'fact',evidence:'芝麻'}]}):'记住啦，芝麻是你家的小猫。周末去公园走走也不错，工作先放一放，好好充个电～';
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{role:'assistant',content},finish_reason:'stop'}]}));
  });
  await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
  // Refuse to connect to an already running process on our documentation port.
  try{await fetch(base+'/api/status');throw Error('Documentation port is in use');}catch(error){if(error.message==='Documentation port is in use')throw error;}
  const python=path.join(root,'pywechat-main','.venv','Scripts','python.exe');
  const proc=spawn(python,['-m','wechat_panel','--demo','--no-browser','--port',String(port),'--data-dir',dataDir],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'},stdio:'ignore'});
  let browser;
  try{
    await waitFor(async()=>{try{const s=await api('/api/status');return s.demo&&s.state==='ready';}catch{return false;}});
    const provider=await api('/api/ai/config/providers',{name:'本地演示模型',base_url:`http://127.0.0.1:${model.address().port}`,model:'demo-chat'});
    const knowledge=await api('/api/ai/config/knowledge',{name:'产品与售后手册',description:'商品说明、配送及退换货规则',documents:[{title:'售后服务',content:'示例商品支持七天退换，具体以订单约定为准。售后处理前需要核对订单号。'}]});
    const skill=await api('/api/ai/config/skills',{name:'weekly-planner',description:'梳理一周目标，拆分任务并识别需要确认的安排。',instructions:'# 周计划助手\n先确认目标，再拆分为三项可执行任务。需要查询时使用已授权工具；没有实际调用时不要声称查过。',files:[]});
    const boot=await api('/api/ai/bootstrap');
    const basic=boot.tools.filter(t=>['clock','calculator','knowledge_search'].includes(t.builtin)).map(t=>t.id);
    const commerce=await api('/api/ai/config/agents',{name:'电商客服',description:'商品咨询、售后说明与订单协助。依据绑定资料回答。',provider_id:provider.id,style_id:boot.styles.find(s=>s.name==='电商客服').id,knowledge_ids:[knowledge.id],tool_ids:basic});
    const finance=await api('/api/ai/config/agents',{name:'财务助手',description:'整理收支、核对金额、解释账目，重要操作交由你确认。',provider_id:provider.id,style_id:boot.styles.find(s=>s.name==='财务助手').id,tool_ids:basic});
    const companion=await api('/api/ai/companion/template',{provider_id:provider.id});
    const contacts=[];
    for(const [lookup,wechat_id] of [['林晓','demo_linxiao'],['周然 · 电商咨询','demo_zhou'],['陈宇 · 财务协作','demo_chen']])contacts.push(await api('/api/contacts',{lookup,wechat_id}));
    for(const c of contacts)await api('/api/contacts/'+c.id,{listening:true},'PATCH');
    await waitFor(async()=> (await api('/api/contacts')).every(c=>c.listener_state==='listening'));
    const cid=contacts[0].id;
    const incoming=async text=>{await api(`/api/demo/${cid}/incoming`,{text});await waitFor(async()=> (await api(`/api/contacts/${cid}/messages`)).some(m=>m.text===text&&m.direction==='incoming'));};
    const outgoing=async text=>{await api(`/api/contacts/${cid}/messages`,{text,client_key:crypto.randomUUID()});await waitFor(async()=> (await api(`/api/contacts/${cid}/messages`)).some(m=>m.text===text&&m.status==='confirmed'));};
    await incoming('今天终于把方案交完了，有点累，不过很开心。');
    await outgoing('辛苦啦，先给自己一个小小的休息。今天最满意的是哪一部分？');
    await incoming('配色终于调到了喜欢的感觉，周末想带猫出去晒晒太阳。');
    await outgoing('听起来很棒，慢慢走，享受一点不用赶进度的时间。');
    for(const [c,a,mode] of [[contacts[0],companion,'ai_auto'],[contacts[1],commerce,'ai_assist'],[contacts[2],finance,'ai_assist']])await api('/api/contacts/'+c.id,{ai_agent_id:a.id,mode},'PATCH');
    await api('/api/ai/settings',{enabled:true,debounce_seconds:.3,context_messages:20},'PUT');
    await incoming('记住啦，我的猫叫芝麻。周末想去公园走走。');
    await waitFor(async()=> (await api('/api/ai/runs')).some(r=>r.status==='sent'));
    const scope={agent_id:companion.id,contact_id:cid};
    await api('/api/ai/companion/profile',{...scope,profile:{display_name:'林晓',preferred_name:'小林',city:'杭州',memory_consent:true,age_status:'adult'}},'PUT');
    for(const memory of [{key:'饮食偏好',category:'preference',content:'喜欢清淡的食物，咖啡偏爱拿铁。',pinned:true},{key:'周末计划',category:'event',content:'想去公园散步，给自己留一点休息时间。'}])await api('/api/ai/companion/memories',{...scope,memory});
    browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
    const page=await browser.newPage({viewport:{width:1560,height:1040},deviceScaleFactor:1,locale:'zh-CN',timezoneId:'Asia/Shanghai'});
    await page.goto(base);await page.getByText('林晓',{exact:true}).first().click();
    await page.locator('#chat-name').filter({hasText:'林晓'}).waitFor();
    await waitFor(async()=>await page.locator('.message-bubble').count()>=6);
    const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(output,name),animations:'disabled'});};
    await shot('workspace.png');
    const focusLibrary=async()=>{await page.locator('#ai-tabs').evaluate(el=>el.scrollIntoView({block:'start'}));await pause(250);};
    await page.getByRole('button',{name:'AI 中心',exact:true}).click();await page.getByRole('heading',{name:'阿慧 · 甜心陪伴',exact:true}).waitFor();
    await waitFor(async()=>await page.locator('#ai-summary>div').nth(1).locator('strong').textContent()==='3');
    await page.setViewportSize({width:1560,height:1200});await shot('agents.png');
    await page.getByRole('tab',{name:'记忆与档案',exact:true}).click();
    await page.getByLabel('记忆所属 Agent').selectOption(companion.id);
    await page.getByLabel('记忆所属用户').selectOption(cid);
    await page.getByRole('button',{name:'读取记忆与档案'}).click();
    await page.locator('.memory-card').first().waitFor();
    await page.locator('#companion-profile summary').click();await page.locator('#memory-select').evaluate(el=>el.scrollIntoView({block:'start'}));await pause(250);await shot('memory.png');
    await page.setViewportSize({width:1560,height:1040});
    await page.getByRole('tab',{name:'Skill 技能',exact:true}).click();await page.getByRole('heading',{name:skill.name,exact:true}).waitFor();
    await shot('skills.png');
    await page.getByRole('tab',{name:'MCP 连接',exact:true}).click();await page.getByRole('heading',{name:'高德地图 MCP',exact:true}).waitFor();await shot('mcp.png');
    await page.getByRole('tab',{name:'运行记录',exact:true}).click();await page.locator('.ai-run-row').first().click();await page.locator('#ai-run-dialog').waitFor();await shot('run-details.png');
    console.log('Captured six demo screenshots in docs/assets');
  }finally{
    if(browser)await browser.close();
    try{const s=await api('/api/status');if(s.demo)await api('/api/shutdown',{});}catch{}
    await pause(1500);if(proc.exitCode===null)proc.kill();
    model.closeAllConnections();await new Promise(resolve=>model.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
