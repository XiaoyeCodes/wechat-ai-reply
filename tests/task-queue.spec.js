const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

test('会话队列：展示当前任务、排队详情，单项取消后按序投递',async({page,request})=>{
 test.setTimeout(45000);
 const http=require('http'),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let calls=0,release;const gate=new Promise(resolve=>release=resolve);const resources=[];let cid;
 const server=http.createServer(async(req,res)=>{
   const chunks=[];for await(const chunk of req)chunks.push(chunk);
   const body=JSON.parse(Buffer.concat(chunks).toString());calls++;
   if(calls===1)await gate;
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'已完成：'+body.messages.at(-1).content}}]}));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const post=async(path,data)=>{const r=await request.post(path,{headers,data});expect(r.ok(),await r.text()).toBeTruthy();return r.json();};
 const create=async(kind,data)=>{const r=await post('/api/ai/config/'+kind,data);resources.push([kind,r.id]);return r;};
 try{
   const provider=await create('providers',{name:'队列界面假模型',model:'fixture',base_url:`http://127.0.0.1:${server.address().port}/v1`});
   const agent=await create('agents',{name:'排队助手',provider_id:provider.id,web_policy:'off',companion:{safety:{emotion_enabled:false,crisis_detection:false}}});
   const c=await post('/api/contacts',{lookup:'排队界面测试',wechat_id:'queue-ui-demo'});cid=c.id;
   expect((await request.patch('/api/contacts/'+cid,{headers,data:{listening:true,mode:'ai_auto',ai_agent_id:agent.id}})).ok()).toBeTruthy();
   expect((await request.put('/api/ai/settings',{headers,data:{enabled:true,debounce_seconds:.3,context_messages:20}})).ok()).toBeTruthy();
   await expect.poll(async()=> (await (await request.get('/api/contacts')).json()).find(x=>x.id===cid)?.listener_state).toBe('listening');
   await page.goto('/');await page.getByRole('button',{name:'打开 排队界面测试 的聊天',exact:true}).click();
   const runs=async()=> (await request.get('/api/ai/runs?contact_id='+cid)).json();
   await post('/api/demo/'+cid+'/incoming',{text:'任务一：先处理这一项'});
   await expect.poll(()=>calls,{timeout:15000}).toBe(1);
   await post('/api/demo/'+cid+'/incoming',{text:'任务二：随后处理'});await expect.poll(async()=> (await runs()).length).toBe(2);
   await post('/api/demo/'+cid+'/incoming',{text:'任务三：最后处理'});await expect.poll(async()=> (await runs()).length).toBe(3);
   const queue=page.locator('.ai-task-queue');await expect(queue).toContainText('后续 2 项排队');
   await expect(page.locator('#ai-conversation .ai-reply-notice')).toContainText('正在处理');
   await page.locator('#ai-conversation').screenshot({path:test.info().outputPath('conversation-queue.png')});
   const second=queue.locator(':scope > div').filter({hasText:'任务二'});await second.getByRole('button',{name:'取消',exact:true}).click();
   await expect(queue).toContainText('后续 1 项排队');
   await queue.getByRole('button',{name:'查看',exact:true}).click();
   await expect(page.locator('#ai-run-detail .ai-run-heading')).toContainText('排队中 · 前面 1 项');
   await page.locator('#ai-run-close').click();
   release();
   await expect.poll(async()=> (await runs()).filter(r=>r.status==='sent').length,{timeout:15000}).toBe(2);
   const result=await runs();expect(result.find(r=>r.input.startsWith('任务二')).status).toBe('cancelled');expect(calls).toBe(2);
   expect(errors).toEqual([]);
 }finally{
   release();await request.put('/api/ai/settings',{headers,data:{enabled:false}});
   if(cid)await request.delete('/api/contacts/'+cid,{headers});
   for(const [kind,id] of resources.reverse())await request.delete(`/api/ai/config/${kind}/${id}`,{headers});
   await new Promise(resolve=>server.close(resolve));
 }
});
