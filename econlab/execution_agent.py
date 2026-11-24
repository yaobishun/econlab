"""Persistent, paper-scoped Codex execution tasks. Only the host commits library changes."""
from __future__ import annotations
import copy,json,re,shutil,threading
from pathlib import Path
import catalog,materials,store

def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
S={'type':'string'}
def arr(p):return {'type':'array','items':p}
REF=obj({'page':{'type':'integer','minimum':1},'quote':S,'asset_id':S})
DERIVE=obj({'operation':S,'source_asset':S,'name':S,'role':S,'fields':arr(S),'values_json':S,'evidence':arr(REF),'reason':S})
PIN=obj({'method_id':S,'experiment':S,'summary':S,'version_notes':arr(S),'selected_asset_ids':arr(S),
         'derivations':arr(DERIVE),'reference_asset':S,'reference_name':S,'validation_fields':arr(S),'zero_tolerance_json':S})
PUBLISH=obj({'path':S,'role':S,'reason':S,'source_assets':arr(S),'evidence':arr(REF)})
RESULT=obj({'summary':S,'method_id':S,'tool_id':S,'name':S,'language':S,'code_path':S,'spec_json':S,'gaps':arr(S),'memory_summary':S,'blocker_type':{'type':'string','enum':['none','materials','environment','scientific','acceptance_policy']},'diagnostic_asset':S})
TOOLS=[
 {'type':'function','name':'econlab_materials','description':'Search real public sources, fetch and unpack public material URLs, or refresh this paper asset inventory. All downloads and source hashes are audited. action=search/fetch/inventory; unused query/url empty.',
  'inputSchema':obj({'action':S,'query':S,'url':S})},
 {'type':'function','name':'econlab_pin_experiment','description':'Pin this paper method, experiment, independent reference and numeric validation fields BEFORE data adaptation and model implementation. May derive reference/input subsets from actual files or cited paper values. Idempotent for the same plan; cannot change the reference on retry. Select an existing reference_asset OR a derived reference_name. zero_tolerance_json is a JSON object keyed by zero-reference fields; use {} when unnecessary. Returns actual asset IDs.', 'inputSchema':PIN},
 {'type':'function','name':'econlab_publish_artifact','description':'Register an actual generated file from adapters/, derived/, tool/, outputs/ or logs/ in this paper. role=data/code/derivation. Input data must trace actual source_assets; references can only be created when pinning the experiment. Before pinning, files are draft inspection artifacts only, never verification or a completed tool. Returns real asset ID for the final tool spec.', 'inputSchema':PUBLISH},
 {'type':'function','name':'econlab_read_page','description':'Read a source PDF page image and saved visual notes, no OCR. asset_id empty for main paper, or a linked appendix PDF asset ID.',
  'inputSchema':obj({'page':{'type':'integer','minimum':1},'asset_id':S})}]

def init():
    with store.db() as c:c.execute('''CREATE TABLE IF NOT EXISTS execution_tasks(
      id TEXT PRIMARY KEY,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
      status TEXT,state TEXT,created TEXT,updated TEXT)''')

def root_for(pid):
    if not re.fullmatch('[a-f0-9]{16}',pid):raise ValueError('Invalid paper ID')
    base=(store.DATA/'agent-workspace'/'papers').resolve();raw=base/pid
    if raw.is_symlink() or (hasattr(raw,'is_junction') and raw.is_junction()):raise ValueError('Linked paper workspace is not supported')
    root=raw.resolve()
    if root.parent!=base or root.name!=pid:raise ValueError('Paper workspace is outside its assigned root')
    return root

def get(identifier):
    with store.db() as c:r=catalog._row(c,'execution_tasks',identifier)
    r['state']=store.decode(r['state'],{});return r

def latest(pid):
    with store.db() as c:r=c.execute('SELECT id FROM execution_tasks WHERE paper_id=? ORDER BY rowid DESC LIMIT 1',(pid,)).fetchone()
    return get(r[0]) if r else None

def save(identifier,status=None,**patch):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=catalog._row(c,'execution_tasks',identifier);data={**store.decode(row['state'],{}),**patch}
        c.execute('UPDATE execution_tasks SET state=?,status=?,updated=? WHERE id=?',(store.encode(data),status or row['status'],store.now(),identifier))

def stop(pid):
    with store.db() as c:c.execute("UPDATE execution_tasks SET status='interrupted',updated=? WHERE paper_id=? AND status='running'",(store.now(),pid))

def remove_workspace(pid):
    root=root_for(pid);base=(store.DATA/'agent-workspace'/'papers').resolve()
    if root.parent!=base or not root.is_relative_to(base):raise ValueError('Unsafe paper workspace cleanup')
    if root.exists():shutil.rmtree(root)

