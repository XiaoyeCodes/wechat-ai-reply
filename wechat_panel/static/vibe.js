export function vibeFields(r, providers, catalog, esc) {
  const native=r.vibe_engine==='sparkflow'||(!r.id&&catalog.sparkflow?.installed);
  const selected=(key,rows)=>r[key]??rows.filter(x=>x.default).map(x=>x.name);
  const checks=(key,rows)=>`<div class="ai-check-grid">${rows.map(x=>`<label class="ai-check"><input type="checkbox" name="${key}" value="${esc(x.name)}" ${selected(key,rows).includes(x.name)?'checked':''}>${esc(x.label)} <small>${esc(x.name)}</small></label>`).join('')}</div>`;
  const nativeList=(label,items=[])=>`<details><summary>${label}（${items.length} 项）</summary><div class="ai-check-grid">${items.map(name=>`<span>${esc(name)}</span>`).join('')}</div></details>`;
  return `<div id="ai-vibe-fields" hidden>
    <input type="hidden" name="vibe_engine" value="${native?'sparkflow':'github'}">
    <h3>${native?'SparkFlow 同源 AI 助手':'Vibe-Trading 金融研究助手'}</h3>
    <p class="field-hint">${native?'使用 SparkFlow 同源引擎、原生提示词、模型调用和完整工具注册；回复原文直接交给微信，不追加精简或改写要求。':'原生 Agent 自动选择技能、查询资料和计算，再回复微信。'}</p>
    <p class="field-hint">${native?'同源快照 '+esc(catalog.sparkflow?.fingerprint?.slice(0,12)||'未安装'):esc(catalog.detail)+' · 上游版本 '+esc(catalog.revision.slice(0,12))}</p>
    <label class="ai-field"><span>Vibe-Trading 使用的模型</span><select name="vibe_provider_id" aria-label="Vibe-Trading 使用的模型"><option value="">请选择模型</option>${providers.map(p=>`<option value="${esc(p.id)}" ${p.id===r.provider_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
    <label class="ai-field"><span>金融研究联网策略</span><select name="vibe_web_policy"><option value="model" ${r.web_policy!=='off'?'selected':''}>由原生 Agent 按问题选择查询工具</option><option value="off" ${r.web_policy==='off'?'selected':''}>关闭联网，仅分析已有信息和计算</option></select></label>
    ${native?`<p class="field-hint">工具和技能与 SparkFlow 同源，按原生注册逻辑加载，不使用旧版勾选列表。</p>
    ${nativeList('原生工具包',catalog.sparkflow?.tools)}${nativeList('原生研究技能包',catalog.sparkflow?.skills)}
    <div class="ai-form-grid"><label class="ai-field"><span>最大迭代次数（同步 SparkFlow）</span><input name="vibe_max_iterations" type="number" value="50" readonly></label>
    <label class="ai-field"><span>任务时限（同步 SparkFlow）</span><input value="不额外设置整任务截止时间" readonly></label></div>
    <p class="field-hint">原生模型请求超时 180 秒，最多重试 2 次；外网代理 127.0.0.1:7890。沿用原生提示词和记忆，不附加金融助手回复要求。</p>`:`<details open><summary>金融工具包（${catalog.tools.length} 项）</summary>${checks('vibe_tools',catalog.tools)}</details>
    <p class="field-hint">部分工具需要数据商凭据或可用网络。“检查引擎”校验工具能否加载；数据源连通情况需通过测试回复确认。FRED 未配置密钥时不可用。</p>
    <details><summary>研究技能包（${catalog.skills.length} 项）</summary>${checks('vibe_skills',catalog.skills)}</details>
    <div class="ai-form-grid"><label class="ai-field"><span>Vibe 最大迭代次数</span><input name="vibe_max_iterations" type="number" min="2" max="30" value="${r.vibe_max_iterations||12}"></label>
    <label class="ai-field"><span>Vibe 任务时限（秒）</span><input name="vibe_task_timeout" type="number" min="30" max="1800" value="${r.vibe_task_timeout||600}"></label></div>`}
  </div>`;
}

export function readVibe(form) {
  const native=form.get('vibe_engine')==='sparkflow';
  return {provider_id:form.get('vibe_provider_id'),prompt:'',vibe_engine:form.get('vibe_engine')||'github',
    vibe_tools:form.getAll('vibe_tools'),vibe_skills:form.getAll('vibe_skills'),
    vibe_max_iterations:native?50:Number(form.get('vibe_max_iterations')||12),vibe_task_timeout:native?null:Number(form.get('vibe_task_timeout')||1800),
    web_policy:form.get('vibe_web_policy'),knowledge_ids:[],skill_ids:[],tool_ids:[],companion:{}};
}
