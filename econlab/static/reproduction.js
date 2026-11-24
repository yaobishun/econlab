/* Persistent Codex execution remains within the existing paper/tool screens. */
const reproductionNames={not_started:'尚未开始',searching:'正在搜索',downloading:'正在下载',checking:'正在核对',preparing:'正在准备实现',inspecting:'检查文件',adapting:'编写适配',executing:'执行检查',repairing:'根据错误修正',awaiting_review:'待人工审核',configuring:'配置运行环境',validating:'正在验证',blocked:'任务待继续',waiting_account:'等待 Codex 额度恢复',verified:'复现通过',failed:'复现未通过',interrupted:'已停止，可继续'};
const reproductionActive=['searching','downloading','checking','preparing','configuring','validating','inspecting','adapting','executing','repairing'];
function reproductionHtml(p){
 const r=p.reproduction||{status:'not_started',state:{},urls:[],events:[]},s=r.state||{},active=reproductionActive.includes(r.status);
 return experimentInventoryHtml(p)+`<section class="panel">
 <div class="row between"><h2>Codex 复现任务与进度</h2><span id="agent-status" class="badge ${r.status==='verified'?'good':''}">${esc(reproductionNames[r.status]||r.status)}</span></div>
 <p id="agent-stage">${esc(r.stage||'首次完整分析后由 Codex 检索材料、检查文件并准备复现工具。')}</p>
 ${s.assessment?.experiment?`<p><b>当前实验：</b>${esc(s.assessment.experiment)}</p>`:''}
 ${s.gaps?.length?'<div class="notice"><b>当前缺口</b><ul>'+s.gaps.map(g=>'<li>'+esc(g)+'</li>').join('')+'</ul></div>':''}
 ${s.implementation_summary?`<p><b>最新实现说明：</b>${esc(s.implementation_summary)}</p>`:''}
 ${s.agent_task_id?`<p class="muted">持续执行任务：<code>${esc(s.agent_task_id)}</code> · <a href="/api/papers/${p.id}/agent" target="_blank" rel="noopener">查看任务与成果记录</a></p>`:''}
 <div id="agent-execution">${executionHtml(r)}</div>
 ${s.tool_id?`<p><a href="#tool/${esc(s.tool_id)}">查看、修改和审核当前工具 →</a></p>`:''}
 <p class="muted">按完整实验清单继续，保留已通过项；修改验收范围请使用上方“人工修改清单”，并重新审核。</p>
 <input type="hidden" id="experiment-request" value="">
 <div class="actions">
 <button class="material-action" id="continue-materials" ${active?'disabled':''}>让 Codex 继续任务</button>
 <button class="secondary material-action" id="refresh-materials" ${active?'disabled':''}>重新检索公开材料</button>
 ${s.tool_id?`<button class="secondary material-action" id="verify-materials" ${active?'disabled':''}>${p.reproduction_mode==='manual'?'审核后继续复现':'继续自动复现'}</button><button class="secondary material-action" id="repair-materials" ${active?'disabled':''}>根据日志修正实现</button>`:''}
 <button class="secondary" id="stop-materials">停止当前任务</button></div>
 <details><summary>补充材料链接</summary><label>作者主页、期刊附件或下载链接（每行一个，最多10个）</label><textarea id="material-urls" placeholder="https://…"></textarea><button class="secondary material-action" id="fetch-material-url" ${active?'disabled':''}>获取并接入材料</button></details>
 <div id="material-job"></div>
 <details><summary>来源与搜索、下载记录（${r.urls?.length||0}个网址）</summary>${(r.urls||[]).map(u=>`<div class="card-field"><a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url)}</a> ${badge(u.status==='acquired'?'completed':'failed')}<p>${esc(u.result?.name||u.result?.error||(u.result?.kind==='page'?'网页已取得，下载文件见后续记录':''))}</p></div>`).join('')||'<p class="muted">尚无下载记录。</p>'}
 <details><summary>处理事件与版本核对记录</summary><pre>${esc(JSON.stringify((r.events||[]).map(e=>({kind:e.kind,created:e.created,data:e.data})),null,2))}</pre></details></details>
 <p class="muted">${p.reproduction_mode==='manual'?'当前为人工审核模式，确认后由后端运行。':'当前为自动模式：Codex 检查模型、代码和清单，系统自动配置环境并在沙箱运行，后端独立验收；人工修改与审核可随时使用。'}</p></section>`;
}
function wireReproduction(p){
 wireExperimentInventory(p);
 const start=async body=>{
  body={...body,experiment_request:$('#experiment-request').value.trim()};
  document.querySelectorAll('.material-action').forEach(b=>b.disabled=true);
  try{const j=await api('/papers/'+p.id+'/reproduction',{method:'POST',body});watchJob(j.id,'#material-job',()=>refreshPaper())}
  catch(e){notify(e.message);document.querySelectorAll('.material-action').forEach(b=>b.disabled=false)}
 };
 $('#continue-materials').onclick=()=>start({});$('#refresh-materials').onclick=()=>start({refresh_search:true});
 $('#stop-materials').onclick=async()=>{try{await api('/papers/'+p.id+'/stop',{method:'POST',body:{}});await refreshPaper()}catch(e){notify(e.message)}};
 if($('#verify-materials'))$('#verify-materials').onclick=()=>{if(p.reproduction_mode==='manual'&&!p.experiment_inventory?.approved)reviewExperimentInventory(p);else start({phase:'verify'})};
 if($('#repair-materials'))$('#repair-materials').onclick=()=>start({repair:true});
 $('#fetch-material-url').onclick=()=>start({urls:$('#material-urls').value.split('\n').map(v=>v.trim()).filter(Boolean)});
}
function executionHtml(r){
 const rows=(r.events||[]).filter(e=>['agent_execution','agent_artifact','experiment_pinned'].includes(e.kind)).slice(0,8);
 return rows.length?'<details open><summary>真实执行与成果（最近8条）</summary>'+rows.map(e=>{
  const d=e.data||{};return `<div class="card-field"><small>${esc(e.created)}</small><b> ${esc(d.type||e.kind)}</b>
  ${d.command?`<pre>${esc(d.command.slice(0,1400))}</pre>`:''}${d.exitCode!=null?`<p>退出码：${Number(d.exitCode)}</p>`:''}
  ${d.aggregatedOutput?`<details><summary>命令输出</summary><pre>${esc(d.aggregatedOutput)}</pre></details>`:''}
  ${d.asset_id?`<a href="/api/assets/${esc(d.asset_id)}/file">下载 ${esc(d.path||d.asset_id)}</a>`:''}${e.kind==='experiment_pinned'?'<p>实验、参考和验收字段已固定。</p>':''}</div>`;
 }).join('')+'</details>':'<p class="muted">执行任务会在这里记录实际命令、错误修正和适配成果。</p>';
}
async function refreshAgentProgress(pid){
 const r=await api('/papers/'+pid+'/reproduction');if(current?.id!==pid)return;
 if($('#agent-stage'))$('#agent-stage').textContent=r.stage;
 if($('#agent-status'))$('#agent-status').textContent=reproductionNames[r.status]||r.status;
 if($('#agent-execution'))$('#agent-execution').innerHTML=executionHtml(r);
 const project=await api('/papers/'+pid+'/experiments');if(current?.id!==pid)return;
 current.experiment_inventory=project.inventory;current.controller=project.controller;
 if($('#experiment-inventory')){$('#experiment-inventory').outerHTML=experimentInventoryHtml(current);wireExperimentInventory(current)}
}
function methodReproductionHtml(r){return (r.paper_experiments||[]).map(v=>`<div class="card-field"><h3>整篇实验覆盖</h3><p>${esc(v.title)}：${v.inventory.counts.passed} / ${v.inventory.counts.required} 项通过 · ${v.inventory.eligible?'已开放':'尚未开放求解'}</p><a href="#paper/${esc(v.paper_id)}">核对完整实验清单与审核 →</a></div>`).join('')}
function agentMemoryHtml(memory){
 const a=memory.agent_session;if(!a?.thread_id&&!a?.summary)return '';
 return `<h3>执行 Agent 的持续记忆</h3><p>执行会话：<code>${esc(a.thread_id||'尚未建立')}</code></p><div class="message">${esc(a.summary||'执行记录与工作区已保存，完成成果提交时更新摘要。')}</div>`;
}