class Task:
    def __init__(self,jid,pid,options=None):
        self.jid=jid;self.pid=pid;self.options=options or {};self.lock=threading.RLock();self.calls=0
        p=catalog.get('paper',pid)
        self.tool_versions={t['id']:t['revision'] for t in p['tools']}
        self.method_versions={m['id']:m['revision'] for m in p['methods']}
        self.inventory_revision=None;assigned=None
        if self.options.get('experiment_id'):
            import experiments
            plan=experiments.raw(pid)
            if not plan:raise ValueError('Assigned experiment requires a paper inventory')
            self.inventory_revision=plan['revision']
            assigned=next((e for e in plan['document']['experiments'] if e['id']==self.options['experiment_id']),None)
            if not assigned:raise ValueError('Assigned experiment no longer exists')
        self.check()
        old=latest(pid);requested=self.options.get('experiment_request',materials.state(pid)['state'].get('experiment_request',''))
        if self.options.get('experiment_id'):
            with store.db() as c:tasks=c.execute('SELECT * FROM execution_tasks WHERE paper_id=? ORDER BY rowid DESC',(pid,)).fetchall()
            old=next((dict(t)|{'state':store.decode(t['state'])} for t in tasks if store.decode(t['state']).get('experiment_id')==self.options['experiment_id']),None)
        fresh=not old or old['state'].get('request','')!=requested
        if assigned and old and old['state'].get('pin'):
            fixed=old['state']['pin']
            if assigned['method_id']!=fixed['method_id'] or (assigned.get('reference_asset') and (assigned['reference_asset']!=fixed['reference_asset'] or assigned['validation_fields']!=fixed['validation_fields'] or assigned.get('zero_tolerance',{})!=fixed.get('zero_tolerance',{}))):fresh=True
        if fresh:
            self.id=store.uid();now=store.now()
            with store.db() as c:c.execute('INSERT INTO execution_tasks VALUES(?,?,?,?,?,?)',(self.id,pid,'running',store.encode({'request':requested,'pin':None,'experiment_id':self.options.get('experiment_id')}),now,now))
        else:self.id=old['id'];save(self.id,'running')
        if self.options.get('repair') and self.options.get('tool_id'):
            tool=catalog.get('tool',self.options['tool_id'])
            if pid not in {p['id'] for p in tool['sources']}:raise ValueError('Repair tool is outside this paper')
            save(self.id,repair_basis={'tool_id':tool['id'],'method_id':tool['method_id'],'reference_asset':tool['spec'].get('reference_asset'),'validation_fields':tool['spec'].get('validation_fields'),'validation_scope':tool['spec'].get('validation_scope')})
            if assigned:save(self.id,repair_basis={'tool_id':tool['id'],'method_id':assigned['method_id'],'reference_asset':assigned.get('reference_asset'),'validation_fields':assigned['validation_fields'],'validation_scope':assigned['name']})
        self.root=root_for(pid)/self.id
        for folder in ('materials','adapters','derived','tool','outputs','logs'):(self.root/folder).mkdir(parents=True,exist_ok=True)
        self.check();self.stage()

    def check(self):
        import engine
        engine.gate(self.jid,self.pid)

    def progress(self,status,stage,**data):
        import reproduction
        self.check();save(self.id,last_stage=stage)
        return reproduction.checkpoint(self.jid,self.pid,status,stage,agent_task_id=self.id,**data)

    def stage(self):
        """Copy immutable originals; never link to live assets or stage references."""
        self.check();rows=[]
        for a in catalog.get('paper',self.pid)['assets']:
            row={k:a[k] for k in ('id','name','role','sha256','metadata')}
            if a['role']=='reference':row['path']=None;row['note']='Independent reference; never staged for execution'
            else:
                name=Path(a['name'].replace('\\','/')).name
                if not name or ':' in name:raise ValueError('Unsafe asset filename')
                path=self.inside('materials/'+a['id']+'/'+name);path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():shutil.copyfile(catalog.asset_path(a['id']),path)
                if store.digest(path.read_bytes())!=a['sha256']:raise ValueError('Staged original was modified: '+a['name'])
                row['path']=path.relative_to(self.root).as_posix()
            rows.append(row)
        self.inside('asset-index.json').write_text(store.encode(rows),encoding='utf-8')
        return rows

    def inside(self,relative):
        raw=self.root/relative
        for part in (raw,*raw.parents):
            if part==self.root.parent:break
            if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):raise ValueError('Linked workspace paths are not supported')
        path=raw.resolve()
        if not path.is_relative_to(self.root.resolve()):raise ValueError('Path escapes the task workspace')
        return path

    def safe_file(self,relative):
        if not isinstance(relative,str):raise ValueError('Expected a relative artifact path')
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or ':' in relative or '\\' in relative:raise ValueError('Artifact must remain in this task workspace; use forward slashes')
        path=self.inside(relative)
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():raise ValueError('Artifact does not exist inside this task')
        if path.relative_to(self.root).parts[0] not in ('adapters','derived','tool','outputs','logs'):raise ValueError('Submit generated artifacts, not original material copies')
        if path.stat().st_size>store.MAX_FILE:raise ValueError('Artifact exceeds the file limit')
        return path

    def call(self,name,args):
        with self.lock:
            self.check();self.calls+=1
            if self.calls>100:raise ValueError('Host tool budget reached; save progress and continue in another turn')
            if not isinstance(args,dict):raise ValueError('Tool arguments must be an object')
            materials.event(self.pid,'agent_host_call',{'task_id':self.id,'tool':name,'arguments':args})
            if name=='econlab_materials':return self.material(args)
            if name=='econlab_pin_experiment':return self.pin(args)
            if name=='econlab_publish_artifact':return self.publish(args)
            if name=='econlab_read_page':return self.page(args['page'],args.get('asset_id'))
            raise ValueError('Unknown EconLab host tool')

    def material(self,args):
        action=args.get('action')
        if action=='search':
            self.progress('searching','Agent 检索公开复现材料')
            return materials.search(self.pid,args['query'],self.check,self.options.get('refresh_search',False))
        if action=='fetch':
            self.progress('downloading','Agent 获取、解包并检查公开材料')
            value=materials.fetch(self.pid,args['url'],self.check,True)
        elif action=='inventory':
            for a in catalog.get('paper',self.pid)['assets']:
                if Path(a['name']).suffix.lower() in ('.zip','.rar','.gz','.tgz','.tar') and a['metadata'].get('archive_inspection_version',0)<3:
                    materials.unpack(self.pid,a,self.check);materials.annotate(a['id'],archive_inspection_version=3)
            value={'assets':[a if a['role']!='reference' else {k:a[k] for k in ('id','name','role','sha256')} for a in materials.inventory(self.pid)]}
        else:raise ValueError('action must be search, fetch or inventory')
        value['workspace_assets']=self.stage();return value

    def pin(self,args):
        import reproduction
        self.check();previous=get(self.id)['state'].get('pin');plan=copy.deepcopy(args)
        if previous:
            if previous['proposal']!=plan:raise ValueError('Experiment and reference are already pinned; keep the current plan or explicitly start a new experiment')
            return previous
        basis=get(self.id)['state'].get('repair_basis')
        if basis and (plan['method_id']!=basis['method_id'] or plan['reference_asset']!=basis['reference_asset'] or plan['validation_fields']!=basis['validation_fields'] or plan['derivations']):raise ValueError('Repair must keep the original reference and all validation fields')
        method=catalog.get('method',plan['method_id'])
        if self.pid not in {p['id'] for p in method['sources']}:raise ValueError('Method is outside this paper')
        if not plan['experiment'].strip() or not plan['validation_fields'] or any(not isinstance(k,str) or not k for k in plan['validation_fields']):raise ValueError('Specify the experiment and all numeric validation fields')
        if len(set(plan['validation_fields']))!=len(plan['validation_fields']):raise ValueError('Validation fields must be unique')
        import math
        zero=json.loads(plan.get('zero_tolerance_json','{}'))
        if not isinstance(zero,dict) or any(k not in plan['validation_fields'] or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for k,v in zero.items()):raise ValueError('Zero-reference tolerances must be a finite nonnegative object keyed by validation fields')
        if self.options.get('experiment_id'):
            import experiments
            inventory=experiments.raw(self.pid)
            e=next((e for e in inventory['document']['experiments'] if e['id']==self.options['experiment_id']),None)
            if not e or plan['method_id']!=e['method_id']:raise ValueError('Pinned model must match the inventory experiment')
            if e.get('reference_asset') and plan['reference_asset']!=e['reference_asset']:raise ValueError('Keep the inventoried independent reference; changing it requires explicit plan revision')
            if e.get('validation_fields') and plan['validation_fields']!=e['validation_fields']:raise ValueError('Do not omit or change inventoried validation fields')
            fixed_zero=e.get('zero_tolerance',{})
            if any(k not in zero or zero[k]!=v for k,v in fixed_zero.items()):raise ValueError('Do not loosen inventoried zero tolerances')
            if zero!=fixed_zero:
                with store.db() as c:attempted=c.execute('SELECT 1 FROM experiment_attempts WHERE paper_id=? AND experiment_id=?',(self.pid,e['id'])).fetchone()
                if attempted:raise ValueError('Do not introduce new zero tolerances after observing formal results')
        for aid in plan['selected_asset_ids']:
            if catalog.asset(aid)['paper_id']!=self.pid:raise ValueError('Selected material is outside this paper')
        if len(plan['derivations'])>8:raise ValueError('At most eight explicit benchmark derivations')
        derived=[]
        for d in plan['derivations']:derived.append(reproduction.derive(self.pid,d))
        reference=plan['reference_asset'] or next((a['id'] for a in derived if a['name']==plan['reference_name'] and a['role']=='reference'),'')
        ref=catalog.asset(reference)
        if ref['paper_id']!=self.pid or ref['role']!='reference':raise ValueError('Pin an actual independent reference asset of this paper')
        path=catalog.asset_path(reference)
        if path.suffix.lower()=='.json':values=json.loads(path.read_text(encoding='utf-8-sig'))
        elif path.suffix.lower()=='.mat':
            from scipy.io import loadmat
            values=loadmat(path,mat_dtype=True)
        else:raise ValueError('先把独立参考整理为有来源的JSON或MAT文件，再固定实验')
        if not all(k in values for k in plan['validation_fields']):raise ValueError('Validation field missing from reference')
        import runner
        check=runner.compare(values,values,plan['validation_fields'],zero)
        if not check['passed']:raise ValueError('参考字段必须为非空有限数值；若含零参考，请先依据单位和数值精度声明zero_tolerance_json，不能在看到计算结果后放宽容差')
        pin={'proposal':plan,'method_id':method['id'],'method_revision':method['revision'],'tool_revisions':{t['id']:t['revision'] for t in method['tools']},'reference_asset':reference,'reference_sha256':ref['sha256'],'validation_fields':plan['validation_fields'],'zero_tolerance':zero,'derived_assets':[a['id'] for a in derived],'pinned_at':store.now()}
        save(self.id,pin=pin);materials.event(self.pid,'experiment_pinned',{'task_id':self.id,**pin})
        self.progress('adapting','已固定验收范围，Agent 检查数据并编写适配程序',method_id=method['id'],assessment={'method_id':method['id'],'experiment':plan['experiment'],'summary':plan['summary'],'version_notes':plan['version_notes']},method_plan={'experiment':plan['experiment'],'summary':plan['summary'],'version_notes':plan['version_notes']})
        self.stage();return pin

    def publish(self,args):
        pin=get(self.id)['state'].get('pin')
        path=self.safe_file(args['path']);role=args['role']
        if role not in ('data','code','derivation'):raise ValueError('Only generated data, code or derivation reports can be published')
        sources=[catalog.asset(i) for i in args['source_assets']]
        if not sources or any(a['paper_id']!=self.pid or a['role']=='reference' for a in sources):raise ValueError('Artifacts must trace non-reference sources from this paper')
        refs=catalog.check_evidence(self.pid,args['evidence'],True)
        self.stage();a=catalog.add_asset(self.pid,path.name,path.read_bytes(),role)
        materials.annotate(a['id'],provenance='agent_execution_artifact' if pin else 'agent_inspection_artifact',pre_pin=not bool(pin),task_id=self.id,workspace_path=args['path'],source_assets=[s['id'] for s in sources],evidence=refs,reason=args['reason'],review_required=True)
        artifacts=get(self.id)['state'].get('artifacts',[])
        record={'asset_id':a['id'],'path':args['path'],'sha256':a['sha256'],'role':role,'sources':args['source_assets']}
        if record not in artifacts:artifacts.append(record)
        save(self.id,artifacts=artifacts);materials.event(self.pid,'agent_artifact',{'task_id':self.id,**record})
        return {k:a[k] for k in ('id','name','sha256','role')}

    def page(self,number,asset_id=None):
        import base64,fitz
        p=catalog.get('paper',self.pid)
        aid=asset_id or p['original_asset'];a=catalog.asset(aid)
        if a['paper_id']!=self.pid or a['role'] not in ('original','appendix'):raise ValueError('PDF source is outside this paper')
        with fitz.open(catalog.asset_path(aid)) as pdf:
            if type(number) is not int or not 1<=number<=len(pdf):raise ValueError('Invalid source PDF page')
            raw=pdf[number-1].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).tobytes('jpg')
            text=pdf[number-1].get_text()
        if aid==p['original_asset']:note=catalog.page(self.pid,number)
        else:
            with store.db() as c:row=c.execute('SELECT text,visual_notes FROM supplementary_pages WHERE asset_id=? AND page=?',(aid,number)).fetchone()
            note=dict(row) if row else {'text':text}
        return {'contentItems':[{'type':'inputText','text':store.encode({'asset_id':aid,'page':number,'text':note.get('text',''),'visual_notes':note.get('visual_notes','')})},{'type':'inputImage','imageUrl':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}],'success':True}

    def on_event(self,event):
        self.check();name=event.get('method','');p=event.get('params',{});item=p.get('item',{})
        if name not in ('item/started','item/completed'):return
        kind=item.get('type','')
        if kind not in ('commandExecution','fileChange','dynamicToolCall','webSearch'):return
        clean={k:item[k] for k in ('id','type','command','cwd','status','exitCode','aggregatedOutput','changes','tool','action') if k in item}
        if isinstance(clean.get('aggregatedOutput'),str):clean['aggregatedOutput']=clean['aggregatedOutput'][-16000:]
        if name=='item/completed':
            materials.event(self.pid,'agent_execution',{'task_id':self.id,**clean})
            with self.inside('logs/execution.jsonl').open('a',encoding='utf-8') as f:f.write(store.encode({'at':store.now(),**clean})+'\n')
            count=get(self.id)['state'].get('execution_count',0)+(kind=='commandExecution')
            save(self.id,execution_count=count,last_execution=clean)
        if kind=='commandExecution':
            bad=item.get('exitCode') not in (None,0)
            self.progress('repairing' if bad else 'executing','检查命令出错，Agent 根据日志继续修正' if bad else 'Agent 执行文件检查与数据适配',last_execution=clean)

    def configuration(self):return {'cwd':self.root,'tools':TOOLS,'handler':self.call,'on_event':self.on_event}

    def run_feedback(self,tools):
        """Expose real formal-run feedback to repair tasks, without staging references."""
        rows=[]
        for tool in tools:
            for run in tool.get('runs',[])[:3]:
                if run.get('paper_id')!=self.pid or not re.fullmatch('[a-f0-9]{16}',run['id']):continue
                row={k:run[k] for k in ('id','tool_id','tool_revision','status','kind','request','fingerprint','scope')}
                result=run.get('result',{})
                row['result']={k:result[k] for k in ('error','exit_code','comparison','validation_policy','started','finished') if k in result}
                for key in ('stdout','stderr'):row['result'][key]=result.get(key,'')[-12000:]
                row['files']=[];run_root=store.DATA/'runs'/run['id']
                for name in ('stdout.log','stderr.log','result.json','result.mat'):
                    source=run_root/name
                    if not source.is_file() or not source.resolve().is_relative_to(run_root.resolve()) or source.stat().st_size>store.MAX_FILE:continue
                    target=self.inside('logs/formal-runs/'+run['id']+'/'+name);target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,target)
                    row['files'].append({'path':target.relative_to(self.root).as_posix(),'sha256':store.digest(target.read_bytes())})
                rows.append(row)
        return rows

    def prompt(self):
        import engine
        p=catalog.get('paper',self.pid);state=get(self.id)['state'];chat=catalog.session('paper',self.pid)
        methods=[catalog.get('method',m['id']) for m in p['methods']]
        for m in methods:
            m.pop('history',None);m.pop('reviews',None)
        full_tools=[catalog.get('tool',t['id']) for t in p['tools']]
        feedback=self.run_feedback(full_tools)
        tools=[{k:t[k] for k in ('id','method_id','name','spec','code','revision','validation_status','review_status')} for t in full_tools]
        notes=[]
        with store.db() as c:
            for r in c.execute('SELECT page,visual_notes FROM pages WHERE paper_id=? AND visual_notes<>\'\' ORDER BY page',(self.pid,)):
                if not notes or notes[-1]['notes']!=r[1]:notes.append({'starting_page':r[0],'notes':r[1]})
        mission={'paper':{k:p[k] for k in ('id','title','authors','year','doi','card','coverage')},'methods':methods,'tools':tools,'formal_run_feedback':feedback,'paper_memory':chat['summary'],'recent_user_discussion':engine.history_window([m for m in catalog.messages(chat['id']) if m['kind'] in ('chat','display')],12000),'request':state.get('request'),'prior_execution_state':state,'urls':self.options.get('urls',[]),'refresh_search':bool(self.options.get('refresh_search')),'original_page_notes':notes,'workspace_assets':self.stage()}
        import experiments
        mission['project_memory']=experiments.memory(self.pid)
        mission['automatic_check_feedback']=self.options.get('automatic_check_feedback')
        import environments
        mission['available_runtimes']=environments.available()
        mission['reproduction_mode']=store.get_setting('reproduction_mode','automatic')
        mission['experiment_inventory']=experiments.raw(self.pid)
        mission['assigned_experiment']=next((e for e in (mission['experiment_inventory'] or {}).get('document',{}).get('experiments',[]) if e['id']==self.options.get('experiment_id')),None)
        self.inside('mission.json').write_text(store.encode(mission),encoding='utf-8')
        return '本次任务的绝对工作目录：'+str(self.root)+'。这是当前指派；上一实验工作区的 mission.json 和 pin 不属于本次任务。\n'+'''完成当前论文的可复用复现工具准备任务。详细任务、论文知识、逐页笔记、当前版本、真实资产路径在当前目录 mission.json 和 asset-index.json。请实际读取，不凭空假设字段和国家标签。
1. 先检查现有文件/上轮日志。formal_run_feedback 是宿主的正式运行反馈，附版本、逐项比较、标准输出与错误；logs/formal-runs/ 下是对应实际输出副本，未包含参考文件。修正任务应先据此定位错误，不能忽略日志重写算法。原材料路径为 materials/<asset_id>/<name>，archive_path保留包内原路径。用命令编写运行通用检查脚本，检查CSV分隔符、Excel工作表及表头、MAT字段/维度、跨文件顺序、映射矩阵和标签；可使用本机应用Python，不必等待用户整理标签。避免一次打印超大数据。
2. 材料不足使用 econlab_materials 查询、下载、解包。也可以用原生web search查公开作者主页和期刊链接，然后经host fetch取得真实字节。搜索失败如实记录，可尝试更精确查询；不能把猜测的链接当来源。必要时用 econlab_read_page 看原始表格/公式。无原作者程序可按已确认方程实现，但真实输入和独立参考不可捏造。
3. 确定一个已有method_id以及最小清晰实验。调用 econlab_pin_experiment 固定全部参考和验收字段：已有reference_asset可直接指定；否则用derivations的paper_values/reference从实际论文表格构建（values_json，独立标签和数值字段，绝对页码、连续短引文）；或mat_fields从作者文件拆分reference。reference_name选派生文件名。参数/输入只能来自真实材料。已有pin不可更换参考或删指标。
4. 写 adapters/ 下数据适配程序并实际运行，输出 derived/ 下真实输入文件与 outputs/ 下映射/单位/守恒/维度检查报告，错误要自主修正。使用 econlab_publish_artifact 把实际文件登记为data/code/derivation，取得资产ID用于工具spec；源资产要逐一说明。工程预检可以运行；正式模型求解和数值验收由审核后的host runner运行，不能自称验证通过。不要安装任意仓库脚本或自行提权；依赖记录到spec待审核，数据检查优先标准库或应用已安装包。
5. 写 tool/ 下最终封装。Python: run(inputs,workspace)->dict；MATLAB: entrypoint(input_file,request_json,output_mat)，request_mode=json。代码不能读取reference或根据validation标志切换算法。spec={parameters:列表,asset_ids:列表,code_assets:列表,bundle_paths:{资产ID:相对路径或路径列表},input_asset:真实输入ID,reference_asset:固定参考ID,validation_fields:固定数值字段列表,zero_tolerance:{},validation_scope:实验,entrypoint:函数名,environment:{requirements:包名版本列表,toolboxes:MATLAB产品名列表,matlab_functions:函数名列表}}。标量业务参数仅number/integer/boolean/string，数组放输入文件；不要设source_kind=author_adapter。不把reference加入bundle_paths。所有被调用作者函数必须绑定实际code_assets及路径。输出保留未舍入精度；收敛/约束检查失败应报错。缺材料可保留草稿但明确gaps，未审核/未正式运行不算材料缺口。
若真实数值验收失败，先读formal_run_feedback中的实际输出与误差，按作者原始实现和同一输入定位原因。available_runtimes列出本机已有程序，空白工具的默认Python语言不是限制；作者代码可运行时应优先实际执行。必要时在本任务工作区运行作者原代码作独立诊断，保留真实命令、输出、源码和输入哈希；外部程序的临时/偏好/缓存目录放任务工作区。不能通过改模型、输入、参考、字段、舍入输出或放宽容差来满足阈值。
如果程序与独立作者实现一致，而失败来自已发表数值舍入或验收规则冲突，保持正式失败，不重复调参。将独立核对、原文显示精度及真实运行证据发布为derivation报告，返回blocker_type=acceptance_policy、diagnostic_asset=该报告真实ID；这只暂停当前项，不授予任何通过状态。其他正常准备返回blocker_type=none、diagnostic_asset=""。
6. 最后返回结构化结果，code_path指向实际tool/文件；method_id和tool_id用任务已有ID，避免改写已验证工具。spec_json使用真实登记的资产ID，不使用文件路径代替ID。memory_summary记住实际材料、标签来源、修正及未完成事项。宿主读取文件并校验来源、版本和固定参考后才写入三库；你不能改数据库/审核/验证状态。
只遇到真正材料缺失或改变模型假设时暂停，常规工程操作应自己完成。用户实验目标：'''+state.get('request','')+'\n本机应用Python：'+__import__('sys').executable+'''
运行策略以 mission.reproduction_mode 为准：可在固定验收前发布检查脚本和材料缺口报告，它们仅是未验证草稿；独立参考仍只能通过pin创建。automatic 时由宿主自动检查、自动配置环境并在沙箱正式执行，不以缺人工签名或尚未手动配置环境为材料缺口；你的工作是准备真实代码和输入，不能伪造数值证明。automatic_check_feedback 是上一轮实际检查或安装错误，需读取并修正。整篇论文模式：若 mission.assigned_experiment 非空，本轮必须只推进该实验，method_id/参考/验收字段按完整清单固定，不能换成更简单实验。第3步的“最小实验”仅适用于旧的未分配任务。复用相同核心模型的已有公共求解器（tool_id），把实验输入、参数和冲击放在配置里，不能为不同图表复制求解器。spec_json 增加 experiment_configuration 对象，填写当前实验的 units（每个验收字段单位）、curve_checks、dependency_bindings、parameters（固定实验参数）及 gaps；曲线必须有monotonic/extremum/coverage三类预定规则，不适合这些规则的复杂曲线应记录缺口，不能冒充通过。依赖校准输出用dependency_bindings=[{experiment_id:前置ID,path:inputs/calibration.json}]；宿主只在前置实际通过后写入其真实输出。units必须以当前validation_fields逐个作为键。unit_notes保存原分组单位说明，只作经济含义依据，不是可抄用的输出键。mission.assigned_experiment.design保存固定研究情景，必须在实际输入文件中忠实实现，不能当作运行参数直接传递。experiment_configuration.parameters只填spec.parameters声明的标量；若工具完全读取输入文件且spec.parameters为空，此处必须为{}，不要把成员数组、年份数组、文字冲击说明当运行参数。若作者材料已提供某前置任务的校准数据快照，可以不重新计算该前置任务就运行当前实验：在experiment_configuration.dependency_materials中逐项填写[{experiment_id:前置ID,asset_ids:[本实验实际装载的data资产ID],reason:说明来源、字段和为什么满足该输入依赖}]。此项只替代当前运行的输入依赖；前置任务仍保留在整篇必验清单中，不能删除或冒充通过。未由真实已装载数据覆盖的依赖仍必须先运行通过。已有验收字段/参考不得更换；补齐空字段需要新计划版本与人工审核。若当前正式工具代码已经满足实验，保持code和依赖不变，仅提交不同实验配置。更改公共代码将使其关联实验的旧证明失效。'''

