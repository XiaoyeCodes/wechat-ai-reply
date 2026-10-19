const {test,expect}=require('@playwright/test');
const headers={'X-Panel-Request':'1'};
let created=[];
test.beforeEach(()=>{created=[];});
test.afterEach(async({request})=>{for(const [kind,id] of created.reverse())await request.delete('/api/ai/config/'+kind+'/'+id,{headers});});
async function create(request,kind,data){const res=await request.post('/api/ai/config/'+kind,{headers,data});expect(res.ok(),await res.text()).toBeTruthy();const result=await res.json();created.push([kind,result.id]);return result;}
async function open(page,id){await page.goto('/');await page.getByRole('button',{name:'AI 中心',exact:true}).click();await page.locator(`.text-button[data-edit="${id}"]`).click();}

test('技能单次勾选：自动添加依赖、取消撤回新增工具、保留已有授权',async({page,request})=>{
 const provider=await create(request,'providers',{name:'图片入口测试模型',base_url:'http://127.0.0.1:19999/v1',model:'fixture'});
 const data=await (await request.get('/api/ai/bootstrap')).json();
 const image=data.tools.find(t=>t.builtin==='generate_image'),audio=data.tools.find(t=>t.builtin==='speak'),clock=data.tools.find(t=>t.builtin==='clock');
 const skill=data.skills.find(s=>s.name==='codex-image-generation');
 const agent=await create(request,'agents',{name:'图片入口测试',provider_id:provider.id,tool_ids:[audio.id,clock.id]});
 await open(page,agent.id);await page.locator('#agent-tab-abilities').click();
 await expect(page.getByRole('checkbox',{name:'生成图片',exact:true})).toHaveCount(0);
 await expect(page.getByRole('checkbox',{name:'合成音频文件',exact:true})).toHaveCount(0);
 await expect(page.locator('summary strong').getByText('图片与音频',{exact:true})).toHaveCount(0);
 await page.getByLabel('查找工具或技能',{exact:true}).fill('speak');
 await expect(page.getByText('没有匹配的工具或技能。',{exact:true})).toBeVisible();
 await page.getByLabel('查找工具或技能',{exact:true}).fill('');
 await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
 const retained=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 expect(retained.tool_ids).toEqual([audio.id,clock.id]);expect(retained.skill_ids).toEqual([]);
 await open(page,agent.id);await page.locator('#agent-tab-abilities').click();
 await page.locator('summary').filter({hasText:'图片创作'}).click();
 await expect(page.getByRole('checkbox',{name:'图像生成',exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:'图像生成',exact:true}).check();
 await expect(page.locator(`[name="tools_ids"][value="${image.id}"]`)).toBeChecked();
 await page.getByRole('checkbox',{name:'图像生成',exact:true}).uncheck();
 await expect(page.locator(`[name="tools_ids"][value="${image.id}"]`)).not.toBeChecked();
 await expect(page.locator(`[name="tools_ids"][value="${audio.id}"]`)).toBeChecked();
 await page.getByRole('checkbox',{name:'图像生成',exact:true}).check();
 expect((await (await request.get('/api/ai/config/agents/'+agent.id)).json()).tool_ids).toEqual(retained.tool_ids);
 await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
 const enabled=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 expect(enabled.tool_ids).toEqual(expect.arrayContaining([audio.id,clock.id,image.id]));
 expect(enabled.skill_ids).toContain(skill.id);
 await open(page,agent.id);await page.locator('#agent-tab-abilities').click();
 await expect(page.getByRole('checkbox',{name:'图像生成',exact:true})).toBeChecked();
 await expect(page.locator('[data-enable-capability],.agent-choice-wrap,[data-cp-news]')).toHaveCount(0);
 await page.locator('#ai-editor').screenshot({path:test.info().outputPath('image-skill-single-entry.png')});
});

test('精简编辑器：移除重复生图、音频和微信语音入口，保存保留原配置',async({page,request})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const provider=await create(request,'providers',{name:'设置视觉模型',base_url:'http://127.0.0.1:19999/v1',model:'fixture'});
 const image=await create(request,'services',{name:'设置图像网关',category:'image',driver:'image_http',url:'http://127.0.0.1:19999/image'});
 const agent=await create(request,'agents',{name:'整理设置测试',provider_id:provider.id,prompt:'我的完整提示词',companion:{memory:{enabled:true,recent_messages:33},media:{image_service_id:image.id,image_prompt:'保留形象',reference_media_id:'old-reference',lora:'old-lora',lora_scale:0,auto_voice:true,voice_id:'old-voice',voice_speed:1.2}}});
 const before=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 await open(page,agent.id);
 await expect(page.getByLabel('Agent 来源',{exact:true})).toHaveCount(0);
 await page.getByLabel('系统提示词 · 此 Agent 的职责与规则',{exact:true}).fill('修改中的完整提示词');
 for(const key of ['abilities','memory','behavior','advanced']){
   await page.locator('#agent-tab-'+key).click();
   await expect(page.getByRole('combobox',{name:'图像生成服务',exact:true})).toHaveCount(0);
   await expect(page.getByRole('combobox',{name:'音频文件服务',exact:true})).toHaveCount(0);
   await expect(page.getByRole('button',{name:'MiniMax 设置与声音克隆',exact:true})).toHaveCount(0);
   await expect(page.getByText('音频文件生成（高级）',{exact:false})).toBeHidden();
   await expect(page.locator('summary').filter({hasText:'图片与音频工具连接'})).toHaveCount(0);
 }
 await page.locator('#agent-tab-behavior').click();
 await page.locator('summary').filter({hasText:'设置收到图片后的理解方式'}).click();
 await page.getByLabel('理解收到的照片',{exact:true}).check();
 await page.getByLabel('图片理解模型连接',{exact:true}).selectOption(provider.id);
 await page.locator('#agent-tab-memory').click();
 await page.locator('summary').filter({hasText:'三层长期记忆'}).click();
 await expect(page.getByLabel('近期消息条数',{exact:true})).toHaveValue('33');
 await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
 const after=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 expect(after.prompt).toBe('修改中的完整提示词');expect(after.companion.memory).toEqual(before.companion.memory);
 expect(after.companion.media).toEqual({...before.companion.media,understand_images:true,vision_provider_id:provider.id});
 await open(page,agent.id);await page.locator('#agent-tab-abilities').click();
 await page.locator('#ai-editor').screenshot({path:test.info().outputPath('agent-abilities-clean.png')});
 await page.setViewportSize({width:390,height:844});await page.locator('#agent-tab-behavior').click();
 expect(await page.locator('#ai-editor').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
 await expect(page.locator('#ai-save')).toBeInViewport();
 await page.screenshot({path:test.info().outputPath('agent-settings-mobile.png')});
 expect(errors).toEqual([]);
});

test('能力配置：原生新闻、按需工具授权、取消不写入和旧值保留',async({page,request})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const provider=await create(request,'providers',{name:'设置新闻模型',base_url:'http://127.0.0.1:19999/v1',model:'fixture'});
 const search=await create(request,'services',{name:'设置搜索连接',category:'search',driver:'agent_reach'});
 const bootstrap=await (await request.get('/api/ai/bootstrap')).json();
 const skill=bootstrap.skills.find(s=>s.name==='news-aggregator-skill')||await create(request,'skills',{name:'news-aggregator-skill',description:'原生新闻采集',instructions:'仅用于配置界面验证，不运行外部采集。'});
 const agent=await create(request,'agents',{name:'新闻授权界面测试',provider_id:provider.id,web_policy:'off',companion:{news_verify:true,search_service_id:search.id}});
 const before=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 const configure=async()=>{
  await page.locator('#agent-tab-abilities').click();await page.locator('summary').filter({hasText:'网页搜索工具连接'}).click();
  await expect(page.locator('#cp-search-status')).toContainText('关闭联网');
  await page.locator('#agent-tab-advanced').click();await page.locator('#agent-network-policy > summary').click();
  await page.getByLabel('联网检索策略',{exact:true}).selectOption('auto');
  await page.locator('#agent-tab-abilities').click();
  await page.locator('[data-cp-grant="web_search,read_web"]').click();
  await page.getByLabel('查找工具或技能',{exact:true}).fill('新闻');
  await page.getByRole('checkbox',{name:'新闻汇总',exact:true}).check();
  await expect(page.locator(`[name="tools_ids"][value="${bootstrap.tools.find(t=>t.builtin==='news_collect').id}"]`)).toBeChecked();
  await expect(page.locator('#cp-news-legacy')).toBeHidden();
  await page.getByLabel('查找工具或技能',{exact:true}).fill('天气');
  await page.getByRole('checkbox',{name:'查询天气',exact:true}).check();
  await page.getByLabel('查找工具或技能',{exact:true}).fill('');
 };
 await open(page,agent.id);await configure();await page.locator('#ai-editor-cancel').click();
 expect(await (await request.get('/api/ai/config/agents/'+agent.id)).json()).toEqual(before);
 await open(page,agent.id);await configure();
 await expect(page.locator('#agent-rights')).toHaveAttribute('open','');
 await expect(page.locator('#agent-rights')).toContainText('新闻采集');
 await expect(page.locator('#agent-rights')).toContainText('允许自动执行');
 await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
 const after=await (await request.get('/api/ai/config/agents/'+agent.id)).json();
 expect(after.web_policy).toBe('auto');expect(after.companion.news_verify).toBe(true);
 expect(after.skill_ids).toContain(skill.id);
 expect(after.tool_ids).toEqual(expect.arrayContaining(bootstrap.tools.filter(t=>['web_search','read_web','weather','news_collect'].includes(t.builtin)).map(t=>t.id)));
 expect(errors).toEqual([]);
});

