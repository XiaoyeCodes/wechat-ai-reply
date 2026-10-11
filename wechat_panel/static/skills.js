let files=[];
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function skillFields(r){
 files=r.files||[];
 return `<p class="field-hint">导入标准 SKILL.md 或包含它的文件夹，保存后在 Agent 中勾选绑定。技能按任务需要加载，通过 Agent 已授权工具执行。</p><div class="companion-memory-actions"><label class="button secondary">导入 SKILL.md<input hidden type="file" id="skill-import-file" accept=".md"></label><label class="button secondary">导入技能文件夹<input hidden type="file" id="skill-import-folder" webkitdirectory multiple></label></div><label class="ai-field"><span>技能说明与触发场景</span><textarea name="description" aria-label="技能说明与触发场景" rows="3">${escape(r.description)}</textarea></label><label class="ai-field"><span>技能步骤（SKILL.md 正文）</span><textarea name="instructions" aria-label="技能步骤（SKILL.md 正文）" rows="10" required>${escape(r.instructions)}</textarea></label><p id="skill-files" class="field-hint"></p><label class="ai-check"><input type="checkbox" name="enabled" ${r.enabled!==false?'checked':''}>启用 Skill</label><p class="field-hint">参考文档可由模型按需读取。脚本源码可导入查阅；本版通过已授权的内置或 HTTP 工具执行技能步骤，不直接运行任意 shell 脚本。</p>`;
}
export function bindSkillFields(ctx){
 const form=document.getElementById('ai-editor-form');
 const show=()=>{document.getElementById('skill-files').textContent=files.length?`附带 ${files.length} 份参考文件：`+files.map(f=>f.path).join('、'):'没有附带参考文件';};show();
 const load=async e=>{try{
  const chosen=[...e.target.files];if(chosen.length>100)throw new Error('技能文件夹过大，请只选择技能本身');
  const readable=chosen.filter(f=>/\.(md|txt|json|ya?ml|py|js|ts|sh)$/i.test(f.name));
  let size=0;const payload=[];
  for(const f of readable){size+=f.size;if(size>1000000)throw new Error('技能文件总量最多 1 MB');payload.push({path:f.webkitRelativePath||f.name,content:await f.text()});}
  const r=await ctx.api('/api/ai/skills/preview',{method:'POST',body:{files:payload}});
  for(const k of ['name','description','instructions'])form.elements.namedItem(k).value=r[k];files=r.files;show();ctx.toast('技能已导入预览，检查后保存');
 }catch(error){ctx.toast(error.message,true);}};
 document.getElementById('skill-import-file').onchange=load;document.getElementById('skill-import-folder').onchange=load;
}
export function readSkill(form){return {description:form.get('description')||'',instructions:form.get('instructions')||'',files,enabled:form.has('enabled')};}