def prepare(jid,pid,options=None):
    import engine,reproduction
    task=Task(jid,pid,options);task.progress('inspecting','Codex 执行 Agent 开始检查本论文工作区',gaps=[])
    try:
        # A single native turn contains repeated actual tool calls and repairs.
        result=engine.ask(jid,pid,task.prompt(),RESULT,kind='execution_agent',task=task.configuration())
        for attempt in range(3):
            try:return accept(task,result)
            except (ValueError,KeyError,TypeError) as exc:
                task.check()
                if 'conflict' in str(exc).lower() or attempt==2:raise
                materials.event(pid,'agent_submission_rejected',{'task_id':task.id,'attempt':attempt+1,'error':str(exc)})
                task.progress('repairing','宿主检查发现成果接口问题，Agent 正在修正')
                result=engine.ask(jid,pid,'宿主拒绝了本次成果提交：'+str(exc)+'。请读取已有文件和固定 pin，修正配置或实现后重新提交完整 RESULT；不能修改固定参考、删减指标或改变经济假设，不重复下载已有材料。上次提交：'+store.encode(result),RESULT,kind='execution_agent',task=task.configuration())
    except Exception as exc:
        try:
            task.check()
            waiting=bool(re.search(r'usage.?limit|rate.?limit|quota|额度',str(exc),re.I))
            status='waiting_account' if waiting else 'blocked'
            save(task.id,status,error=str(exc));task.progress(status,'Codex 账户额度暂不可用，记录已保留，恢复后可继续' if waiting else '执行任务未完成，已保留工作区，可继续',gaps=[str(exc)])
        except ValueError:pass
        raise

