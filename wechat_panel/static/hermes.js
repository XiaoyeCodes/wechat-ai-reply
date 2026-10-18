export function hermesFields(r,providers,contacts,skills,esc){
  const managed=r.hermes_managed??true;
  const choices=(rows,id,empty)=>`<option value="">${empty}</option>`+rows.map(x=>`<option value="${esc(x.id)}" ${x.id===id?'selected':''}>${esc(x.name||x.lookup)}</option>`).join('');
  return `<div id="ai-hermes-fields" hidden>
    <h3>Hermes 电脑助手</h3><p class="field-hint">Hermes 负责多步任务、文件、终端、浏览器与桌面工具。工作台负责审批和微信回复。测试台也会按所选权限实际操作电脑。</p>
    <label class="ai-field"><span>引擎运行方式</span><select name="hermes_managed" aria-label="引擎运行方式"><option value="true" ${managed?'selected':''}>本机托管，随任务自动启动</option><option value="false" ${!managed?'selected':''}>连接已有 Hermes API 服务</option></select></label>
    <div id="ai-hermes-local"><label class="ai-field"><span>Hermes 使用的模型</span><select name="hermes_provider_id" aria-label="Hermes 使用的模型">${choices(providers,r.provider_id,'请选择模型')}</select></label>
    <label class="ai-field"><span>起始工作目录</span><input name="hermes_workspace" value="${esc(r.hermes_workspace||'')}" placeholder="留空使用独立的 Hermes 工作目录"></label><p class="field-hint">这是任务的起始目录，不是文件访问隔离边界。工具和审批策略由独立 Hermes 配置管理。</p></div>
    <label class="ai-field"><span>电脑助手系统提示词</span><textarea name="hermes_prompt" rows="4">${esc(r.prompt||'用中文协助我完成电脑上的任务。先核对目标，再操作；根据实际工具结果汇报。')}</textarea></label>
    <div id="ai-hermes-skills"><label class="field-label">同步到本机 Hermes 的 Skill</label><div class="ai-check-grid">${skills.map(s=>`<label class="ai-check"><input type="checkbox" name="hermes_skill_ids" aria-label="Hermes Skill：${esc(s.name)}" value="${esc(s.id)}" ${(r.skill_ids||[]).includes(s.id)?'checked':''}>${esc(s.name)}</label>`).join('')||'<p class="field-hint">可先在 Skill 技能页导入。已内置官方 computer-use 桌面操作说明。</p>'}</div></div>
    <label class="ai-field"><span>允许下达电脑任务的微信联系人</span><select name="hermes_contact_id" aria-label="允许下达电脑任务的微信联系人">${choices(contacts,r.hermes_contact_id,'仅在测试台使用')}</select></label>
    <p class="field-hint">选择授权联系人后，还需在该会话绑定此 Agent。每个 Hermes Agent 仅授权一位联系人。</p>
    <label class="ai-check"><input type="checkbox" name="hermes_chat_routing" ${r.hermes_chat_routing!==false?'checked':''}>普通聊天直接回复，仅明确的任务交给电脑执行引擎</label>
    <p class="field-hint">本机托管使用所选模型判断本轮意图。闲聊和含糊请求不会调用电脑工具；旧指令不自动补做。</p>
    <label class="ai-field"><span>任务执行策略</span><select name="hermes_task_approval" aria-label="任务执行策略"><option value="confirm" ${r.hermes_task_approval!=='auto'?'selected':''}>电脑任务开始前先确认</option><option value="auto" ${r.hermes_task_approval==='auto'?'selected':''}>任务自动开始，按操作权限确认（推荐）</option></select></label>
    <label class="ai-field"><span>电脑操作权限</span><select name="hermes_tool_policy" aria-label="电脑操作权限">${['native','all'].includes(r.hermes_tool_policy)?`<option value="${r.hermes_tool_policy}" selected>原有配置（可切换到以下三档）</option>`:''}<option value="sensitive" ${!r.hermes_tool_policy||r.hermes_tool_policy==='sensitive'?'selected':''}>1. 修改、删除需审批，读取自动执行</option><option value="ai_review" ${r.hermes_tool_policy==='ai_review'?'selected':''}>2. AI 代审，敏感操作由用户确认</option><option value="full" ${r.hermes_tool_policy==='full'?'selected':''}>3. 完全访问，无需任何审批</option></select></label>
    <p class="field-hint">AI 代审会检查实际操作，敏感或无法判断的操作交给用户；整项任务确认一次。自定义权限适用于本机托管。完全访问会直接执行授权联系人的任务，包括修改或删除文件；普通聊天仍直接回复。</p>
    <div id="ai-hermes-confirm-options">
    <label class="ai-field"><span>审批范围</span><select name="hermes_approval_scope" aria-label="审批范围"><option value="task" ${(r.hermes_approval_scope||(r.id?'operation':'task'))==='task'?'selected':''}>整项任务确认一次（推荐）</option><option value="operation" ${(r.hermes_approval_scope||(r.id?'operation':'task'))==='operation'?'selected':''}>每个操作分别确认</option></select></label>
    <p class="field-hint">整项任务确认后，后续必要步骤连续完成；新任务重新判断权限。微信中用日常语言说明准备完成的事情，技术详情保留在运行记录。</p>
    <label class="ai-field"><span>操作确认渠道</span><select name="hermes_approval_channel" aria-label="操作确认渠道"><option value="panel" ${r.hermes_approval_channel!=='wechat'?'selected':''}>在工作台确认</option><option value="wechat" ${r.hermes_approval_channel==='wechat'?'selected':''}>发送到原微信会话，回复“确认”</option></select></label>
    <p class="field-hint">直接回复“确认”或“取消”。只处理当前联系人唯一的待确认任务，不需要编号。测试台在面板确认。</p>
    <label class="ai-field"><span>确认有效期（秒）</span><input name="hermes_approval_timeout" type="number" min="60" max="1800" value="${r.hermes_approval_timeout||600}"></label>
    </div><p id="ai-hermes-full-note" class="field-hint" hidden>完全访问已选：任务直接开始，执行过程中无需面板或微信批准。仅作用于此 Agent 授权的联系人和本轮任务。</p>
    <label class="ai-field"><span>单次任务时限（秒）</span><input name="hermes_task_timeout" type="number" min="30" max="3600" value="${r.hermes_task_timeout||900}"></label>
  </div>`;
}

