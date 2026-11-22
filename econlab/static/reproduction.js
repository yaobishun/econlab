/* Persistent Codex execution remains within the existing paper/tool screens. */
const reproductionNames={not_started:'尚未开始',searching:'正在搜索',downloading:'正在下载',checking:'正在核对',preparing:'正在准备实现',inspecting:'检查文件',adapting:'编写适配',executing:'执行检查',repairing:'根据错误修正',awaiting_review:'待人工审核',configuring:'配置运行环境',validating:'正在验证',blocked:'任务待继续',waiting_account:'等待 Codex 额度恢复',verified:'复现通过',failed:'复现未通过',interrupted:'已停止，可继续'};
const reproductionActive=['searching','downloading','checking','preparing','configuring','validating','inspecting','adapting','executing','repairing'];
function reproductionHtml(p){
 const r=p.reproduction||{status:'not_started',state:{},urls:[],events:[]},s=r.state||{},active=reproductionActive.includes(r.status);
 return `<section class="panel">
 <div class="row between"><h2>Codex 复现任务与进度</h2><span id="agent-status" class="badge ${r.status==='verified'?'good':''}">${esc(reproductionNames[r.status]||r.status)}</span></div>
 <p id="agent-stage">${esc(r.stage||'首次完整分析后由 Codex 检索材料、检查文件并准备复现工具。')}</p>
 ${s.assessment?.experiment?`<p><b>当前实验：</b>${esc(s.assessment.experiment)}</p>`:''}
 ${s.gaps?.length?'<div class="notice"><b>当前缺口</b><ul>'+s.gaps.map(g=>'<li>'+esc(g)+'</li>').join('')+'</ul></div>':''}
 ${s.implementation_summary?`<p><b>最新实现说明：</b>${esc(s.implementation_summary)}</p>`:''}
 ${s.agent_task_id?`<p class="muted">持续执行任务：<code>${esc(s.agent_task_id)}</code> · <a href="/api/papers/${p.id}/agent" target="_blank" rel="noopener">查看任务与成果记录</a></p>`:''}
 <div id="agent-execution">${executionHtml(r)}</div>
 ${s.tool_id?`<p><a href="#tool/${esc(s.tool_id)}">查看、修改和审核当前工具 →</a></p>`:''}
 <label>本次希望复现的实验与输出（可选，计算前固定）</label>
 <textarea id="experiment-request" placeholder="例如：表2中三国的总福利和实际工资变化；留空时由 AI 根据论文选择有明确基准的实验。">${esc(s.experiment_request||'')}</textarea>
 <div class="actions">
 <button class="material-action" id="continue-materials" ${active?'disabled':''}>让 Codex 继续任务</button>
 <button class="secondary material-action" id="refresh-materials" ${active?'disabled':''}>重新检索公开材料</button>
 ${s.tool_id?`<button class="secondary material-action" id="verify-materials" ${active?'disabled':''}>审核后继续复现</button><button class="secondary material-action" id="repair-materials" ${active?'disabled':''}>根据日志修正实现</button>`:''}
 <button class="secondary" id="stop-materials">停止当前任务</button></div>
 <details><summary>补充材料链接</summary><label>作者主页、期刊附件或下载链接（每行一个，最多10个）</label><textarea id="material-urls" placeholder="https://…"></textarea><button class="secondary material-action" id="fetch-material-url" ${active?'disabled':''}>获取并接入材料</button></details>
 <div id="material-job"></div>
 <details><summary>来源与搜索、下载记录（${r.urls?.length||0}个网址）</summary>${(r.urls||[]).map(u=>`<div class="card-field"><a href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.url)}</a> ${badge(u.status==='acquired'?'completed':'failed')}<p>${esc(u.result?.name||u.result?.error||(u.result?.kind==='page'?'网页已取得，下载文件见后续记录':''))}</p></div>`).join('')||'<p class="muted">尚无下载记录。</p>'}
 <details><summary>处理事件与版本核对记录</summary><pre>${esc(JSON.stringify((r.events||[]).map(e=>({kind:e.kind,created:e.created,data:e.data})),null,2))}</pre></details></details>
 <p class="muted">Codex 在本论文工作区编写和执行数据检查、适配程序。正式工具仍需人工审核，再由后端实际运行并独立比较参考结果。</p></section>`;
}
function wireReproduction(p){
 const start=async body=>{
  body={...body,experiment_request:$('#experiment-request').value.trim()};
  document.querySelectorAll('.material-action').forEach(b=>b.disabled=true);
  try{const j=await api('/papers/'+p.id+'/reproduction',{method:'POST',body});watchJob(j.id,'#material-job',()=>refreshPaper())}
  catch(e){notify(e.message);document.querySelectorAll('.material-action').forEach(b=>b.disabled=false)}
 };
 $('#continue-materials').onclick=()=>start({});$('#refresh-materials').onclick=()=>start({refresh_search:true});
 $('#stop-materials').onclick=async()=>{try{await api('/papers/'+p.id+'/stop',{method:'POST',body:{}});await refreshPaper()}catch(e){notify(e.message)}};
 if($('#verify-materials'))$('#verify-materials').onclick=()=>start({phase:'verify'});
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
}
function methodReproductionHtml(r){return (r.reproduction_plans||[]).map(v=>`<div class="card-field"><h3>关联复现实验</h3><p>${esc(v.plan.experiment||'待确定')}</p><p>${esc(v.stage)}</p>${(v.plan.version_notes||[]).map(n=>'<p class="muted">'+esc(n)+'</p>').join('')}<a href="#paper/${esc(v.paper_id)}">查看材料来源、缺口与验证进度 →</a></div>`).join('')}
function agentMemoryHtml(memory){
 const a=memory.agent_session;if(!a?.thread_id&&!a?.summary)return '';
 return `<h3>执行 Agent 的持续记忆</h3><p>执行会话：<code>${esc(a.thread_id||'尚未建立')}</code></p><div class="message">${esc(a.summary||'执行记录与工作区已保存，完成成果提交时更新摘要。')}</div>`;
}