def record_validation(pid,tool,result):
    identifier=tool['spec'].get('execution_task_id')
    if not identifier:return
    try:task=get(identifier)
    except ValueError:return
    if task['paper_id']!=pid or task['state'].get('tool_id')!=tool['id']:return
    save(identifier,'verified' if result['status']=='passed' else 'failed',run_id=result['run_id'],validation_error=result.get('error'))


def accept(task,result):
    import reproduction
    jid,pid=task.jid,task.pid
    task.check();task.stage();state=get(task.id)['state'];pin=state.get('pin')
    if not pin:
        if not result['gaps']:raise ValueError('Agent did not pin a reproducible experiment')
        save(task.id,'blocked',summary=result['summary'],gaps=result['gaps'],memory_summary=result['memory_summary'])
        if task.options.get('experiment_id'):
            import experiments
            inventory=experiments.raw(pid)
            if inventory['revision']!=task.inventory_revision:raise ValueError('Version conflict: inventory changed during source inspection')
            e=next(v for v in inventory['document']['experiments'] if v['id']==task.options['experiment_id'])
            e['gaps']=result['gaps']
            experiments.save(pid,inventory['document'],inventory['revision'],'Codex','记录实际文件检查后的材料缺口，不生成通过证明')
            experiments.remember(pid,{'summary':result['memory_summary']},event={'kind':'experiment_blocked','experiment_id':e['id'],'task_id':task.id,'gaps':result['gaps']})
        session=catalog.session('paper_agent',pid);catalog.save_session(session['id'],session['thread_id'],result['memory_summary'])
        return task.progress('blocked','公开材料或目标不足，执行记录已保留',gaps=result['gaps'])
    if result.get('blocker_type')=='acceptance_policy':
        import experiments
        plan=experiments.raw(pid);eid=task.options.get('experiment_id')
        e=next((v for v in (plan or {}).get('document',{}).get('experiments',[]) if v['id']==eid),None)
        with store.db() as c:failed=c.execute("SELECT 1 FROM experiment_attempts WHERE paper_id=? AND experiment_id=? AND status='failed'",(pid,eid)).fetchone()
        if not e or not failed or e.get('reference_asset')!=pin['reference_asset'] or e['validation_fields']!=pin['validation_fields'] or result['method_id']!=pin['method_id']:raise ValueError('A policy conflict requires an actual failed experiment and unchanged frozen benchmark')
        aid=result.get('diagnostic_asset','')
        if not any(a['asset_id']==aid and a['role']=='derivation' for a in state.get('artifacts',[])):raise ValueError('Policy conflict needs a real published diagnostic report')
        report=catalog.asset(aid);catalog.asset_path(aid)
        if report['paper_id']!=pid:raise ValueError('Diagnostic report belongs to another paper')
        save(task.id,'blocked',blocker_type='acceptance_policy',diagnostic_asset=aid,summary=result['summary'],gaps=result['gaps'],memory_summary=result['memory_summary'])
        session=catalog.session('paper_agent',pid);catalog.save_session(session['id'],session['thread_id'],result['memory_summary'])
        experiments.remember(pid,{'summary':result['memory_summary']},event={'kind':'acceptance_policy_conflict','experiment_id':eid,'diagnostic_asset':aid,'summary':result['summary']})
        return task.progress('blocked','当前验收口径需要确认，原失败结果与冻结参考均保留',blocker_type='acceptance_policy',diagnostic_asset=aid,gaps=result['gaps'])
    spec=json.loads(result['spec_json'])
    experiment_config=spec.pop('experiment_configuration',{})
    if not isinstance(experiment_config,dict):raise ValueError('Experiment configuration must be an object')
    if result['method_id']!=pin['method_id']:raise ValueError('Agent changed the pinned method')
    if spec.get('reference_asset')!=pin['reference_asset'] or spec.get('validation_fields')!=pin['validation_fields']:raise ValueError('Agent changed reference or omitted pinned validation fields')
    if spec.get('zero_tolerance',{})!=pin.get('zero_tolerance',{}):raise ValueError('Zero-reference tolerance changed after the experiment was pinned')
    if catalog.asset_path(pin['reference_asset']).is_file() and catalog.asset(pin['reference_asset'])['sha256']!=pin['reference_sha256']:raise ValueError('Pinned reference changed')
    if pin['reference_asset'] in spec.get('bundle_paths',{}):raise ValueError('Reference must not be exposed to the calculation')
    path=task.safe_file(result['code_path']) if result['code_path'] else None
    built={k:result[k] for k in ('summary','method_id','tool_id','name','language','spec_json','gaps')};built['code']=path.read_text(encoding='utf-8-sig') if path else ''
    spec['execution_task_id']=task.id;spec['execution_artifacts']=state.get('artifacts',[])
    spec['pinned_experiment']={'task_id':task.id,'reference_sha256':pin['reference_sha256'],'validation_fields':pin['validation_fields'],'experiment':pin['proposal']['experiment']}
    built['spec_json']=store.encode(spec)
    method=catalog.get('method',pin['method_id']);tools=[catalog.get('tool',t['id']) for t in method['tools']]
    if method['revision']!=task.method_versions[method['id']]:raise ValueError('Version conflict: core model changed during Agent preparation')
    target=next((t for t in tools if t['id']==result['tool_id']),None)
    allowed_revision=task.tool_versions.get((target or {}).get('id'))
    if target and target['revision']!=allowed_revision:raise ValueError('Version conflict: tool changed during Agent preparation')
    if task.options.get('experiment_id'):
        import experiments,runner
        inventory=experiments.raw(pid)
        if inventory['revision']!=task.inventory_revision:raise ValueError('Version conflict: inventory changed during Agent preparation')
        e=next(e for e in inventory['document']['experiments'] if e['id']==task.options['experiment_id'])
        if e['method_id']!=pin['method_id']:raise ValueError('Assigned experiment model changed')
        allowed={'units','curve_checks','dependency_bindings','dependency_materials','parameters','gaps'}
        if set(experiment_config)-allowed:raise ValueError('Agent cannot change experiment identity, dependencies or acceptance scope')
        for key in ('units','curve_checks','dependency_bindings','dependency_materials','parameters'):
            if e.get(key) and key in experiment_config and experiment_config[key]!=e[key]:raise ValueError('Do not change fixed experiment '+key+' during implementation/repair; propose an explicit reviewed inventory revision')
        if e.get('reference_asset') and e['reference_asset']!=pin['reference_asset']:raise ValueError('Assigned experiment reference changed')
        if e.get('validation_fields') and e['validation_fields']!=pin['validation_fields']:raise ValueError('Assigned experiment validation fields changed')
        # Preserve a common solver when only the experiment's data/configuration changes.
        def solver_contract(s):
            code=[aid for aid in s.get('code_assets',[]) if catalog.asset(aid)['metadata'].get('provenance')!='generated_implementation']
            return {'environment':s.get('environment',{}),'entrypoint':s.get('entrypoint'),'request_mode':s.get('request_mode','json' if built['language']=='matlab' else None),
                    'parameters':[{k:v for k,v in p.items() if k!='default'} for p in s.get('parameters',[])],'code_assets':sorted(code)}
        reuse=next((t for t in tools if t['code']==built['code'] and t['language']==built['language'] and solver_contract(t['spec'])==solver_contract(spec)),None)
        candidate=copy.deepcopy(inventory['document']);candidate_e=next(v for v in candidate['experiments'] if v['id']==e['id'])
        candidate_e.update({k:spec[k] for k in ('input_asset','reference_asset','asset_ids','bundle_paths','validation_fields','zero_tolerance') if k in spec})
        candidate_e.update(experiment_config);candidate_e['gaps']=result['gaps']
        try:runner.parameters(spec,candidate_e['parameters'])
        except ValueError as exc:raise ValueError('Experiment parameters must match the declared scalar tool ABI; preserve scientific conditions in design and actual input files, not undeclared call parameters: '+str(exc)) from exc
        experiments.normalize(pid,candidate)
        if reuse:
            prepared=materials.progress(pid,'awaiting_review','复用公共求解器，实验配置等待审核',tool_id=reuse['id'])
        else:
            spec['paper_controller']=True;built['spec_json']=store.encode(spec)
            prepared=reproduction.save_prepared(jid,pid,built,method['id'],tools,task.method_versions[method['id']])
        prepared_tool=prepared['state']['tool_id']
        e.update({k:spec[k] for k in ('input_asset','reference_asset','asset_ids','bundle_paths','validation_fields','zero_tolerance') if k in spec})
        e.update(experiment_config);e.update(tool_id=prepared_tool,gaps=result['gaps'],preparation_task_id=task.id)
        if not e['parameters']:e['parameters']=runner.parameters(spec,{})
        experiments.save(pid,inventory['document'],inventory['revision'],'Codex','准备指定实验的输入、公共求解器映射与验收配置')
        experiments.remember(pid,{'summary':result['memory_summary']},event={'kind':'agent_submission','experiment_id':e['id'],'tool_id':prepared_tool,'task_id':task.id})
    else:prepared=reproduction.save_prepared(jid,pid,built,method['id'],tools,task.method_versions[method['id']])
    prepared_id=prepared['state'].get('tool_id')
    if task.options.get('experiment_id') and __import__('automation').enabled() and prepared['status']=='awaiting_review':
        prepared=materials.progress(pid,'prepared','实验配置已准备，继续自动检查、配置环境和数值验收',tool_id=prepared_id)
    task.tool_versions[prepared_id]=catalog.get('tool',prepared_id)['revision']
    save(task.id,prepared['status'],summary=result['summary'],tool_id=prepared_id,prepared_tool_revision=catalog.get('tool',prepared_id)['revision'],memory_summary=result['memory_summary'])
    session=catalog.session('paper_agent',pid);catalog.save_session(session['id'],session['thread_id'],result['memory_summary'])
    materials.event(pid,'agent_submission',{'task_id':task.id,'tool_id':prepared['state'].get('tool_id'),'execution_count':state.get('execution_count',0),'artifacts':state.get('artifacts',[])})
    return prepared
