"""Reviewed, per-tool runtime setup; no AI-provided shell command is executed."""
from __future__ import annotations
import json,os,re,subprocess,sys,venv
from pathlib import Path
import catalog,store

PACKAGE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:(?:==|>=|<=|>|<|~=|!=)[A-Za-z0-9_.+*-]+(?:,(?:>=|<=|>|<|!=)[A-Za-z0-9_.+*-]+)*)?')

def available():
    """Read-only discovery; installation does not imply an available license."""
    import runner
    value={'python':{'executable':sys.executable,'version':sys.version},'matlab':{'installed':False,'license_checked':False}}
    try:value['matlab'].update(installed=True,executable=runner._matlab())
    except ValueError:pass
    return value


def configuration(tool):
    value=tool['spec'].get('environment',{})
    if not isinstance(value,dict):raise ValueError('Environment must be an object')
    requirements=value.get('requirements',[])
    if tool['language']=='matlab':
        toolboxes=value.get('toolboxes',requirements)
        functions=value.get('matlab_functions',[])
        if not isinstance(toolboxes,list) or len(toolboxes)>20 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _().+\-]{0,120}',v) for v in toolboxes):raise ValueError('MATLAB环境请列出产品或工具箱名称')
        if not isinstance(functions,list) or len(functions)>100 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',v) for v in functions):raise ValueError('MATLAB依赖函数名无效')
        return {'language':'matlab','requirements':[],'toolboxes':sorted(set(toolboxes)),'matlab_functions':sorted(set(functions))}
    if not isinstance(requirements,list) or len(requirements)>20 or any(not isinstance(p,str) or not PACKAGE.fullmatch(p) for p in requirements):raise ValueError('Use package names and version constraints; shell commands, URLs and local installers are not accepted')
    return {'language':tool['language'],'requirements':sorted(set(requirements))}

def root(tool):return store.DATA/'environments'/tool['id']/store.digest(configuration(tool))[:20]

def status(tool):
    config=configuration(tool);manifest=root(tool)/'environment.json'
    if manifest.exists():return json.loads(manifest.read_text(encoding='utf-8'))
    return {'status':'needs_setup' if config['requirements'] or tool['language']=='matlab' else 'host_runtime','configuration':config,'note':'依赖安装与环境检测在代码审核后执行。'}

def python(tool):
    config=configuration(tool)
    if not config['requirements']:return sys.executable
    manifest=status(tool);exe=root(tool)/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if manifest.get('status')!='ready' or not exe.is_file():raise ValueError('先审核工具并完成运行环境配置')
    return str(exe)

def prepare(tool_id,check=lambda:None):
    tool=catalog.get('tool',tool_id)
    import automation
    if not automation.allowed('tool',tool):raise ValueError('先完成代码及依赖配置的自动检查或人工审核，再准备运行环境')
    config=configuration(tool);folder=root(tool);folder.mkdir(parents=True,exist_ok=True);manifest=folder/'environment.json'
    before=store.digest({'revision':tool['revision'],'spec':tool['spec'],'code':tool['code'],'method_revision':catalog.get('method',tool['method_id'])['revision']})
    previous=status(tool);result={'status':'preparing','configuration':config,'tool_id':tool_id,'started':store.now()}
    manifest.write_text(store.encode(result),encoding='utf-8')
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ('TOKEN','SECRET','PASSWORD','API_KEY'))}
    try:
        check()
        if tool['language']=='matlab':
            from runner import _matlab
            exe=_matlab();expr="disp(['ECONLAB_MATLAB ' version]); products=ver; disp(products);"
            for name in config['toolboxes']:expr+="assert(any(strcmpi({products.Name},'"+name+"')),'Required MATLAB product is unavailable: "+name+"');"
            for name in config['matlab_functions']:expr+="assert(~isempty(which('"+name+"')),'Required MATLAB function is unavailable: "+name+"');"
            command=[exe,'-batch',expr]
        else:
            exe=sys.executable
            if config['requirements']:
                # Use a separate environment; installation never changes the app environment.
                exe=folder/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
                if not exe.exists():venv.EnvBuilder(with_pip=True).create(folder)
                command=[str(exe),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',*config['requirements']]
                proc=subprocess.run(command,capture_output=True,timeout=600,env=env,**flags)
                (folder/'install.log').write_bytes(proc.stdout+proc.stderr)
                if proc.returncode:raise ValueError('依赖安装失败，见环境记录；可能需要可用网络或对应Python版本的二进制包')
            command=[str(exe),'-c','import sys,importlib.metadata,json;print(json.dumps({"python":sys.version,"packages":{d.metadata["Name"]:d.version for d in importlib.metadata.distributions()}},sort_keys=True))']
        check();proc=subprocess.run(command,capture_output=True,timeout=90,env=env,**flags)
        if proc.returncode:raise ValueError('运行环境检测失败：'+proc.stderr.decode('utf-8',errors='replace')[-1500:])
        current=catalog.get('tool',tool_id)
        after=store.digest({'revision':current['revision'],'spec':current['spec'],'code':current['code'],'method_revision':catalog.get('method',tool['method_id'])['revision']})
        if after!=before:raise ValueError('工具已变更，请重新审核环境配置')
        result.update(status='ready',executable=str(exe),detected=proc.stdout.decode('utf-8',errors='replace')[-18000:])
    except Exception as exc:result.update(status='failed',error=str(exc))
    result['finished']=store.now();manifest.write_text(store.encode(result),encoding='utf-8')
    if config['requirements'] and previous.get('status')=='ready' and previous.get('detected')!=result.get('detected'):
        with store.db() as c:c.execute("UPDATE tools SET validation_status='stale' WHERE id=? AND validation_status='verified'",(tool_id,))
    return result