export function bindHermes(form){
  const policy=form.elements.namedItem('hermes_tool_policy'),task=form.elements.namedItem('hermes_task_approval'),scope=form.elements.namedItem('hermes_approval_scope'),managed=form.elements.namedItem('hermes_managed');
  const update=()=>{
    if(managed.value!=='true'&&policy.value!=='native'){
      if(![...policy.options].some(x=>x.value==='native'))policy.add(new Option('远程引擎原生审批','native'));
      policy.value='native';
    }
    const full=policy.value==='full',simple=['sensitive','ai_review','full'].includes(policy.value);
    if(simple){task.value='auto';scope.value='task';}
    task.disabled=simple;task.closest('label').hidden=simple;scope.closest('label').hidden=simple;
    form.querySelector('#ai-hermes-confirm-options').hidden=full;
    form.querySelector('#ai-hermes-full-note').hidden=!full;
  };
  policy.addEventListener('change',update);managed.addEventListener('change',update);update();
}

export function readHermes(form){
  return {hermes_managed:form.get('hermes_managed')==='true',
    hermes_workspace:form.get('hermes_workspace')||'',hermes_contact_id:form.get('hermes_contact_id')||'',
    hermes_task_approval:['sensitive','ai_review','full'].includes(form.get('hermes_tool_policy'))?'auto':form.get('hermes_task_approval')||'confirm',hermes_task_timeout:Number(form.get('hermes_task_timeout'))||900,
    hermes_approval_scope:form.get('hermes_approval_scope')||'task',
    hermes_tool_policy:form.get('hermes_tool_policy')||'native',hermes_approval_channel:form.get('hermes_approval_channel')||'panel',
    hermes_approval_timeout:Number(form.get('hermes_approval_timeout'))||600,hermes_chat_routing:form.has('hermes_chat_routing'),
    skill_ids:form.getAll('hermes_skill_ids'),provider_id:form.get('hermes_managed')==='true'?form.get('hermes_provider_id'):'',prompt:form.get('hermes_prompt')||''};
}
