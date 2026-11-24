'use strict';

const reportOrigins={paper:'论文原文',data:'数据文件',code:'代码文件',ai_assumption:'AI 自行设定 · 待审核',ai_derivation:'AI 补充推导 · 待审核',manual:'人工补充',unknown:'来源待确认'};
const reportStages={method:'模型设定与方程',tool:'代码实现',code_part:'代码分段检查',scope:'实验范围',experiment:'输入与参考',qualitative:'定性核对',plan:'运行计划'};
const reportDecisions={accept:'检查接受',revise:'退回修改',blocked:'条件缺失',accepted:'终审已确认',disputed:'终审有异议',pending:'待终审'};
function reportText(value){return typeof value==='string'?value:value==null?'':JSON.stringify(value,null,2)}
function reportBlock(value,empty='尚未补充'){return `<div class="report-prose">${esc(reportText(value)||empty)}</div>`}
function reportEvidence(refs,r){return (refs||[]).map(v=>`<div class="evidence"><a href="/api/assets/${esc(v.asset_id||r.paper.original_asset)}/file#page=${Number(v.page)}" target="_blank" rel="noopener">原文第 ${Number(v.page)} 页 ↗</a><br>${esc(v.quote)}</div>`).join('')}
function reportOrigin(value){return `<span class="badge ${['ai_assumption','unknown'].includes(value)?'bad':''}">${esc(reportOrigins[value]||value||'来源待确认')}</span>`}
function reportDetails(label,value){return `<details><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(value??{},null,2))}</pre></details>`}
function reportAsset(aid,r){const a=r.assets.find(a=>a.id===aid);return a?`<a href="/api/assets/${esc(aid)}/file" target="_blank" rel="noopener">${esc(a.name)} ↗</a>`:'<span class="muted">未绑定文件</span>'}

async function renderReproductionReport(p,box){
 const id=p.id,epoch=routeEpoch;box.innerHTML='<p class="muted">正在汇总真实运行、来源和审核记录…</p>';
 try{
  const r=await api('/papers/'+id+'/report');
  if(epoch!==routeEpoch||current?.id!==id||tab!=='report')return;
  box.innerHTML=reportHtml(r);wireReport(r,box);
  if(r.active_job){$('#generate-report').disabled=true;$('#stop-report').disabled=false;watchJob(r.active_job.id,'#report-job',()=>renderReproductionReport(current,box))}
  else if(r.latest_job&&['failed','cancelled','interrupted'].includes(r.latest_job.status))$('#report-job').innerHTML='<p class="notice">上次报告说明整理未完成：'+esc(r.latest_job.error||r.latest_job.stage)+'。已有报告保留，可重新点击 AI 补充；这不会重跑实验。</p>';
 }catch(e){if(epoch===routeEpoch&&tab==='report')box.innerHTML='<p class="notice">'+esc(e.message)+'</p>'}
}

