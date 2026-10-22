const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

test('长微信昵称在手机顶部截断，并保留完整账号提示',async({page,request})=>{
 const bootstrap=await (await request.get('/api/bootstrap')).json();
 const name='一个很长的微信昵称'.repeat(8);
 await page.route('**/api/bootstrap',route=>route.fulfill({json:{...bootstrap,contacts:[],
  status:{...bootstrap.status,account_id:'long-account',account_name:name}}}));
 await page.route('**/api/events?*',route=>route.abort());
 await page.setViewportSize({width:390,height:844});
 await page.goto('/');
 await expect(page.locator('#current-account')).toHaveText(name);
 await expect(page.locator('#current-account')).toHaveAttribute('title',/long-account/);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('切换登录账号清空旧会话，草稿只在原账号会话恢复',async({page,request})=>{
 const ids=[];
 try{
  for(const name of ['账号隔离好友甲','账号隔离好友乙']){
   const result=await request.post('/api/contacts',{headers,data:{lookup:name,wechat_id:name}});
   expect(result.ok(),await result.text()).toBeTruthy();ids.push((await result.json()).id);
  }
  const original=await (await request.get('/api/bootstrap')).json();
  const first=original.contacts.find(c=>c.id===ids[0]),second=original.contacts.find(c=>c.id===ids[1]);
  let snapshot={...original,contacts:[first]};
  await page.route('**/api/bootstrap',route=>route.fulfill({json:snapshot}));
  await page.goto('/');
  await expect(page.locator('#chat-name')).toHaveText(first.lookup);
  await page.locator('#message-input').fill('仅属于原账号的草稿');
  snapshot={...original,contacts:[],status:{...original.status,account_id:'second-account',account_name:'另一个账号'}};
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#chat-empty')).toBeVisible();
  await expect(page.locator('#chat-empty')).toContainText('当前微信账号尚未添加会话');
  await expect(page.locator('#message-input')).toHaveValue('');
  await expect(page.locator('#conversation-list .conversation-item')).toHaveCount(0);
  snapshot={...snapshot,contacts:[{...second,account_id:'second-account'}]};
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#chat-name')).toHaveText(second.lookup);
  await expect(page.locator('#message-input')).toHaveValue('');
  snapshot={...original,contacts:[first]};
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#chat-name')).toHaveText(first.lookup);
  await expect(page.locator('#message-input')).toHaveValue('仅属于原账号的草稿');
 }finally{
  for(const id of ids)await request.delete('/api/contacts/'+id,{headers});
 }
});
