"""Versioned, reviewed local tools and strict reproduction validation.
Tools execute with the current OS user's permissions; review trusted code before running.
"""
from __future__ import annotations
import json, math, os, re, shutil, subprocess, sys, threading
from pathlib import Path
import numpy as np
from scipy.io import loadmat
import catalog, store
ACTIVE={}; LOCK=threading.Lock()
VALIDATION_POLICY={'relative_error_limit':0.1,'strict':True,'round_outputs':False,'zero_reference':'explicit_absolute_tolerance'}

def jsonable(value):
    """Preserve MATLAB cells/structs and NumPy scalars in execution reports."""
    if isinstance(value,np.ndarray):
        if value.dtype.names:
            rows=[{k:jsonable(item[k]) for k in value.dtype.names} for item in value.flat]
            return np.asarray(rows,dtype=object).reshape(value.shape).tolist()
        return jsonable(value.tolist())
    if isinstance(value,np.void) and value.dtype.names:return {k:jsonable(value[k]) for k in value.dtype.names}
    if isinstance(value,np.generic):return jsonable(value.item())
    if hasattr(value,'_fieldnames'):return {k:jsonable(getattr(value,k)) for k in value._fieldnames}
    if isinstance(value,dict):return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [jsonable(v) for v in value]
    if isinstance(value,bytes):return value.decode('utf-8',errors='replace')
    if isinstance(value,complex) or isinstance(value,float) and not math.isfinite(value):raise ValueError('Output contains complex or non-finite values')
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('Unsupported output value type: '+type(value).__name__)

def parameters(spec,values):
    if not isinstance(values,dict):raise ValueError('Parameters must be an object')
    fields=spec.get('parameters',[]);known={f['name'] for f in fields}
    if set(values)-known:raise ValueError('Unknown parameters: '+', '.join(set(values)-known))
    result={}
    for f in fields:
        key=f['name'];v=values.get(key,f.get('default'))
        if v is None:
            if f.get('required'):raise ValueError('Missing parameter: '+key)
            continue
        typ=f.get('type','number')
        if typ in ('number','integer'):
            if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v):raise ValueError('Invalid numeric input: '+key)
            if typ=='integer' and int(v)!=v:raise ValueError('Integer required: '+key)
            if 'min' in f and v<f['min'] or 'max' in f and v>f['max']:raise ValueError('Parameter outside allowed range: '+key)
        elif typ=='boolean':
            if not isinstance(v,bool):raise ValueError('Boolean required: '+key)
        elif typ=='string':
            if not isinstance(v,str) or len(v)>2000:raise ValueError('Invalid string: '+key)
        else:raise ValueError('Unsupported parameter type')
        result[key]=v
    return result

def compare(actual,reference,fields,zero_tolerance=None):
    if not fields:raise ValueError('Specify reference output fields before validation')
    if isinstance(zero_tolerance,(int,float)) and not isinstance(zero_tolerance,bool):
        if not math.isfinite(zero_tolerance) or zero_tolerance<0:raise ValueError('Zero-reference tolerance must be finite and nonnegative')
        zero_tolerance={key:zero_tolerance for key in fields}
    elif zero_tolerance is not None and not isinstance(zero_tolerance,dict):raise ValueError('Invalid zero-reference tolerance')
    report=[]
    for key in fields:
        if key not in actual or key not in reference:
            report.append({'field':key,'passed':False,'error':'Missing actual or reference output'});continue
        try:
            if np.iscomplexobj(actual[key]) or np.iscomplexobj(reference[key]):raise ValueError('Complex output')
            a=np.asarray(actual[key],dtype=float);b=np.asarray(reference[key],dtype=float)
        except (TypeError,ValueError):
            report.append({'field':key,'passed':False,'error':'Expected real numeric output and reference'});continue
        # MATLAB serializes a scalar as 1x1, whereas JSON represents it as
        # a number. Accept only this scalar ABI difference, never reshape arrays.
        if a.size==b.size==1 and (a.ndim==0 or b.ndim==0):a=a.reshape(());b=b.reshape(())
        if a.shape!=b.shape or not a.size or not np.isfinite(a).all() or not np.isfinite(b).all():
            report.append({'field':key,'passed':False,'error':'Shape mismatch, empty or non-finite output'});continue
        zero=b==0;nonzero=~zero;relative=np.abs((a[nonzero]-b[nonzero])/b[nonzero]);tol=(zero_tolerance or {}).get(key)
        zeros_ok=not zero.any() or tol is not None and np.max(np.abs(a[zero]-b[zero]))<=float(tol)
        maximum=float(relative.max()) if relative.size else None
        report.append({'field':key,'count':a.size,'max_relative_error':maximum,'mean_relative_error':float(relative.mean()) if relative.size else None,
                       'zero_reference_count':int(zero.sum()),'zero_absolute_tolerance':tol,'passed':bool(zeros_ok and (maximum is None or maximum<.10))})
    return {'passed':all(r['passed'] for r in report),'threshold':'every nonzero element: relative error strictly < 10%','metrics':report}