Object.assign(reproductionNames,{planning:'核对完整实验清单',prepared:'配置已准备，继续自动检查',running:'执行实验',complete:'全部必验项通过',verified:'单项验证通过（整体见实验清单）'});
reproductionActive.push('planning','prepared','running');
const experimentStatuses={passed:'数值通过',failed:'失败',missing_materials:'缺材料',not_run:'未执行',stale:'变更后需重验',running:'运行中',waiting_dependency:'等待前置实验',qualitative_review_required:'定性项目：需单独核对',qualitative_checked:'定性已核对（非数值验证）',excluded:'已排除（见审核原因）',interrupted:'中断，可续跑'};
function experimentInventoryHtml(p){
 const s=p.experiment_inventory||{revision:0,experiments:[],coverage:[],counts:{passed:0,required:0}},c=s.counts,controller=p.controller||{};
 return `<section class="panel" id="experiment-inventory"><div class="row between"><h2>整篇论文实验清单</h2><span class="badge ${s.eligible?'good':''}">${s.eligible?'全部必验项通过 · 可用于求解':'尚未整篇复现完成'}</span></div>
 ${s.draft?`<div class="notice"><strong>清单草稿：已识别 ${s.draft.candidate_count} 项</strong>（尚未通过结构校验，不计验收）${s.draft.errors.map(e=>`<p>${esc(e)}</p>`).join('')}<details><summary>查看已识别项目</summary>${s.draft.experiments.map(e=>`<p>${esc(e.name)} · ${esc((e.figures||[]).join('、'))}</p>`).join('')}</details></div>`:''}
 <p>已验收 <strong>${c.completed??c.passed} / ${c.required}</strong> 项 · 数值通过 ${c.passed} · 定性核对 ${c.qualitative_checked||0} · 清单版本 ${s.revision} · ${s.approved?'当前批量检查有效':p.reproduction_mode==='manual'?'等待人工审核':'自动检查与环境配置尚未完成'}</p>
 <p class="muted">失败 ${c.failed||0} · 缺材料 ${c.missing_materials||0} · 未执行 ${c.not_run||0} · 需重验 ${c.stale||0} · 等待依赖 ${c.waiting_dependency||0} · 已排除 ${c.excluded||0}</p>
 <h3>核心模型与公共求解器</h3>${(p.methods||[]).map(m=>`<p><a href="#method/${esc(m.id)}">${esc(m.name)}</a> · ${(p.tools||[]).filter(t=>t.method_id===m.id).map(t=>`<a href="#tool/${esc(t.id)}">${esc(t.name)}</a>`).join(' / ')||'待准备'}</p>`).join('')}
 ${controller.stage?`<p class="notice">当前步骤：${esc(controller.stage)}${controller.next_action==='review'?'；下一步请核对下方模型/工具及清单，再审核并继续。':''}</p>`:''}
 <div class="actions"><button class="secondary" id="edit-experiment-plan">人工修改清单</button><button id="approve-experiment-plan" ${!s.revision?'disabled':''}>人工审核清单并继续</button><button class="secondary" id="experiment-history">修订与审核记录</button><button class="secondary" id="project-memory">论文项目记忆</button></div>
 ${(s.experiments||[]).map(e=>`<details class="card-field"><summary><span class="badge ${e.status==='passed'?'good':''}">${esc(experimentStatuses[e.status]||e.status)}</span> ${esc(e.name)} · ${e.required?'必验':'排除'} · ${esc(e.kind)}</summary>
 <p>${esc(e.description||'')} ${esc((e.figures||[]).join('、'))}</p>${e.qualitative_report_asset?`<p><a href="/api/assets/${esc(e.qualitative_report_asset)}/file" target="_blank" rel="noopener">查看解析或逐行核对报告</a> · ${esc(e.qualitative_check_mode)}</p>`:''}<p>模型：<a href="#method/${esc(e.method_id)}">${esc(e.method_id)}</a> · 求解器：${e.tool_id?`<a href="#tool/${esc(e.tool_id)}">查看、修改与审核 →</a>`:'待准备'} · 依赖：${esc((e.dependencies||[]).join('、')||'无')}</p>
 ${e.gaps?.length?`<p class="notice">${esc(e.gaps.join('；'))}</p>`:''}${e.exclusion_reason?`<p>排除理由：${esc(e.exclusion_reason)}</p>`:''}
 <p>验收字段：${esc((e.validation_fields||[]).join('、'))} · 单位：${esc(JSON.stringify(e.units||{}))}</p><p>输入 ${e.input_asset?`<a href="/api/assets/${esc(e.input_asset)}/file">下载</a>`:'待获取'} · 独立参考 ${e.reference_asset?`<a href="/api/assets/${esc(e.reference_asset)}/file">下载</a>`:'待获取'}</p>
 ${e.result?.comparison?comparisonHtml(e.result.comparison):''}${e.result?.error?`<p class="notice">${esc(e.result.error)}</p>`:''}${e.run_id?`<a href="/api/runs/${esc(e.run_id)}/report" target="_blank" rel="noopener">实际结果、误差及运行日志</a>`:''}
 ${e.result?.curve_comparison?`<details><summary>曲线形状、临界点与区间验收</summary><pre>${esc(JSON.stringify(e.result.curve_comparison,null,2))}</pre></details>`:''}
 ${e.kind==='qualitative'?`<button class="secondary" data-check-qualitative="${esc(e.id)}">记录定性核对（不计数值通过）</button>`:''}
 ${e.kind==='qualitative'?`<button class="secondary qualitative-check" data-experiment="${esc(e.id)}">记录定性核对</button>`:''}<details><summary>原文依据、参数与依赖映射</summary><pre>${esc(JSON.stringify({evidence:e.evidence,design:e.design,parameters:e.parameters,bundle_paths:e.bundle_paths,dependency_bindings:e.dependency_bindings,dependency_materials:e.dependency_materials,related_experiments:e.related_experiments,dependency_notes:e.dependency_notes,observations:e.observations,curve_checks:e.curve_checks},null,2))}</pre></details></details>`).join('')||'<p>完整视觉阅读后，Codex 将逐页核对正文、附录和所有图表并建立实验清单。</p>'}
 <details><summary>图表及逐页核对记录（${s.coverage?.length||0} 条）</summary>${(s.coverage||[]).map(i=>`<p>PDF ${i.page} 页 · ${esc(i.label)} → ${esc(i.experiment_ids?.join('、')||i.noncomputational_reason)}</p>`).join('')||'<p>尚未核对图表。</p>'}</details>
 <p class="muted">数值逐项相对误差严格小于 10%，零参考使用预先固定的绝对容差。曲线另验形状、临界点和区间。定性项目不会计作定量通过；范围排除须明确理由并重新审核。</p></section>`;
}
function wireExperimentInventory(p){
 document.querySelectorAll('[data-check-qualitative]').forEach(b=>b.onclick=()=>{modal('<h2>定性核对</h2><p>仅记录图示与原文的一致性，不替代任何数值验收。</p><label>审核人</label><input id="qual-reviewer"><label>原文依据与核对结果</label><textarea id="qual-opinion"></textarea><button id="submit-qual">保存定性核对</button>');$('#submit-qual').onclick=async()=>{try{await api('/papers/'+p.id+'/experiments/qualitative',{method:'POST',body:{experiment_id:b.dataset.checkQualitative,revision:p.experiment_inventory.revision,reviewer:$('#qual-reviewer').value.trim(),opinion:$('#qual-opinion').value.trim()}});$('#modal').close();await refreshPaper()}catch(e){notify(e.message)}}});
 document.querySelectorAll('.qualitative-check').forEach(b=>b.onclick=()=>{modal('<h2>记录定性核对</h2><p>仅确认图示、机制或方向的原文依据，不会生成数值验证通过证明。</p><label>核对人</label><input id="qual-reviewer"><label>原文与核对结论</label><textarea id="qual-opinion"></textarea><button id="save-qualitative">保存</button>');$('#save-qualitative').onclick=async()=>{try{await api('/papers/'+p.id+'/experiments/qualitative',{method:'POST',body:{revision:p.experiment_inventory.revision,experiment_id:b.dataset.experiment,reviewer:$('#qual-reviewer').value.trim(),opinion:$('#qual-opinion').value.trim()}});$('#modal').close();await refreshPaper()}catch(e){notify(e.message)}}});
 $('#edit-experiment-plan').onclick=()=>{const s=p.experiment_inventory||{};modal(`<h2>人工修改完整实验清单</h2><p>保留稳定 id；新增、排除、修改参考或依赖都会保存版本并撤销旧审核。禁止填入状态或验证结果。</p><label>编辑人</label><input id="inventory-actor"><label>修改原因</label><input id="inventory-reason"><label>完整清单 JSON</label><textarea id="inventory-json" rows="22">${esc(JSON.stringify(s.document||{experiments:[],coverage:[],pages_audited:[]},null,2))}</textarea><button id="save-inventory">保存清单</button>`);$('#save-inventory').onclick=async()=>{try{await api('/papers/'+p.id+'/experiments',{method:'PUT',body:{revision:s.revision||0,actor:$('#inventory-actor').value.trim(),reason:$('#inventory-reason').value.trim(),document:JSON.parse($('#inventory-json').value)}});$('#modal').close();await refreshPaper()}catch(e){notify(e.message)}}};
 $('#approve-experiment-plan').onclick=()=>reviewExperimentInventory(p);
 $('#experiment-history').onclick=async()=>{try{const h=await api('/papers/'+p.id+'/experiments/history');modal('<h2>实验清单修订与审核</h2><pre>'+esc(JSON.stringify(h,null,2))+'</pre>')}catch(e){notify(e.message)}};
 $('#project-memory').onclick=async()=>{try{const r=await api('/papers/'+p.id+'/experiments');modal('<h2>统一论文项目记忆</h2><pre>'+esc(JSON.stringify(r.memory,null,2))+'</pre>')}catch(e){notify(e.message)}};
}
function reviewExperimentInventory(p){
 modal('<h2>审核完整实验计划</h2><p>先在模型和工具详情确认人工审核，并配置运行环境。请核对全部页面、图表覆盖、必验项、依赖、代码、输入、独立参考、单位及误差规则。审核不会代替实际计算。</p><label>审核人</label><input id="batch-reviewer"><label>核对内容与排除理由</label><textarea id="batch-opinion"></textarea><button id="submit-batch-review">保存审核并继续批量执行</button>');
 $('#submit-batch-review').onclick=async()=>{try{await api('/papers/'+p.id+'/experiments/review',{method:'POST',body:{revision:p.experiment_inventory.revision,reviewer:$('#batch-reviewer').value.trim(),opinion:$('#batch-opinion').value.trim()}});$('#modal').close();const j=await api('/papers/'+p.id+'/reproduction',{method:'POST',body:{phase:'verify'}});watchJob(j.id,'#material-job',()=>refreshPaper())}catch(e){notify(e.message)}};
}
