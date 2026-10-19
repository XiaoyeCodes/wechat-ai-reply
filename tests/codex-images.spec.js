const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};

test('Codex 账号生图连接与技能可发现，不要求 API Key',async({page,request})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');await page.getByRole('button',{name:'设置',exact:true}).click();
  await page.getByRole('tab',{name:'服务配置',exact:true}).click();
  const card=page.locator('[data-resource-kind="services"]').filter({has:page.getByRole('heading',{name:'Codex / ChatGPT 账号生图',exact:true})});
  await expect(card).toContainText('账号授权 · 无需 API Key');
  await card.getByRole('button',{name:'编辑',exact:true}).click();
  const form=page.locator('#ai-editor-form');
  await expect(form.getByLabel('接入协议',{exact:true})).toHaveValue('codex_image');
  await expect(form.getByLabel('服务 API Key',{exact:true})).toBeHidden();
  await expect(form.getByLabel('服务地址 / 网关完整地址',{exact:true})).toBeHidden();
  await expect(form.getByLabel('服务超时（秒）',{exact:true})).toHaveValue('300');
  await expect(page.locator('#service-hint')).toContainText('图生图');
  await page.screenshot({path:'test-results/codex-image-connector.png',fullPage:true});
  await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await expect(page.locator('#ai-editor')).not.toBeVisible();
  await page.getByRole('button',{name:'AI 中心',exact:true}).click();
  await page.getByRole('tab',{name:'扩展中心',exact:true}).click();
  await page.getByRole('tab',{name:'Skills',exact:true}).click();
  await expect(page.getByRole('heading',{name:'codex-image-generation',exact:true})).toBeVisible();
  const {tools}=await (await request.get('/api/ai/bootstrap')).json();
  const tool=tools.find(t=>t.builtin==='generate_image');
  expect(tool.parameters.properties.mode.enum).toEqual(['generate','edit']);
  expect(tool.approval).toBe('confirm');
  expect(errors).toEqual([]);
});
