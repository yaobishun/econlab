"""Versioned paper experiment inventories and host-owned reproduction proofs.

An inventory is related data for the three libraries, never a fourth library.
Neither the model nor a tool's old validation badge can grant paper eligibility.
"""
from __future__ import annotations
import copy, json, math
import store, catalog


def init():
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS experiment_plans(
          paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
          revision INTEGER NOT NULL, document TEXT NOT NULL, updated TEXT);
        CREATE TABLE IF NOT EXISTS experiment_plan_history(
          id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          revision INTEGER, document TEXT, actor TEXT, reason TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS experiment_approvals(
          id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          revision INTEGER, binding TEXT, reviewer TEXT, opinion TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS experiment_attempts(
          id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          experiment_id TEXT, plan_revision INTEGER, signature TEXT, status TEXT,
          run_id TEXT, result TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS qualitative_checks(
          id TEXT PRIMARY KEY,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          experiment_id TEXT,revision INTEGER,binding TEXT,reviewer TEXT,opinion TEXT,created TEXT);
        CREATE INDEX IF NOT EXISTS experiment_attempt_scope ON experiment_attempts(paper_id,experiment_id,created);
        CREATE TABLE IF NOT EXISTS paper_controllers(
          paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
          state TEXT NOT NULL, updated TEXT);
        CREATE TABLE IF NOT EXISTS paper_project_memory(
          paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
          memory TEXT NOT NULL, updated TEXT);
        CREATE TABLE IF NOT EXISTS supplementary_pages(
          asset_id TEXT REFERENCES assets(id) ON DELETE CASCADE,
          page INTEGER,text TEXT,visual_notes TEXT,PRIMARY KEY(asset_id,page));
        CREATE TABLE IF NOT EXISTS inventory_drafts(
          id TEXT PRIMARY KEY,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          stage TEXT,proposal TEXT,errors TEXT,created TEXT);
        ''')


def raw(pid):
    with store.db() as c:
        r=c.execute('SELECT * FROM experiment_plans WHERE paper_id=?',(pid,)).fetchone()
    return {'paper_id':pid,'revision':r['revision'],'document':store.decode(r['document']),'updated':r['updated']} if r else None


def save_draft(pid,stage,proposal,errors=None):
    with store.db() as c:c.execute('INSERT INTO inventory_drafts VALUES(?,?,?,?,?,?)',(store.uid(),pid,stage,store.encode(proposal),store.encode(errors or []),store.now()))


def draft(pid):
    with store.db() as c:r=c.execute('SELECT * FROM inventory_drafts WHERE paper_id=? ORDER BY rowid DESC LIMIT 1',(pid,)).fetchone()
    if not r:return None
    return {**dict(r),'proposal':store.decode(r['proposal']),'errors':store.decode(r['errors'])}


def _paper(pid):
    with store.db() as c:
        p=catalog._decode(catalog._row(c,'papers',pid))
        p['page_count']=c.execute('SELECT count(*) FROM pages WHERE paper_id=?',(pid,)).fetchone()[0]
    return p


def _refs(pid,refs):
    return catalog.check_evidence(pid,refs,True)


def normalize(pid,document):
    """Validate an entire replacement atomically; omissions cannot keep old proofs."""
    p=_paper(pid);doc=copy.deepcopy(document)
    if not isinstance(doc,dict) or set(doc)-{'experiments','coverage','pages_audited','notes','appendices'}:
        raise ValueError('Invalid experiment inventory fields')
    rows=doc.get('experiments');coverage=doc.get('coverage')
    if not isinstance(rows,list) or not rows or len(rows)>500:raise ValueError('Inventory requires 1–500 experiments')
    if not isinstance(coverage,list) or len(coverage)>2000:raise ValueError('Invalid figure/table coverage inventory')
    pages=doc.get('pages_audited',[])
    if not isinstance(pages,list) or any(type(n) is not int or not 1<=n<=p['page_count'] for n in pages):raise ValueError('Invalid audited PDF pages')
    doc['pages_audited']=sorted(set(pages))
    with store.db() as c:
        mids={r[0] for r in c.execute('SELECT method_id FROM method_sources WHERE paper_id=?',(pid,))}
        tids={r[0] for r in c.execute('SELECT tool_id FROM tool_sources WHERE paper_id=?',(pid,))}
        assets={r['id']:dict(r) for r in c.execute('SELECT id,role,sha256 FROM assets WHERE paper_id=?',(pid,))}
    ids=set()
    allowed={'id','name','method_id','tool_id','kind','required','exclusion_reason','evidence','figures','dependencies',
             'input_asset','asset_ids','bundle_paths','parameters','reference_asset','validation_fields','zero_tolerance',
             'units','curve_checks','gaps','dependency_bindings','description','preparation_task_id','dependency_notes','observations','dependency_materials','qualitative_report_asset','qualitative_check_mode','design','unit_notes','related_experiments','publication_precision'}
    for e in rows:
        if not isinstance(e,dict) or set(e)-allowed:raise ValueError('Unknown experiment field; states/proofs are host-owned')
        eid=e.get('id','')
        if not isinstance(eid,str) or not eid or len(eid)>120 or eid in ids:raise ValueError('Experiment IDs must be unique stable keys')
        ids.add(eid)
        if not e.get('name') or e.get('method_id') not in mids:raise ValueError('Experiment must map to an existing core model of this paper')
        if e.get('tool_id'):
            if e['tool_id'] not in tids or catalog.get('tool',e['tool_id'])['method_id']!=e['method_id']:raise ValueError('Experiment solver/model mapping is outside this paper')
        e.setdefault('kind','numeric');e.setdefault('required',True)
        if e['kind'] not in ('numeric','curve','qualitative') or type(e['required']) is not bool:raise ValueError('Invalid experiment type')
        if not e['required'] and not str(e.get('exclusion_reason','')).strip():raise ValueError('Excluded experiments require a recorded reason and renewed human review')
        e['evidence']=_refs(pid,e.get('evidence',[]))
        if not e['evidence']:raise ValueError('Each experiment requires original page evidence')
        if e.get('preparation_task_id'):
            with store.db() as c:task=c.execute('SELECT paper_id,state FROM execution_tasks WHERE id=?',(e['preparation_task_id'],)).fetchone()
            if not task or task['paper_id']!=pid or store.decode(task['state']).get('experiment_id')!=eid:raise ValueError('Preparation task must belong to this paper experiment')
        for field,default in [('dependencies',[]),('asset_ids',[]),('bundle_paths',{}),('parameters',{}),('validation_fields',[]),('zero_tolerance',{}),('units',{}),('curve_checks',[]),('gaps',[]),('figures',[]),('dependency_bindings',[]),('dependency_materials',[]),('related_experiments',[])]:e.setdefault(field,default)
        for k in ('dependencies','asset_ids','validation_fields','curve_checks','gaps','figures','dependency_bindings','dependency_notes','observations','dependency_materials','related_experiments'):
            if k in e and not isinstance(e[k],list):raise ValueError(eid+'.'+k+': expected a list, got '+type(e[k]).__name__)
        for k in ('dependency_notes','observations','gaps','figures','dependencies','asset_ids'):
            if any(not isinstance(v,str) for v in e.get(k,[])):raise ValueError(eid+'.'+k+': items must be text')
        if e.get('unit_notes') is not None and not isinstance(e['unit_notes'],dict):raise ValueError('Unit notes must be an object')
        if set(e['units'])-set(e['validation_fields']):raise ValueError(eid+'.units must use actual validation field names; grouped descriptions belong in unit_notes')
        if e.get('design') is not None:
            if not isinstance(e['design'],dict):raise ValueError('Experiment design must be an object')
            json.dumps(e['design'],allow_nan=False)
        if any(not isinstance(e[k],dict) for k in ('bundle_paths','parameters','zero_tolerance','units')):raise ValueError('Invalid experiment configuration')
        if any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() for k,v in e['units'].items()):raise ValueError('Units must be nonempty text keyed by output fields')
        if any(not isinstance(f,str) or not f for f in e['validation_fields']) or len(set(e['validation_fields']))!=len(e['validation_fields']):raise ValueError('Invalid validation fields')
        if e.get('publication_precision') is not None:
            import runner
            runner.check_publication_precision(e['publication_precision'],e['validation_fields'])
            e['publication_precision']['evidence']=_refs(pid,e['publication_precision']['evidence'])
        for tolerance in e['zero_tolerance'].values():
            if type(tolerance) not in (float,int) or not math.isfinite(tolerance) or tolerance<0:raise ValueError('Zero tolerances must be finite and nonnegative')
        if e.get('qualitative_report_asset'):
            q=catalog.asset(e['qualitative_report_asset'])
            if e['kind']!='qualitative' or q['paper_id']!=pid or q['role']!='derivation' or e.get('qualitative_check_mode') not in ('analytic','documentary'):raise ValueError('Qualitative report must be a sourced derivation of this paper')
        for aid in set(e['asset_ids'])|set(e['bundle_paths'])|{e[k] for k in ('input_asset','reference_asset') if e.get(k)}:
            if aid not in assets:raise ValueError('Experiment asset outside source paper')
        if e.get('input_asset') and assets[e['input_asset']]['role']!='data':raise ValueError('Experiment input requires data role')
        if e.get('reference_asset') and assets[e['reference_asset']]['role']!='reference':raise ValueError('Independent reference requires reference role')
        if any(assets[aid]['role']=='reference' for aid in e['bundle_paths']):raise ValueError('Independent reference cannot enter solver workspace')
        catalog.check_tool_contract({'parameters':[],'bundle_paths':e['bundle_paths'],'validation_fields':e['validation_fields']})
        for check in e['curve_checks']:
            if not isinstance(check,dict) or set(check)-{'field','type','direction','x_field','min','max','index','index_tolerance','start','stop'}:raise ValueError('Invalid curve check')
            if check.get('type') not in ('monotonic','extremum','coverage'):raise ValueError('Unsupported curve rule')
            if check.get('field') not in e['validation_fields']:raise ValueError('Curve check must use a validated output field')
            if check['type']=='monotonic' and check.get('direction') not in ('increasing','decreasing'):raise ValueError('Declare curve direction')
            if any(type(check[k]) is not int or check[k]<0 for k in ('start','stop') if k in check):raise ValueError('Curve segment bounds must be nonnegative integers')
            if check['type']=='coverage' and (not check.get('x_field') or any(type(check.get(k)) not in (int,float) or not math.isfinite(check[k]) for k in ('min','max')) or check['min']>=check['max']):raise ValueError('Declare curve coverage interval')
            if check['type']=='extremum' and (check.get('direction') not in ('min','max') or type(check.get('index')) is not int or check['index']<0 or type(check.get('index_tolerance',0)) is not int or check.get('index_tolerance',0)<0):raise ValueError('Declare critical point and index tolerance')
        material_deps=set()
        staged=set(e['asset_ids'])|set(e['bundle_paths'])|{e.get('input_asset')}
        for material in e['dependency_materials']:
            if not isinstance(material,dict) or set(material)!={'experiment_id','asset_ids','reason'} or material['experiment_id'] not in e['dependencies'] or material['experiment_id'] in material_deps:raise ValueError('Invalid material-satisfied dependency')
            sources=material['asset_ids']
            if not isinstance(sources,list) or not sources or not material['reason'].strip() or any(a not in assets or assets[a]['role']!='data' or a not in staged for a in sources):raise ValueError('Dependency material must be actual staged data of this experiment, with a reason')
            material_deps.add(material['experiment_id'])
        for binding in e['dependency_bindings']:
            if isinstance(binding,dict) and binding.get('experiment_id') in material_deps:raise ValueError('Dependency cannot use both a prior run and a supplied data snapshot')

            if not isinstance(binding,dict) or set(binding)!={'experiment_id','path'} or binding['experiment_id'] not in e['dependencies']:raise ValueError(eid+'.dependency_bindings: expected {experiment_id, path} linked to a declared predecessor')
            from pathlib import PurePosixPath
            path=binding['path']
            if not isinstance(path,str) or not path or '\\' in path or ':' in path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or path.lower() in ('request.json','result.json','tool.py','result.mat','stdout.log','stderr.log'):raise ValueError('Unsafe dependency output path')
    byid={e['id']:e for e in rows}
    visiting=set();done=set()
    def visit(eid):
        if eid in visiting:raise ValueError('Experiment dependency cycle')
        if eid in done:return
        if eid not in byid:raise ValueError('Missing dependency experiment')
        visiting.add(eid)
        for dep in byid[eid]['dependencies']:
            if not isinstance(dep,str):raise ValueError('Invalid dependency ID')
            visit(dep)
        visiting.remove(eid);done.add(eid)
    for eid in byid:visit(eid)
    for e in rows:
        for relation in e['related_experiments']:
            if not isinstance(relation,dict) or set(relation)!={'experiment_id','relationship','reason'} or relation['experiment_id'] not in ids or relation['experiment_id']==e['id'] or relation['relationship'] not in ('comparison','shared_procedure') or not str(relation['reason']).strip():raise ValueError('Invalid related experiment; comparisons and shared procedures are not runtime inputs')
    for e in rows:
        if e['required'] and any(not byid[d]['required'] for d in e['dependencies']):raise ValueError('Required experiments cannot depend on excluded prerequisites')
    labels=set()
    for item in coverage:
        if not isinstance(item,dict) or set(item)-{'label','page','asset_id','experiment_ids','noncomputational_reason','notes','evidence'}:raise ValueError('Invalid figure/table coverage item')
        total=p['page_count']
        if item.get('asset_id'):
            import fitz
            a=catalog.asset(item['asset_id'])
            if a['paper_id']!=pid or a['role']!='appendix':raise ValueError('Coverage appendix outside source paper')
            with fitz.open(catalog.asset_path(a['id'])) as pdf:total=len(pdf)
        if not item.get('label') or type(item.get('page')) is not int or not 1<=item['page']<=total:raise ValueError('Figure/table requires label and absolute page')
        key=(item.get('asset_id',''),item['page'],item['label'])
        if key in labels:raise ValueError('Duplicate figure/table coverage item')
        labels.add(key)
        links=item.get('experiment_ids',[])
        if not isinstance(links,list) or any(eid not in ids for eid in links):raise ValueError('Coverage links unknown experiment')
        if not links and not str(item.get('noncomputational_reason','')).strip():raise ValueError('Every figure/table needs experiments or an explicit noncomputational reason')
        if links and item.get('noncomputational_reason'):raise ValueError('A figure/table cannot be both computational and excluded')
        item['evidence']=_refs(pid,item.get('evidence',[]))
        if not item['evidence']:raise ValueError('Coverage item requires original evidence')
    # A named figure must occur in the audited coverage inventory (and map back).
    for e in rows:
        for label in e['figures']:
            if not any(i['label']==label and e['id'] in i.get('experiment_ids',[]) for i in coverage):raise ValueError(e['id']+'.figures: missing coverage mapping for exact label '+repr(label)+' linking this experiment')
    return doc


def appendix_bindings(pid):
    import fitz
    with store.db() as c:assets=[dict(r) for r in c.execute("SELECT id,sha256 FROM assets WHERE paper_id=? AND role='appendix'",(pid,))]
    rows=[]
    for a in assets:
        try:
            with fitz.open(catalog.asset_path(a['id'])) as pdf:total=len(pdf)
        except Exception as exc:raise ValueError('附录必须是可读取的PDF，不能跳过未读取的附录：'+a['id']) from exc
        rows.append({'asset_id':a['id'],'sha256':a['sha256'],'pages_audited':list(range(1,total+1))})
    return rows


def save(pid,document,expected_revision,actor,reason):
    if not str(actor).strip() or not str(reason).strip():raise ValueError('Inventory changes require editor and reason')
    document=normalize(pid,document)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT revision FROM experiment_plans WHERE paper_id=?',(pid,)).fetchone()
        if (old[0] if old else 0)!=expected_revision:raise ValueError('Version conflict: experiment inventory changed')
        revision=expected_revision+1;now=store.now()
        c.execute('INSERT OR REPLACE INTO experiment_plans VALUES(?,?,?,?)',(pid,revision,store.encode(document),now))
        c.execute('INSERT INTO experiment_plan_history VALUES(?,?,?,?,?,?,?)',(store.uid(),pid,revision,store.encode(document),actor,reason,now))
    remember(pid,event={'kind':'inventory_revision','revision':revision,'actor':actor,'reason':reason})
    return raw(pid)


def binding(plan):
    """Review snapshot includes current model/code/assets/env, not mutable labels alone."""
    if sorted(plan['document'].get('appendices',[]),key=lambda a:a['asset_id'])!=sorted(appendix_bindings(plan['paper_id']),key=lambda a:a['asset_id']):raise ValueError('附录有新增或变动，尚未纳入完整实验清单')
    stamps=[]
    for e in plan['document']['experiments']:
        method=catalog.get('method',e['method_id'])
        tool=catalog.get('tool',e['tool_id']) if e.get('tool_id') else None
        aids=set(e['asset_ids'])|set(e['bundle_paths'])|{e[k] for k in ('input_asset','reference_asset') if e.get(k)}
        hashes=[]
        for aid in sorted(aids):
            a=catalog.asset(aid);catalog.asset_path(aid);hashes.append((aid,a['sha256']))
        stamps.append({'id':e['id'],'method_revision':method['revision'],'method_review':method['review_status'],
                       'tool_fingerprint':catalog.fingerprint(tool) if tool else None,'tool_review':tool['review_status'] if tool else None,'environment':environment_binding(tool) if tool else None,'assets':hashes})
    return store.digest({'revision':plan['revision'],'document':plan['document'],'bindings':stamps})


def scope_binding(plan):
    """Scientific scope survives attachment/config preparation, never exclusions."""
    doc=plan['document']
    keys=('id','name','method_id','kind','required','exclusion_reason','dependencies','figures','evidence','description','observations','design','unit_notes','related_experiments')
    return store.digest({'pages':doc['pages_audited'],'coverage':doc['coverage'],'notes':doc.get('notes',''),'reading_coverage':_paper(plan['paper_id'])['coverage'],'appendices':doc.get('appendices',[]),
        'experiments':[{k:e[k] for k in keys if k in e} for e in doc['experiments']],'current_appendices':appendix_bindings(plan['paper_id'])})


def case_binding(plan,e):
    e=next(v for v in plan['document']['experiments'] if v['id']==e['id'])
    tool=catalog.get('tool',e['tool_id'])
    method=catalog.get('method',e['method_id'])
    aids=set(e['asset_ids'])|set(e['bundle_paths'])|{e[k] for k in ('input_asset','reference_asset') if e.get(k)}
    assets=[]
    for aid in sorted(aids):
        a=catalog.asset(aid);catalog.asset_path(aid);assets.append((aid,a['sha256']))
    return store.digest({'paper_id':plan['paper_id'],'source_appendices':appendix_bindings(plan['paper_id']),'experiment':e,'tool':catalog.fingerprint(tool),
        'model_review':method['review_status'],'tool_review':tool['review_status'],
        'environment':environment_binding(tool),'assets':assets})


def case_checked(plan,e):
    import automation
    if not e.get('tool_id'):return False
    try:return automation.checked('experiment',plan['paper_id']+':'+e['id'],case_binding(plan,e))
    except (ValueError,OSError):return False


def approved(plan):
    if not plan:return False
    try:stamp=binding(plan)
    except (ValueError,OSError):return False
    with store.db() as c:manual=bool(c.execute('SELECT 1 FROM experiment_approvals WHERE paper_id=? AND revision=? AND binding=?',(plan['paper_id'],plan['revision'],stamp)).fetchone())
    import automation
    if manual or automation.checked('plan',plan['paper_id'],stamp):return True
    return automation.checked('scope',plan['paper_id'],scope_binding(plan)) and all(case_checked(plan,e) for e in plan['document']['experiments'] if e['required'] and e['kind']!='qualitative')


def execution_approved(plan,e):
    import automation
    return approved(plan) or (automation.checked('scope',plan['paper_id'],scope_binding(plan)) and case_checked(plan,e))


def qualitative_binding(plan,e):
    e=next(v for v in plan['document']['experiments'] if v['id']==e['id']);m=catalog.get('method',e['method_id'])
    return store.digest({'paper_id':plan['paper_id'],'source_appendices':appendix_bindings(plan['paper_id']),'experiment':e,'model_revision':m['revision'],'model_spec':m['spec'],'model_review':m['review_status']})


def review(pid,revision,reviewer,opinion):
    plan=raw(pid)
    if not plan or plan['revision']!=revision:raise ValueError('Version conflict: reload experiment inventory')
    if not reviewer.strip() or not opinion.strip():raise ValueError('Record reviewer and coverage/code/data/reference checks')
    preflight(plan)
    stamp=binding(plan)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT revision FROM experiment_plans WHERE paper_id=?',(pid,)).fetchone()
        if not current or current[0]!=revision or binding(plan)!=stamp:raise ValueError('Version conflict during experiment approval')
        c.execute('INSERT INTO experiment_approvals VALUES(?,?,?,?,?,?,?)',(store.uid(),pid,revision,stamp,reviewer,opinion,store.now()))
    remember(pid,event={'kind':'batch_review','revision':revision,'reviewer':reviewer,'opinion':opinion})
    return summary(pid)


def preflight(plan,experiment_id=None):
    if not plan:raise ValueError('Experiment inventory missing')
    pid=plan['paper_id']
    p=_paper(pid)
    if not p['coverage'].get('completed') or plan['document']['pages_audited']!=list(range(1,p['page_count']+1)):raise ValueError('Review requires every original PDF page audited for experiments')
    if not any(e['required'] for e in plan['document']['experiments']):raise ValueError('At least one experiment must remain required')
    for e in plan['document']['experiments']:
        if not e['required'] or (experiment_id is not None and e['id']!=experiment_id):continue
        if e['kind']=='qualitative':continue
        if missing(e):raise ValueError(e['name']+': '+ '; '.join(missing(e)))
        t=catalog.get('tool',e['tool_id']);m=catalog.get('method',e['method_id'])
        import automation
        if not automation.allowed('tool',t) or not automation.allowed('method',m):raise ValueError('Complete model and solver automatic checks or human review before batch approval')
        import runner,environments
        path=catalog.asset_path(e['reference_asset'])
        if path.suffix.lower()=='.mat':
            from scipy.io import loadmat
            reference=loadmat(path,mat_dtype=True)
        else:reference=json.loads(path.read_text(encoding='utf-8-sig'))
        if not runner.compare(reference,reference,e['validation_fields'],e['zero_tolerance'],e.get('publication_precision'))['passed']:raise ValueError('参考字段必须是非空有限数值；零参考需先明确绝对容差，再审核')
        if environments.status(t)['status'] not in ('host_runtime','ready'):raise ValueError('请先配置并检测运行环境，再冻结审核')


def missing(e):
    gaps=list(e.get('gaps',[]))
    if not e.get('tool_id'):gaps.append('缺少公共求解器')
    if e.get('kind')!='qualitative':
        for k,label in [('input_asset','真实输入'),('reference_asset','独立参考'),('validation_fields','验收字段'),('units','输出单位')]:
            if not e.get(k):gaps.append('缺少'+label)
        if any(f not in e.get('units',{}) for f in e.get('validation_fields',[])):gaps.append('每个验收字段均需声明单位')
        if e.get('kind')=='curve' and {r.get('type') for r in e.get('curve_checks',[])}!={'monotonic','extremum','coverage'}:gaps.append('曲线需固定形状、临界点与覆盖区间规则')
    if e.get('tool_id'):
        tool=catalog.get('tool',e['tool_id'])
        if not tool['code']:gaps.append('求解器代码尚未准备')
        import automation
        if automation.enabled() and tool['review_status']!='confirmed' and e.get('kind')!='qualitative' and not e.get('preparation_task_id'):gaps.append('尚未准备当前实验的输入与执行包映射')
    return list(dict.fromkeys(gaps))


def rows(pid):
    with store.db() as c:items=c.execute('SELECT * FROM experiment_attempts WHERE paper_id=? ORDER BY rowid DESC',(pid,)).fetchall()
    return [{**dict(r),'result':store.decode(r['result'],{})} for r in items]


def signature(plan,e,dependencies):
    return store.digest({'case':case_binding(plan,e),'dependencies':dependencies})


def environment_binding(tool):
    import environments,sys
    runtime=environments.status(tool)
    return {'status':runtime['status'],'configuration':runtime.get('configuration'),'detected':runtime.get('detected'),'executable':runtime.get('executable',sys.executable if tool['language']=='python' else None)}


def summary(pid):
    plan=raw(pid)
    if not plan:return {'paper_id':pid,'revision':0,'approved':False,'status':'needs_inventory','eligible':False,'experiments':[],'coverage':[],'counts':{'passed':0,'required':0,'completed':0},'reason':'尚未建立完整实验清单；旧的单项验证不代表整篇复现完成'}
    attempts=rows(pid);byid={e['id']:e for e in plan['document']['experiments']};resolved={};valid_review=approved(plan)
    try:review_stamp=binding(plan)
    except (ValueError,OSError):review_stamp=None
    def resolve(eid):
        if eid in resolved:return resolved[eid]
        e=byid[eid];deps=[resolve(d) for d in e['dependencies']];dep_proofs={d['id']:d.get('run_id') for d in deps if d['id'] not in {v['experiment_id'] for v in e.get('dependency_materials',[])}}
        item=copy.deepcopy(e);item['attempts']=[a for a in attempts if a['experiment_id']==eid][:10]
        gaps=missing(e) if e['kind']!='qualitative' else list(e.get('gaps',[]))
        stamp=None
        try:stamp=signature(plan,e,dep_proofs) if e.get('tool_id') else None
        except (ValueError,OSError) as exc:gaps.append(str(exc))
        history=[a for a in attempts if a['experiment_id']==eid]
        current=next((a for a in history if a['signature']==stamp),None) if stamp else None
        if not e['required']:status='excluded'
        elif e['kind']=='qualitative':
            with store.db() as c:q=c.execute('SELECT 1 FROM qualitative_checks WHERE paper_id=? AND experiment_id=? AND revision=? AND binding=?',(pid,eid,plan['revision'],review_stamp)).fetchone()
            import automation
            automatic=automation.checked('qualitative',pid+':'+eid,qualitative_binding(plan,e))
            status='qualitative_checked' if q or automatic else 'qualitative_review_required'
            item['qualitative_check_mode']='manual' if q else 'automatic' if automatic else None
        elif gaps:status='missing_materials'
        elif any(d['status'] not in ('passed','qualitative_checked') for d in deps if d['id'] not in {v['experiment_id'] for v in e.get('dependency_materials',[])}):status='waiting_dependency'
        elif current:
            status=current['status']
            if status=='passed':
                with store.db() as c:r=c.execute('SELECT status,fingerprint FROM runs WHERE id=?',(current['run_id'],)).fetchone()
                if not r or r['status']!='passed' or r['fingerprint']!=stamp:status='stale'
        elif history:status='stale'
        else:status='not_run'
        item.update(status=status,gaps=gaps,signature=stamp,run_id=current['run_id'] if current else None,result=current['result'] if current else {},dependencies_current=dep_proofs)
        resolved[eid]=item;return item
    items=[resolve(e['id']) for e in plan['document']['experiments']]
    required=[e for e in items if e['required']];passed=sum(e['status']=='passed' for e in required)
    checked=sum(e['status']=='qualitative_checked' for e in required)
    complete=passed>0 and passed+checked==len(required) and valid_review
    counts={state:sum(e['status']==state for e in required) for state in ('passed','failed','missing_materials','not_run','stale','running','waiting_dependency','qualitative_review_required','qualitative_checked','interrupted')}
    counts.update(required=len(required),completed=passed+checked,total=len(items),excluded=len(items)-len(required))
    status='complete' if complete else 'awaiting_review' if not valid_review else 'partial' if passed else 'pending'
    return {**plan,'approved':valid_review,'status':status,'eligible':complete,'experiments':items,'coverage':plan['document']['coverage'],'counts':counts,'reason':'全部必验实验在当前审核版本通过' if complete else '完整清单须审核，且全部必验项在当前版本通过；定性核对不代替定量复现'}


def check_qualitative(pid,eid,revision,reviewer,opinion):
    p=raw(pid)
    if not p or p['revision']!=revision:raise ValueError('Version conflict')
    e=next((e for e in p['document']['experiments'] if e['id']==eid),None)
    if not e or e['kind']!='qualitative':raise ValueError('Only qualitative items accept manual qualitative checks')
    if not reviewer.strip() or not opinion.strip():raise ValueError('Record the qualitative evidence and reviewer')
    stamp=binding(p)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT revision FROM experiment_plans WHERE paper_id=?',(pid,)).fetchone()[0]!=revision:raise ValueError('Version conflict')
        c.execute('INSERT INTO qualitative_checks VALUES(?,?,?,?,?,?,?,?)',(store.uid(),pid,eid,revision,stamp,reviewer,opinion,store.now()))
    remember(pid,event={'kind':'qualitative_check','experiment_id':eid,'reviewer':reviewer,'opinion':opinion})
    return summary(pid)


def require_eligible(tool):
    import automation
    if not automation.allowed('tool',tool):raise ValueError('Solver check is not current')
    method=catalog.get('method',tool['method_id'])
    if not automation.allowed('method',method):raise ValueError('Core model requires a current automatic check or human review')
    for source in tool['sources']:
        state=summary(source['id'])
        if state['eligible'] and any(e['tool_id']==tool['id'] and e['method_id']==tool['method_id'] and e['status']=='passed' for e in state['experiments']):return state
    raise ValueError('整篇论文的完整实验清单尚未全部通过；单个工具或实验通过不能用于问题求解')


def public_summary(pid):
    """Keep polling bounded; full arrays and process logs stay in downloadable runs."""
    result=summary(pid)
    candidate=draft(pid)
    if candidate and (not result['revision'] or candidate['created']>result['updated']):
        proposal=candidate['proposal'];parts=proposal.get('parts',[proposal]);rows=[e for part in parts for e in part.get('experiments',[])]
        result['draft']={'stage':candidate['stage'],'created':candidate['created'],'errors':candidate['errors'],'candidate_count':len({e['id'] for e in rows}),'experiments':[{k:e[k] for k in ('id','name','kind','description','figures') if k in e} for e in rows]}
    for e in result['experiments']:
        e['result']={k:v for k,v in e['result'].items() if k in ('run_id','status','error','comparison','curve_comparison','finished')}
        e['attempts']=[{k:a[k] for k in ('id','status','run_id','plan_revision','created','updated')} for a in e.get('attempts',[])]
    return result


def execute(pid,eid,gate=lambda:None):
    """Run one frozen approved configuration through the actual existing runner."""
    import runner
    state=summary(pid)
    e=next((e for e in state['experiments'] if e['id']==eid),None)
    if not e or not e['required'] or e['kind']=='qualitative':raise ValueError('Not a required quantitative experiment')
    if not execution_approved(state,e):raise ValueError('实验计划、模型、代码、数据或环境已变化，请重新审核')
    if missing(e) or e['status']=='waiting_dependency':raise ValueError('Experiment materials/dependencies are incomplete')
    gate();aid=store.uid();stamp=e['signature'];now=store.now()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM experiment_attempts WHERE paper_id=? AND status='running'",(pid,)).fetchone():raise ValueError('A paper experiment is already running')
        c.execute('INSERT INTO experiment_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,pid,eid,state['revision'],stamp,'running',None,'{}',now,now))
    try:
        dependencies=[]
        for b in e['dependency_bindings']:
            dep=next(d for d in state['experiments'] if d['id']==b['experiment_id'])
            with store.db() as c:r=c.execute('SELECT result FROM runs WHERE id=?',(dep['run_id'],)).fetchone()
            output=store.decode(r[0]).get('outputs') if r else None
            if output is None:raise ValueError('Dependency has no persisted actual outputs; rerun its experiment')
            dependencies.append({'path':b['path'],'outputs':output,'run_id':dep['run_id']})
        result=runner.execute(e['tool_id'],e['parameters'],True,pid,experiment={**e,'proof_signature':stamp,'dependency_outputs':dependencies})
        gate()
        current=summary(pid)
        latest=next(v for v in current['experiments'] if v['id']==eid)
        if latest['signature']!=stamp or not execution_approved(current,latest):raise ValueError('Version conflict during experiment execution; proof is stale')
        status=result['status']
    except Exception as exc:
        result={'error':str(exc)};status='interrupted' if 'cancel' in str(exc).lower() else 'failed'
    audit={k:v for k,v in result.items() if k not in ('outputs','stdout','stderr','source_assets','runtime')}
    with store.db() as c:c.execute('UPDATE experiment_attempts SET status=?,run_id=?,result=?,updated=? WHERE id=?',(status,result.get('run_id'),store.encode(audit),store.now(),aid))
    if e.get('preparation_task_id'):
        import execution_agent
        task=execution_agent.get(e['preparation_task_id'])
        if task['paper_id']==pid and task['state'].get('experiment_id')==eid:
            execution_agent.save(task['id'],'verified' if status=='passed' else status,run_id=result.get('run_id'),validation_error=result.get('error'))
    remember(pid,event={'kind':'experiment_result','experiment_id':eid,'status':status,'run_id':result.get('run_id'),'error':result.get('error')})
    return {'attempt_id':aid,'status':status,**result}


def curve_compare(actual,checks):
    import numpy as np
    results=[]
    for rule in checks:
        a=np.asarray(actual[rule['field']],dtype=float).reshape(-1)
        if not np.isfinite(a).all() or len(a)<2:raise ValueError('Curve requires finite reference points')
        kind=rule['type']
        if kind=='monotonic':
            start=rule.get('start',0);stop=rule.get('stop',len(a))
            if not 0<=start<stop<=len(a) or stop-start<2:raise ValueError('Curve segment outside evaluated points')
            segment=a[start:stop];ok=bool(np.all(np.diff(segment)>=0) if rule['direction']=='increasing' else np.all(np.diff(segment)<=0))
        elif kind=='extremum':ok=abs(int(np.argmin(a) if rule['direction']=='min' else np.argmax(a))-rule['index'])<=rule.get('index_tolerance',0)
        else:
            x=np.asarray(actual[rule['x_field']],dtype=float).reshape(-1)
            ok=bool(len(x)==len(a) and np.isfinite(x).all() and np.all(np.diff(x)>0) and x.min()<=rule['min'] and x.max()>=rule['max'])
        results.append({'rule':rule,'passed':ok})
    return {'passed':all(r['passed'] for r in results),'checks':results}


def remember(pid,patch=None,event=None):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT memory FROM paper_project_memory WHERE paper_id=?',(pid,)).fetchone()
        memory=store.decode(row[0],{}) if row else {}
        if patch:
            if set(patch)-{'user_goal','decisions','open_questions','next_task','summary'}:raise ValueError('Invalid project memory fields')
            memory.update(patch)
        if event:memory.setdefault('events',[]).append({**event,'created':store.now()})
        c.execute('INSERT OR REPLACE INTO paper_project_memory VALUES(?,?,?)',(pid,store.encode(memory),store.now()))
    return memory


def memory(pid):
    with store.db() as c:
        row=c.execute('SELECT memory FROM paper_project_memory WHERE paper_id=?',(pid,)).fetchone()
        conversations=[dict(r) for r in c.execute('SELECT scope,thread_id,summary FROM sessions WHERE scope_id=?',(pid,))]
        controller=c.execute('SELECT state FROM paper_controllers WHERE paper_id=?',(pid,)).fetchone()
    m=store.decode(row[0],{}) if row else {}
    events=m.pop('events',[]);m['recent_events']=events[-25:];m['total_events']=len(events)
    state=summary(pid)
    controller_state=store.decode(controller[0],{}) if controller else {}
    m.update(conversations=conversations,controller={k:controller_state[k] for k in ('status','stage','next_action','experiment_id','updated') if k in controller_state},
             inventory_revision=state['revision'],inventory_status=state['status'],counts=state['counts'],
             pending=[{'id':e['id'],'name':e['name'],'status':e['status'],'gaps':e['gaps']} for e in state['experiments'] if e['required'] and e['status']!='passed'])
    return m


def separate_legacy_design(pid):
    """Preserve research descriptions separately from scalar calls and field units.

    Only cases never formally executed are migrated. All original descriptive
    values survive verbatim; no code, input, reference or validation field changes.
    """
    inventory=raw(pid)
    if not inventory:return False
    changed=[]
    with store.db() as c:attempted={r[0] for r in c.execute('SELECT DISTINCT experiment_id FROM experiment_attempts WHERE paper_id=?',(pid,))}
    for e in inventory['document']['experiments']:
        if e['id'] in attempted:continue
        t=catalog.get('tool',e['tool_id']) if e.get('tool_id') else None
        declared={v['name'] for v in (t or {}).get('spec',{}).get('parameters',[])}
        descriptive={k:v for k,v in e['parameters'].items() if k not in declared or type(v) not in (str,int,float,bool)}
        units={k:v for k,v in e['units'].items() if k not in e['validation_fields']}
        if not descriptive and not units:continue
        for key,values in (('design',descriptive),('unit_notes',units)):
            notes=e.setdefault(key,{})
            if any(k in notes and notes[k]!=v for k,v in values.items()):raise ValueError('Legacy description conflict; original conditions retained')
            notes.update(values)
        e['parameters']={k:v for k,v in e['parameters'].items() if k not in descriptive}
        e['units']={k:v for k,v in e['units'].items() if k not in units}
        changed.append(e['id'])
    if changed:save(pid,inventory['document'],inventory['revision'],'system','区分研究情景、单位说明与运行接口；原有内容逐字保留，未改变参考或验收字段')
    return bool(changed)