function reportHtml(r){
 const c=r.summary.counts,s=r.supplement;
 const unresolved=r.audit.issues.filter(v=>v.status!=='resolved');
 const activeReview=r.final_reviews.find(v=>v.current);
 return `<article class="reproduction-report">
 <section class="panel report-intro"><div class="row between"><div><div class="eyebrow">REPRODUCTION / REPORT</div><h2>复现报告</h2></div>${badge(r.summary.eligible?'passed':'部分完成 / 待处理')}</div>
 <p>${esc(r.paper.title)}</p><p class="muted">数值通过 ${c.passed} 项 · 定性核对 ${c.qualitative_checked||0} 项 · 当前清单必验 ${c.required} 项。准备任务和实验仍按现有清单统计。</p>
 <div class="report-progress" role="img" aria-label="当前完成 ${c.completed} / ${c.required}"><span style="width:${c.required?100*c.completed/c.required:0}%"></span></div>
 <p class="muted">报告说明版本 ${s.revision} · ${esc(s.actor||'尚未生成补充说明')} ${s.updated?esc(new Date(s.updated).toLocaleString()):''}。报告终审与实际数值验收分别记录。</p>
 ${r.supplement_stale?'<p class="notice">模型、材料、代码或运行记录已变化：以下补充说明需要重新核对，旧终审不再适用于当前报告。</p>':''}
 <div class="actions report-controls"><button id="generate-report">用 AI 补充报告说明</button><button class="secondary" id="stop-report" disabled>停止本论文当前 AI 任务</button><button class="secondary" id="edit-report">人工修改报告说明</button><button class="secondary" id="refresh-report">刷新报告</button><a class="button secondary" href="/api/papers/${r.paper.id}/report/export">导出完整报告 JSON</a><button class="secondary" id="print-report">打印 / 保存 PDF</button></div><div id="report-job"></div>
 <p class="muted">AI 补充假设理由、逐步推导与结果解释，单独标注来源；此操作不重跑实验。数值、误差和运行状态直接来自后端记录。打印包含当前已加载的明细与图表；完整数值可下载 CSV 或报告 JSON。</p>
 <nav class="report-nav" aria-label="报告章节"><a href="#report-models" data-report-anchor="report-models">1 模型设定</a><a href="#report-equations" data-report-anchor="report-equations">2 推导与均衡</a><a href="#report-inputs" data-report-anchor="report-inputs">3 输入与代码</a><a href="#report-results" data-report-anchor="report-results">4 结果对照</a><a href="#report-audit" data-report-anchor="report-audit">5 审核返修</a></nav></section>
 <section class="panel" id="report-models"><h2>1．模型设定</h2>${r.models.map(m=>{
  const v=m.supplement;return `<div class="report-model"><h3><a href="#method/${esc(m.id)}">${esc(m.name)} ↗</a> <small>版本 ${m.revision}</small></h3><h4>研究问题与适用范围</h4>${reportBlock(m.spec.problem)}${reportBlock(m.spec.scope)}<h4>主体与行为规则</h4>${reportBlock(v?.actors,'尚未单独整理；可用 AI 补充或人工填写。')}
  ${v?.assumptions?.length?`<table><thead><tr><th>关键假设</th><th>采用理由</th><th>来源</th></tr></thead><tbody>${v.assumptions.map(a=>`<tr><td>${esc(a.statement)}</td><td>${esc(a.reason||'理由尚未核对')}${reportEvidence(a.evidence,r)}</td><td>${reportOrigin(a.origin)}</td></tr>`).join('')}</tbody></table>`:`<h4>已有假设记录</h4>${reportBlock(m.spec.assumptions)}<p class="notice">逐条假设的选择理由与来源尚待整理，不能据此假定 AI 已核对。</p>`}
  <h4>已有模型选择理由</h4>${reportBlock(m.spec.rationale)}<h4>经济机制</h4>${reportBlock(m.spec.mechanisms)}${reportEvidence(m.spec.evidence,r)}</div>`
 }).join('')||'<p class="muted">尚未提取核心模型。</p>'}</section>
 <section class="panel" id="report-equations"><h2>2．公式推导与均衡条件</h2>${r.models.map(m=>`<div class="report-model"><h3>${esc(m.name)}</h3>${m.supplement?.derivations?.length?m.supplement.derivations.map(d=>`<div class="report-derivation"><h4>${esc(d.title)} ${reportOrigin(d.origin)}</h4><h4>目标</h4>${reportBlock(d.objective)}<h4>约束</h4>${reportBlock(d.constraints)}<ol>${d.steps.map(step=>`<li>${reportBlock(step.formula)}${reportBlock(step.explanation)}</li>`).join('')}</ol><h4>最终均衡条件</h4>${d.equilibrium_conditions.map(v=>reportBlock(v)).join('')||'<p class="muted">待补充</p>'}${d.code_links.length?'<h4>方程与代码对应</h4><ul>'+d.code_links.map(l=>`<li>${esc(l.symbol)} → <a href="#tool/${esc(l.tool_id)}">${esc(l.location)} ↗</a></li>`).join('')+'</ul>':''}${reportEvidence(d.evidence,r)}</div>`).join(''):`<h4>已有变量与方程</h4>${reportBlock(m.spec.equations)}<h4>已有求解步骤</h4>${reportBlock(m.spec.steps)}<p class="notice">当前记录不等于完整推导。目标、约束、中间步骤和均衡方程的对应关系需要补充。</p>`}</div>`).join('')}</section>
 <section class="panel" id="report-inputs"><h2>3．参数、数据和代码</h2><h3>关键输入与来源</h3>${r.inputs.length?`<div class="report-table"><table><thead><tr><th>输入</th><th>数值 / 单位</th><th>来源与适用实验</th></tr></thead><tbody>${r.inputs.map(v=>`<tr><td>${esc(v.name)}</td><td>${esc(v.value)}<br><small>${esc(v.unit||'单位待确认')}</small></td><td>${reportOrigin(v.origin)} ${v.asset_ids.map(a=>reportAsset(a,r)).join('、')}<br>${esc(v.experiment_id||'模型公共输入')}<br>${esc(v.note)}${reportEvidence(v.evidence,r)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="notice">关键输入尚未逐项整理；下面保留实际参数契约、数据文件和环境记录。缺少来源的值不会自动当作论文参数。</p>'}
 ${r.tools.map(t=>`<details class="report-tool"><summary>${esc(t.name)} · ${esc(t.language)} · 版本 ${t.revision}</summary><p><a href="#tool/${esc(t.id)}">打开工具与人工审核 ↗</a></p><h4>参数契约（默认值不一定等于每个实验实际输入）</h4><table><tr><th>参数</th><th>默认值</th><th>单位与说明</th></tr>${(t.spec.parameters||[]).map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(reportText(p.default))}</td><td>${esc(p.unit||'单位未注明')} · ${esc(p.description)}</td></tr>`).join('')}</table>${reportDetails('运行环境、依赖及实际版本',t.environment)}${reportDetails('公共工具文件映射',t.spec.bundle_paths)}<details><summary>完整封装代码</summary><pre>${esc(t.code||'代码尚未准备')}</pre></details><h4>重跑材料</h4><p class="muted">${esc(t.replay.note)} 环境依赖与作者材料可能有独立的使用条件。</p>${t.replay.run_ids.map(id=>`<p><a href="/api/papers/${r.paper.id}/report/runs/${id}/package">下载运行 ${id} 的代码、输入、日志与复跑说明 ZIP ↗</a></p>`).join('')||'<p class="muted">尚无当前版本的实际运行包。</p>'}</details>`).join('')}
 <details><summary>原件、数据、参考和代码附件（${r.assets.length}）</summary><table><tr><th>文件</th><th>角色 / 内容哈希</th></tr>${r.assets.map(a=>`<tr><td>${reportAsset(a.id,r)}</td><td>${esc(a.role)}<br><small>${esc(a.sha256)}</small></td></tr>`).join('')}</table></details></section>
 <section class="panel" id="report-results"><h2>4．计算结果与原文对照</h2><p class="muted">按实验展示基准、政策改变与真实计算结果。图表以所选字段的原始单位对比论文值和复现值；变化率、水平值和福利贡献不能混作同一经济含义。</p>
 ${r.experiments.map((e,n)=>`<details class="report-case" ${e.run_id?'open':''}><summary>${badge(e.status)} ${esc(e.name)} ${e.kind==='qualitative'?'<small>定性项，非数值实验</small>':''}</summary><p>${esc(e.description)}</p>${e.supplement?`<div class="grid"><div><h4>基准情景</h4>${reportBlock(e.supplement.baseline)}</div><div><h4>政策改变</h4>${reportBlock(e.supplement.policy_changes)}</div></div><h4>输出含义与对照解释</h4>${reportBlock(e.supplement.output_meaning)}${reportBlock(e.supplement.conclusion)}${reportEvidence(e.supplement.evidence,r)}`:`<h4>已有情景设定</h4>${reportBlock(e.design)}<p class="muted">基准与冲击的独立说明尚待整理。</p>`}
 <p>实际输入：${reportAsset(e.input_asset,r)} · 独立参考：${reportAsset(e.reference_asset,r)}</p>${reportDetails('本实验实际参数与单位',{parameters:e.parameters,units:e.units,bundle_paths:e.bundle_paths})}
 ${e.gaps?.length?'<p class="notice">待处理：'+esc(e.gaps.join('；'))+'</p>':''}${e.error?'<p class="notice">运行错误：'+esc(e.error)+'</p>':''}
 ${e.comparison?`${comparisonHtml(e.comparison)}<label for="report-field-${n}">选择结果字段</label><select id="report-field-${n}">${e.comparison.metrics.map(m=>`<option value="${esc(m.field)}">${esc(m.field)} · ${m.count||0} 个数值</option>`).join('')}</select><div class="actions report-controls"><button class="secondary report-load-result" data-case="${n}">查看逐项数值与对比图</button><span id="report-result-download-${n}"></span></div><div id="report-result-${n}"></div><p><a href="/api/runs/${e.run_id}/report" target="_blank">下载完整运行结果和验收记录 ↗</a></p>`:'<p class="muted">当前版本尚无实际数值验收结果；不会展示推测的数值或通过状态。</p>'}
 ${e.curve_comparison?reportDetails('曲线形状、临界点与覆盖范围检查',e.curve_comparison):''}${e.diagnostics?reportDetails('收敛、约束与实际诊断',e.diagnostics):''}${reportEvidence(e.evidence,r)}</details>`).join('')||'<p class="muted">尚无实验清单。</p>'}</section>
 <section class="panel" id="report-audit"><h2>5．审核与返修记录</h2><h3>需要研究员注意的问题</h3>${unresolved.length?unresolved.map(v=>`<div class="notice"><b>${esc(v.stage)} · ${esc(v.experiment_id||'项目整体')}</b>${reportBlock(v.issue)}<p>对结论的影响：${esc(v.impact||'尚待判断')}</p><p>已做修复：${esc(v.resolution||'尚未修复')}</p><p>复验：${esc(v.revalidation||'尚未复验')}</p>${reportEvidence(v.evidence,r)}</div>`).join(''):'<p class="muted">尚无人工或 AI 单列的未决问题，不代表所有实验已通过。请同时查看下列材料缺口和检查意见。</p>'}
 ${r.experiments.filter(e=>e.gaps?.length||e.status==='failed'||e.status==='stale').map(e=>`<p class="notice">${esc(e.name)}：${esc(e.gaps?.join('；')||e.error||labels[e.status]||e.status)}</p>`).join('')}
 <details open><summary>自动审核与退回证据（${r.audit.checks.length} 条）</summary><div class="report-timeline">${r.audit.checks.map(a=>`<details><summary>${esc(reportStages[a.kind]||a.kind)} · ${esc(reportDecisions[a.decision]||a.decision)} · ${esc(new Date(a.created).toLocaleString())}</summary><p>对象：${esc(a.entity_id)}</p>${reportBlock(a.result.summary)}${a.result.issues?.length?'<h4>退回问题</h4><ul>'+a.result.issues.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>':''}${a.result.checks?.length?'<h4>实际核对说明</h4><ul>'+a.result.checks.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>':''}<small>检查版本绑定：${esc(a.binding)}</small></details>`).join('')||'<p class="muted">尚无自动审核记录。</p>'}</div></details>
 <details><summary>修改与实际复验时间线</summary><p class="muted">修改记录与运行记录分别保存；后一次记录不自动证明前一次问题已解决，以当前版本实际验收为准。</p>${r.audit.revisions.map(v=>`<p>${esc(v.created)} · ${esc(v.entity_type)} ${esc(v.entity_id)} · 版本 ${v.revision}<br>${esc(v.actor)}：${esc(v.reason)}</p>`).join('')}${r.audit.attempts.map(a=>`<details><summary>${badge(a.status)} ${esc(a.experiment_id)} · 清单版本 ${a.plan_revision} · ${esc(a.created)}</summary>${a.run_id?`<a href="/api/runs/${a.run_id}/report" target="_blank">实际运行 ${esc(a.run_id)} ↗</a>`:''}${a.result.comparison?comparisonHtml(a.result.comparison):''}${reportBlock(a.result.error,'未记录运行错误')}</details>`).join('')}</details>
 <details><summary>人工模型 / 工具审核（${r.audit.reviews.length} 条）</summary>${r.audit.reviews.map(v=>`<p>${esc(v.reviewer)} · ${badge(v.status)} · ${esc(v.entity_type)} 版本 ${v.revision}<br>${esc(v.opinion)}</p>`).join('')||'<p class="muted">尚无人工审核。</p>'}</details>
 <h3>报告终审</h3><p>${activeReview?esc(reportDecisions[activeReview.status]||activeReview.status)+' · '+esc(activeReview.reviewer):'当前版本尚无终审记录'}</p><p class="muted">终审记录绑定当前资料与报告说明版本，不会使失败、缺材料或未运行实验变为通过。</p><div class="report-controls"><label>审核人</label><input id="report-reviewer" value="${esc(localStorage.getItem('econlab-reviewer')||'')}"><label>终审意见</label><textarea id="report-opinion" placeholder="仍会影响结论的问题、接受范围或需要继续核对的内容"></textarea><label>报告状态</label><select id="report-review-status"><option value="pending">待处理</option><option value="accepted">确认报告与限制说明</option><option value="disputed">存在异议</option></select><button id="review-report">保存终审记录</button></div>
 ${r.final_reviews.length?reportDetails('历次终审（current 表示适用于当前版本）',r.final_reviews):''}${r.history.length?reportDetails('报告说明修订记录',r.history):''}</section></article>`;
}

function reportChart(page){
 const rows=page.rows.slice(0,12);if(!rows.length)return '';
 const values=rows.flatMap(v=>[v.reference,v.actual]);let min=Math.min(0,...values),max=Math.max(0,...values);if(min===max)max=min+1;
 const width=900,left=270,right=110,span=width-left-right,scale=v=>left+(v-min)/(max-min)*span,zero=scale(0),height=50+rows.length*48;
 const bar=(v,y,color)=>`<rect x="${Math.min(zero,scale(v))}" y="${y}" width="${Math.max(.8,Math.abs(scale(v)-zero))}" height="10" fill="${color}"><title>${esc(v)}</title></rect>`;
 return `<figure class="report-chart"><figcaption>论文值与复现值对照 · ${esc(page.unit||'单位尚未注明')} · 当前页前 ${rows.length} 项</figcaption><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="论文值与复现值对比，详细值见下方表格"><rect x="270" y="8" width="14" height="10" fill="#aaa99c"/><text x="290" y="18">论文值</text><rect x="370" y="8" width="14" height="10" fill="#dba379"/><text x="390" y="18">复现值</text><line x1="${zero}" x2="${zero}" y1="30" y2="${height-5}" stroke="#777"/>${rows.map((v,i)=>{const y=38+i*48;return `<text x="0" y="${y+16}">${esc(v.label.length>32?v.label.slice(0,30)+'…':v.label)}<title>${esc(v.label)}</title></text>${bar(v.reference,y,'#aaa99c')}${bar(v.actual,y+13,'#dba379')}<text x="${width-right+10}" y="${y+16}">${esc(Number(v.actual).toPrecision(5))}</text>`}).join('')}</svg></figure>`;
}

async function loadReportResult(r,n,offset=0){
 const id=r.paper.id,e=r.experiments[n],field=$('#report-field-'+n)?.value,epoch=routeEpoch,box=$('#report-result-'+n);if(!box||!field)return;
 box.innerHTML='<p class="muted">读取实际结果…</p>';
 const params=new URLSearchParams({experiment_id:e.id,field,offset:String(offset),limit:'50'});
 try{
  const page=await api('/papers/'+id+'/report/results?'+params);
  if(epoch!==routeEpoch||tab!=='report'||current?.id!==id||$('#report-field-'+n)?.value!==field)return;
  const csv=new URLSearchParams({experiment_id:e.id,field});$('#report-result-download-'+n).innerHTML=`<a href="/api/papers/${id}/report/results.csv?${esc(csv.toString())}">下载完整逐项对照 CSV ↗</a>`;
  box.innerHTML=reportChart(page)+`<p class="muted">${esc(page.interpretation)}</p><div class="report-table"><table><thead><tr><th>指标 / 地区</th><th>论文值</th><th>复现值</th><th>有符号差值</th><th>相对误差</th><th>复现 / 论文</th><th>符号对照</th><th>验收</th></tr></thead><tbody>${page.rows.map(v=>`<tr><td>${esc(v.label)}</td><td>${esc(v.reference)}</td><td>${esc(v.actual)}</td><td>${esc(v.difference)}</td><td>${v.relative_error==null?'零参考，见绝对容差':esc((v.relative_error*100).toPrecision(6))+'%'}</td><td>${v.actual_to_reference_ratio==null?'—':esc(Number(v.actual_to_reference_ratio).toPrecision(6))+' 倍'}</td><td>${v.same_sign?'同号':'异号 / 零值不同'}</td><td>${badge(v.passed?'passed':'failed')}${v.precision_accepted?'<small>按论文显示精度接受</small>':''}</td></tr>`).join('')}</tbody></table></div><div class="actions report-controls"><button class="secondary" id="report-prev-${n}" ${offset===0?'disabled':''}>上一页</button><span>${offset+1}–${Math.min(page.total,offset+50)} / ${page.total}</span><button class="secondary" id="report-next-${n}" ${offset+50>=page.total?'disabled':''}>下一页</button></div>`;
  $('#report-prev-'+n).onclick=()=>loadReportResult(r,n,Math.max(0,offset-50));$('#report-next-'+n).onclick=()=>loadReportResult(r,n,offset+50);
 }catch(err){if(epoch===routeEpoch&&tab==='report'&&current?.id===id)box.innerHTML='<p class="notice">'+esc(err.message)+'</p>'}
}

function wireReport(r,box){
 $('#refresh-report').onclick=()=>renderReproductionReport(current,box);
 box.querySelectorAll('[data-report-anchor]').forEach(a=>a.onclick=event=>{event.preventDefault();document.getElementById(a.dataset.reportAnchor)?.scrollIntoView({behavior:'smooth',block:'start'})});
 $('#generate-report').onclick=async event=>{event.target.disabled=true;try{const job=await api('/papers/'+r.paper.id+'/report/generate',{method:'POST',body:{revision:r.supplement.revision,basis:r.basis}});$('#stop-report').disabled=false;watchJob(job.id,'#report-job',()=>renderReproductionReport(current,box))}catch(e){notify(e.message);event.target.disabled=false}};
 $('#stop-report').onclick=async event=>{event.target.disabled=true;try{await api('/papers/'+r.paper.id+'/stop',{method:'POST'});await refreshPaper();notify('AI 任务已停止，现有报告和复现记录保留')}catch(e){notify(e.message);event.target.disabled=false}};
 $('#edit-report').onclick=()=>editReport(r,box);
 $('#print-report').onclick=()=>window.print();
 box.querySelectorAll('.report-load-result').forEach(b=>b.onclick=()=>loadReportResult(r,Number(b.dataset.case)));
 $('#review-report').onclick=async event=>{event.target.disabled=true;try{await api('/papers/'+r.paper.id+'/report/review',{method:'POST',body:{revision:r.supplement.revision,basis:r.basis,status:$('#report-review-status').value,reviewer:$('#report-reviewer').value.trim(),opinion:$('#report-opinion').value.trim()}});notify('终审已保存，数值验收状态未改变');await renderReproductionReport(current,box)}catch(e){notify(e.message);event.target.disabled=false}};
}

function editReport(r,box){
 const d=JSON.parse(JSON.stringify(r.supplement.document));
 if(!d.models.length)d.models=r.models.map(m=>({method_id:m.id,actors:'',assumptions:[{statement:reportText(m.spec.assumptions),reason:'',origin:'unknown',evidence:[]}],derivations:[]}));
 const names={models:'模型主体、假设理由和逐步推导',inputs:'关键输入：值、单位和来源',experiments:'基准、政策变化与结果解释',issues:'未决问题及修复复验说明'};
 modal(`<h2>人工修改报告说明</h2><p>说明层与计算记录分开保存。不能在这里修改实际结果或通过状态。AI 自行设定用 ai_assumption，补充推导用 ai_derivation，来源未核对用 unknown；原文内容用 paper 并提供证据。</p><label>编辑人</label><input id="report-editor"><label>修改原因</label><input id="report-edit-reason">${Object.entries(names).map(([key,label])=>`<label>${esc(label)}（JSON 列表）</label><textarea class="code" id="report-edit-${key}">${esc(JSON.stringify(d[key],null,2))}</textarea>`).join('')}<details><summary>字段格式示例</summary><pre>${esc(JSON.stringify(reportExamples(r),null,2))}</pre></details><button id="save-report-edit">保存报告说明</button>`);
 $('#save-report-edit').onclick=async event=>{event.target.disabled=true;try{const document=Object.fromEntries(Object.keys(names).map(k=>[k,JSON.parse($('#report-edit-'+k).value)]));await api('/papers/'+r.paper.id+'/report',{method:'PUT',body:{document,revision:r.supplement.revision,basis:r.basis,actor:$('#report-editor').value.trim(),reason:$('#report-edit-reason').value.trim()}});$('#modal').close();await renderReproductionReport(current,box);notify('报告说明已保存，模型和实验配置未改变')}catch(e){notify(e.message);event.target.disabled=false}};
}

function reportExamples(r){
 return {models:[{method_id:r.models[0]?.id||'方法ID',actors:'居民、企业及其决策',assumptions:[{statement:'具体规则',reason:'采用理由与影响',origin:'unknown',evidence:[]}],derivations:[{title:'推导名称',objective:'目标函数',constraints:'预算等约束',steps:[{formula:'\\(公式\\)',explanation:'变换或一阶条件说明'}],equilibrium_conditions:['最终方程'],code_links:r.tools[0]?[{tool_id:r.tools[0].id,symbol:'方程符号',location:'文件与函数名'}]:[],origin:'ai_derivation',evidence:[]}]}],inputs:[{method_id:r.models[0]?.id||'方法ID',experiment_id:r.experiments[0]?.id||'',name:'参数名称',value:'实际数值或未获取',unit:'单位',origin:'unknown',asset_ids:[],evidence:[],note:'来源和限制'}],experiments:r.experiments[0]?[{experiment_id:r.experiments[0].id,baseline:'基准规则',policy_changes:'冲击及保持不变的条件',output_meaning:'变化率或水平值含义',conclusion:'依据实际结果填写',evidence:[]}]:[],issues:[{experiment_id:'',stage:'输入适配',issue:'需要核对的问题',evidence:[],impact:'对结论的影响',resolution:'已作修改',revalidation:'实际复验记录或尚未复验',status:'open'}]};
}
