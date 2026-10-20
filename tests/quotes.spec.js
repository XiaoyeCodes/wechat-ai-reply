const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB7sAAAAASUVORK5CYII=','base64');

for(const group of [false,true])test(`${group?'群聊':'私聊'}：分开展示引用原文、引用图片和当前问题`,async({page,request})=>{
 let contact;
 try{
  const response=await request.post('/api/contacts',{headers,data:group?
   {lookup:'引用显示测试群',chat_type:'group',group_self_name:'机器人'}:
   {lookup:'引用显示测试人',wechat_id:'quote-ui-private'}});
  expect(response.ok(),await response.text()).toBeTruthy();contact=await response.json();
  const quotedText='<img src=x onerror="window.quoteExecuted=true"> 引用文字只作为数据。';
  const common={contact_id:contact.id,direction:'incoming',kind:'text',status:'observed',created_at:new Date().toISOString(),
   historical:false,sender:'张三',sender_verified:true,mention_me:group,mention_verified:true};
  const messages=[{...common,id:90001,text:'这句话是什么意思？',quote:{kind:'text',sender:'李四',text:quotedText,status:'ready'}},
   {...common,id:90002,text:'被引用的图是什么颜色？',quote:{kind:'image',sender:'机器人',text:'图片',status:'ready',media_id:'quote-ui-image'}},
   {...common,id:90003,text:'这张旧图呢？',quote:{kind:'image',sender:'李四',text:'图片',status:'unavailable'}}];
  await page.route(`**/api/contacts/${contact.id}/messages*`,route=>route.fulfill({json:messages}));
  await page.route('**/api/ai/media/quote-ui-image',route=>route.fulfill({contentType:'image/png',body:png}));
  await page.goto('/');await page.locator(`[data-contact="${contact.id}"]`).click();
  const text=page.locator('[data-message-id="90001"]');
  await expect(text.locator('.message-quote')).toContainText(quotedText);
  await expect(text.locator('.message-bubble')).toContainText('这句话是什么意思？');
  expect(await page.evaluate(()=>!!window.quoteExecuted)).toBe(false);
  const image=page.locator('[data-message-id="90002"]');
  await expect(image.getByAltText('被引用的图片')).toBeVisible();
  await expect(image.locator('.message-quote')).toContainText('引用 机器人');
  await expect(page.locator('[data-message-id="90003"] .message-quote')).toContainText('引用图片暂未读取');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('quotes.png'),fullPage:true});
 }finally{if(contact?.id)await request.delete('/api/contacts/'+contact.id,{headers});}
});