test('按用户任务分类：单一字段归属、搜索授权与停用状态无损保存',async({page,request})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const p=await create(request,'providers',{name:'分类模型',base_url:'http://localhost:19999/v1',model:'test'});
 const library=await (await request.get('/api/ai/bootstrap')).json();
 const weather=library.tools.find(t=>t.builtin==='weather');
 const news=library.skills.find(s=>s.name==='news-aggregator-skill')||await create(request,'skills',{name:'news-aggregator-skill',instructions:'采集新闻',description:'新闻汇总'});
 const a=await create(request,'agents',{name:'分类与保留验证',enabled:false,provider_id:p.id,skill_ids:[news.id],tool_ids:[weather.id],companion:{weather_enabled:true,memory:{enabled:true,retention_days:321},care:{enabled:true,quiet_start:'22:30'},safety:{export_content:true}}});
 const before=await (await request.get('/api/ai/config/agents/'+a.id)).json();
 await open(page,a.id);
 await expect(page.getByLabel('启用 Agent',{exact:true})).toHaveCount(0);
 await expect(page.locator('#ai-editor-form [name="enabled"]')).toHaveCount(0);
 for(const name of ['cp.media.image_service_id','cp.media.tts_service_id']){
   const field=page.locator(`#ai-editor-form [name="${name}"]`);await expect(field).toHaveCount(1);await expect(field).toBeHidden();
 }
 const places={'provider_id':'basics','prompt':'basics','knowledge_mode':'memory','cp.memory.enabled':'memory','cp.care.enabled':'behavior','cp.weather_enabled':'behavior','cp.media.understand_images':'behavior','cp.safety.emotion_enabled':'behavior','cp.search_service_id':'abilities','web_policy':'advanced','source_policy':'advanced','cp.news_verify':'advanced','cp.safety.observability_service_id':'advanced','cp.safety.export_content':'advanced'};
 for(const [name,pane] of Object.entries(places)){
   const field=page.locator(`#ai-editor-form [name="${name}"]`);await expect(field).toHaveCount(1);
   expect(await field.evaluate(el=>el.closest('.agent-editor-pane').id)).toBe('agent-pane-'+pane);
 }
 await page.locator('#agent-tab-abilities').click();
 await expect(page.getByText('联网与生活',{exact:true})).toHaveCount(0);
 expect(await page.getByRole('checkbox',{name:'新闻汇总',exact:true}).evaluate(el=>el.closest('.agent-resource-group').querySelector('summary strong').textContent)).toBe('内容检索与阅读');
 await expect(page.locator('[data-enable-capability],.agent-choice-wrap,[data-cp-news]')).toHaveCount(0);
 await page.getByLabel('查找工具或技能',{exact:true}).fill('天气');
 await expect(page.getByRole('checkbox',{name:'查询天气',exact:true})).toBeVisible();
 await expect(page.getByRole('checkbox',{name:'查询天气',exact:true})).toBeChecked();
 await page.getByLabel('查找工具或技能',{exact:true}).fill('没有这个能力xyz');
 await expect(page.getByText('没有匹配的工具或技能。',{exact:true})).toBeVisible();
 await page.getByLabel('查找工具或技能',{exact:true}).fill('');
 await page.locator('#ai-editor-fields').evaluate(el=>el.scrollTop=0);
 await page.locator('#ai-editor').screenshot({path:test.info().outputPath('agent-capability-categories.png')});
 await page.locator('#agent-tab-behavior').click();
 await page.locator('summary').filter({hasText:'聊天上下文'}).click();
 await expect(page.getByLabel('把用户所在城市的天气加入聊天上下文',{exact:true})).toBeChecked();
 await page.locator('#ai-save').click();await expect(page.locator('#ai-editor')).toBeHidden();
 const after=await (await request.get('/api/ai/config/agents/'+a.id)).json();
 for(const key of ['enabled','tool_ids','skill_ids','companion','web_policy','source_policy','steps'])expect(after[key],key).toEqual(before[key]);
 expect(errors).toEqual([]);
});