def _matlab():
    path=os.environ.get('ECONLAB_MATLAB') or shutil.which('matlab')
    if not path and os.name=='nt':
        choices=sorted(Path('C:/Program Files/MATLAB').glob('R*/bin/matlab.exe'),reverse=True)
        if choices:path=str(choices[0])
    if not path or not Path(path).is_file():raise ValueError('MATLAB is required for this tool; set ECONLAB_MATLAB to matlab.exe')
    return path

def cancelled(paper_id):
    with LOCK:
        for rid,(pid,proc) in list(ACTIVE.items()):
            if pid==paper_id:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                else:proc.kill()

def execute(tool_id,values,validation=False,paper_id=None):
    tool=catalog.get('tool',tool_id);spec=tool['spec']
    if not tool['code']:raise ValueError('This method has no executable code yet')
    if tool['review_status']!='confirmed':raise ValueError('Review the tool code and configuration before execution')
    if not validation and catalog.get('method',tool['method_id'])['review_status']=='disputed':raise ValueError('Resolve the disputed method review before using it for a new problem')
    fingerprint=catalog.fingerprint(tool)
    if not validation:
        with store.db() as c:proof=c.execute("SELECT id FROM runs WHERE tool_id=? AND kind='validation' AND status='passed' AND fingerprint=?",(tool_id,fingerprint)).fetchone()
        if tool['validation_status']!='verified' or not proof:raise ValueError('Current tool version has not passed reproduction validation')
    elif not spec.get('input_asset') or not spec.get('reference_asset'):raise ValueError('Real input data and independent reference results are required')
    if validation:
        code_sources=spec.get('code_assets',[]) or spec.get('author_code_assets',[])
        if code_sources and any(catalog.asset(a)['role']!='code' for a in code_sources):raise ValueError('Implementation code assets must have the code role')
        if not code_sources:
            refs=spec.get('implementation_evidence',[])
            allowed={p['id'] for p in tool['sources']}
            if not refs:raise ValueError('Attach implementation code or record the paper evidence used to implement it')
            for ref in refs:
                if ref.get('paper_id') not in allowed:raise ValueError('Implementation evidence must come from a linked paper')
                catalog.check_evidence(ref['paper_id'],[ref],True)
        if catalog.asset(spec['input_asset'])['role']!='data' or catalog.asset(spec['reference_asset'])['role']!='reference':raise ValueError('Validation requires separately identified input data and reference output assets')
    inputs=parameters(spec,values);rid=store.uid();root=store.DATA/'runs'/rid;root.mkdir(parents=True)
    paper_id=paper_id or tool['sources'][0]['id'];now=store.now()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM runs WHERE tool_id=? AND status='running'",(tool_id,)).fetchone():raise ValueError('This tool already has a running calculation; wait for completion')
        c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(rid,tool_id,tool['revision'],paper_id,'validation' if validation else 'calculation','running',store.encode(inputs),'{}',fingerprint,spec.get('validation_scope',''),now,now))
    method=catalog.get('method',tool['method_id'])
    result={'run_id':rid,'tool_revision':tool['revision'],'method_id':method['id'],'method_revision':method['revision'],'code_sha256':store.digest(tool['code'].encode('utf-8')),'fingerprint':fingerprint,'scope':spec.get('validation_scope',''),'inputs':inputs,'started':now,'validation_policy':VALIDATION_POLICY}
    proc=None
    try:
        authorized={a['id'] for p in tool['sources'] for a in catalog.get('paper',p['id'])['assets']}
        ids=set(spec.get('asset_ids',[]))|set(spec.get('author_code_assets',[]))|set(spec.get('code_assets',[]))|{spec.get(k) for k in ('input_asset','reference_asset') if spec.get(k)}
        if not ids<=authorized:raise ValueError('A tool asset is no longer linked to its source paper')
        result['source_assets']=[{k:catalog.asset(a)[k] for k in ('id','name','role','sha256')} for a in sorted(ids)]
        bundle=spec.get('bundle_paths',{});copied={};targets={}
        for aid in ids:
            item=catalog.asset(aid)
            # Reference files never enter the execution workspace. Only the host compares them.
            if item['role']=='reference':continue
            relative=bundle.get(aid,item['name']);paths=relative if isinstance(relative,list) else [relative]
            if not paths or len(paths)>100 or any(not isinstance(p,str) for p in paths):raise ValueError('Invalid bundled file paths')
            for relative in paths:
                target=(root/relative).resolve()
                if not target.is_relative_to(root) or target==root:raise ValueError('Invalid bundled file path')
                reserved={'request.json','result.json','result.mat','stdout.log','stderr.log','tool.py'}
                if target.parent==root and target.name.lower() in reserved:raise ValueError('Bundled asset conflicts with a runtime file; use an author/ or data/ subdirectory')
                if target in targets and targets[target]!=aid:raise ValueError('Different source assets map to the same file path; resolve the version conflict')
                targets[target]=aid;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(catalog.asset_path(aid),target);copied.setdefault(aid,target)
        request=root/'request.json';output=root/('result.mat' if tool['language']=='matlab' else 'result.json')
        payload={**inputs,'validation':validation} if spec.get('source_kind')=='author_adapter' else inputs
        request.write_text(store.encode(payload),encoding='utf-8')
        if tool['language']=='matlab':
            entry=spec.get('entrypoint','')
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',entry):raise ValueError('Invalid MATLAB function name')
            (root/(entry+'.m')).write_text(tool['code'],encoding='utf-8')
            # Only trusted host-generated quoted paths are placed in the MATLAB expression.
            q=lambda p:str(p).replace('\\','/').replace("'","''")
            mode=spec.get('request_mode','file')
            if mode not in ('file','json'):raise ValueError('MATLAB request_mode must be file or json')
            argument=store.encode(payload).replace("'","''") if mode=='json' else q(request)
            expr="addpath('"+q(root)+"'); "+entry+"('"+q(copied[spec['input_asset']])+"','"+argument+"','"+q(output)+"');"
            cmd=[_matlab(),'-batch',expr]
        else:
            (root/'tool.py').write_text(tool['code'],encoding='utf-8')
            host="import json;from pathlib import Path;from tool import run;v=run(json.loads(Path('request.json').read_text(encoding='utf-8')),Path.cwd());Path('result.json').write_text(json.dumps(v,allow_nan=False),encoding='utf-8')"
            import environments
            cmd=[environments.python(tool),'-I','-c',"import sys;sys.path.insert(0,"+repr(str(root))+");"+host]
        env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
        import environments
        result['runtime']={'language':tool['language'],'executable':cmd[0],'python_version':sys.version if tool['language']=='python' else None,'environment':environments.status(tool)}
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        with (root/'stdout.log').open('w',encoding='utf-8') as log,(root/'stderr.log').open('w',encoding='utf-8') as err:
            proc=subprocess.Popen(cmd,cwd=root,env=env,stdout=log,stderr=err,**flags)
            with LOCK:ACTIVE[rid]=(paper_id,proc)
            try:code=proc.wait(timeout=min(int(spec.get('timeout_seconds',1800)),3600))
            except subprocess.TimeoutExpired:
                cancelled(paper_id);raise ValueError('Execution timed out')
        result['exit_code']=code
        result['stdout']=(root/'stdout.log').read_text(encoding='utf-8',errors='replace')[-20000:]
        result['stderr']=(root/'stderr.log').read_text(encoding='utf-8',errors='replace')[-12000:]
        if code:raise ValueError('Tool program failed; inspect the execution logs')
        if not output.is_file():raise ValueError('Tool did not write its declared result file')
        actual=loadmat(output,mat_dtype=True) if tool['language']=='matlab' else json.loads(output.read_text(encoding='utf-8'))
        if validation:
            refpath=catalog.asset_path(spec['reference_asset'])
            reference=loadmat(refpath,mat_dtype=True) if refpath.suffix.lower()=='.mat' else json.loads(refpath.read_text(encoding='utf-8-sig'))
            result['comparison']=compare(actual,reference,spec.get('validation_fields'),spec.get('zero_tolerance'))
            result['passed']=result['comparison']['passed']
        else:
            result['outputs']={k:jsonable(v) for k,v in actual.items() if not k.startswith('__')}
        status='passed' if result.get('passed',True) else 'failed'
        current=catalog.get('tool',tool_id)
        if catalog.fingerprint(current)!=fingerprint:raise ValueError('Tool or method changed during execution; this result cannot validate the new version')
        if validation:
            with store.db() as c:c.execute('UPDATE tools SET validation_status=?,updated=? WHERE id=? AND revision=?',('verified' if status=='passed' else 'failed',store.now(),tool_id,tool['revision']))
            if status=='passed':catalog.supersede_candidates(tool_id)
    except Exception as e:
        status='failed';result['error']=str(e)
        # A failed rerun must revoke execution eligibility for this exact version.
        # Concurrent edits/deletion retain their own invalidation state.
        if validation:
            try:
                current=catalog.get('tool',tool_id)
                if catalog.fingerprint(current)==fingerprint:
                    with store.db() as c:c.execute("UPDATE tools SET validation_status='failed',updated=? WHERE id=? AND revision=?",(store.now(),tool_id,tool['revision']))
            except ValueError:pass
    finally:
        with LOCK:ACTIVE.pop(rid,None)
    result['finished']=store.now()
    with store.db() as c:c.execute('UPDATE runs SET status=?,result=?,updated=? WHERE id=?',(status,store.encode(result),store.now(),rid))
    (root/'report.json').write_text(store.encode(result),encoding='utf-8')
    return {'id':rid,'status':status,**result}