test('阅读技能：勾选直接添加依赖，保留预先授权，取消编辑不落库',async({page,request})=>{
 const p=await create(request,'providers',{name:'阅读交互模型',model:'fixture',base_url:'http://localhost:19999/v1'});
 const data=await (await request.get('/api/ai/bootstrap')).json();
 for(const name of ['web-video-reader','agent-reach-platforms','news-aggregator-skill'])if(!data.skills.some(s=>s.name===name))await create(request,'skills',{name,description:'阅读与新闻测试技能',instructions:'仅测试勾选配置，不执行。'});
 const read=data.tools.find(t=>t.builtin==='read_web');
 const a=await create(request,'agents',{name:'勾选阅读技能',provider_id:p.id,tool_ids:[read.id]});
 const before=await (await request.get('/api/ai/config/agents/'+a.id)).json();
 await open(page,a.id);await page.locator('#agent-tab-abilities').click();
 await page.getByLabel('查找工具或技能',{exact:true}).fill('视频总结');
 await page.getByRole('checkbox',{name:'视频总结',exact:true}).check();
 await page.getByRole('checkbox',{name:'视频总结',exact:true}).uncheck();
 await expect(page.locator(`[name="tools_ids"][value="${read.id}"]`)).toBeChecked();
 await page.getByLabel('查找工具或技能',{exact:true}).fill('多平台阅读');
 await page.getByRole('checkbox',{name:'多平台阅读',exact:true}).check();
 const ids=data.tools.filter(t=>['platform_search','platform_read','platform_browse'].includes(t.builtin)).map(t=>t.id);
 for(const id of ids)await expect(page.locator(`[name="tools_ids"][value="${id}"]`)).toBeChecked();
 await page.getByRole('checkbox',{name:'多平台阅读',exact:true}).uncheck();
 for(const id of ids)await expect(page.locator(`[name="tools_ids"][value="${id}"]`)).not.toBeChecked();
 for(const name of ['视频总结','多平台阅读','新闻汇总']){
   await page.getByLabel('查找工具或技能',{exact:true}).fill(name);await page.getByRole('checkbox',{name,exact:true}).check();
 }
 await page.getByLabel('查找工具或技能',{exact:true}).fill('');
 await expect(page.locator('.agent-choice-wrap,[data-enable-capability],[data-cp-news]')).toHaveCount(0);
 await page.locator('#ai-editor').screenshot({path:test.info().outputPath('skills-checkbox-cards.png')});
 await page.locator('#ai-editor-cancel').click();
 expect(await (await request.get('/api/ai/config/agents/'+a.id)).json()).toEqual(before);
});
