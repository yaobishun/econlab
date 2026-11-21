"""Real Codex persistence and solver probe; isolated test DB only."""
import os,sys,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];os.environ['ECONLAB_DATA']=str(ROOT/'.local'/'ui-preview');sys.path.insert(0,str(ROOT))
import catalog,engine,store,runner,fitz
from provider_bridge import bridge

def chat(pid,text):
 j=catalog.job('chat',pid);catalog.job_update(j['id'],status='running')
 try:
  r=engine.chat(j['id'],pid,text,False);catalog.job_update(j['id'],status='completed');return r
 except Exception as e:catalog.job_update(j['id'],status='failed',error=str(e));raise
if len(sys.argv)>1 and sys.argv[1]!='--solver-only':
 try:
  pid=sys.argv[1];before=catalog.session('paper',pid)['thread_id'];r=chat(pid,'What was the exact test codename I asked you to remember? Reply with the codename only. Do not change any library.');after=catalog.session('paper',pid)['thread_id'];(store.DATA/'restart-recall.json').write_text(store.encode({'same_thread':before==after,'answer':r['answer']}),encoding='utf-8')
 finally:bridge.close()
 sys.exit(0)
catalog.init();d=fitz.open();p=d.new_page();p.insert_text((50,50),'The linear test model returns x unchanged.');paper,_=catalog.import_paper('Software fixture.pdf',d.tobytes());d.close();pid=paper['id'];checks={}
try:
 if '--solver-only' in sys.argv:
  checks=json.loads((store.DATA/'live-workflow-checks.json').read_text(encoding='utf-8'));checks={k:v for k,v in checks.items() if k.startswith('native_thread') or k=='prior_codename_recalled'}
 else:
  chat(pid,'Remember that this software acceptance test codename is M-217. Do not change any library; only preserve it in conversation memory.');bridge.close();subprocess.run([sys.executable,'-X','utf8',__file__,pid],check=True)
  recall=json.loads((store.DATA/'restart-recall.json').read_text(encoding='utf-8'));checks['native_thread_survives_restart']=recall['same_thread'];checks['prior_codename_recalled']='M-217' in recall['answer']
 m=catalog.create_method(pid,'软件验收夹具线性模型 linear fixture',{'problem':'软件验收线性模型，给定x输出answer；software acceptance linear fixture','assumptions':'仅软件测试；输出等于x，不推断真实经济。','equations':'answer=[x]','inputs':'x','outputs':'answer数组','data_requirements':'x','scope':'synthetic test only','evidence':[{'paper_id':pid,'page':1,'quote':'The linear test model returns x unchanged.'}]},'software-test')
 a=catalog.add_asset(pid,'author.py',b'def reference_model(x): return [x]\n','code');b=catalog.add_asset(pid,'input.json',b'{"x":100}','data');c=catalog.add_asset(pid,'reference.json',b'{"answer":[100]}','reference')
 spec={'asset_ids':[a['id'],b['id'],c['id']],'author_code_assets':[a['id']],'bundle_paths':{a['id']:'author.py'},'input_asset':b['id'],'reference_asset':c['id'],'parameters':[{'name':'x','type':'number','required':True,'min':0,'max':200,'default':100}],'validation_fields':['answer'],'validation_scope':'Synthetic software fixture, not economics paper reproduction'}
 t=catalog.create_tool(pid,m['id'],'软件验收线性工具',spec,'from author import reference_model\ndef run(inputs,workspace):\n return {"answer":reference_model(inputs["x"])}\n',actor='software-test');catalog.review('method',m['id'],'confirmed','software-test','Inspected synthetic assumptions and evidence',m['revision']);catalog.review('tool',t['id'],'confirmed','software-test','Inspected synthetic fixture code',t['revision']);checks['reference_validation']=runner.execute(t['id'],{},True,pid)['status']=='passed'
 j=catalog.job('solve');catalog.job_update(j['id'],status='running');plan=engine.solve_plan(j['id'],None,'软件验收夹具：用linear fixture线性模型，x=100，求answer。仅软件流程测试，已确认全部假设，不讨论现实政策。',{'x':100,'data':'synthetic input','goal':'answer=[x]'});catalog.job_update(j['id'],status='completed');checks['correct_method']=plan['selected_method_id']==m['id'];checks['verified_tool_selected']=plan['selected_tool_id']==t['id'];checks['no_missing_information']=not plan['missing_information']
 if not all(checks.values()):raise AssertionError({k:v for k,v in plan.items() if k!='candidates'})
 j=catalog.job('solve_execute');catalog.job_update(j['id'],status='running');r=engine.solve_execute(j['id'],None,plan['id'],{'x':100},True);catalog.job_update(j['id'],status='completed');checks['computed_answer']=r['outputs']['answer']==[100];checks['sources_and_explanation']=bool(r['sources'] and r['explanation']) and not r.get('explanation_warning');checks['report_saved']=(store.DATA/'runs'/r['run_id']/'report.json').is_file();print(store.encode({'checks':checks,'answer':r['outputs'],'explanation':r['explanation']}),flush=True)
finally:
 (store.DATA/'live-workflow-checks.json').write_text(store.encode(checks),encoding='utf-8')
 try:engine.delete_paper(pid)
 except Exception:pass
 with store.db() as c:sessions=[dict(s) for s in c.execute("SELECT * FROM sessions WHERE scope='solve'")]
 for s in sessions:
  if s['thread_id']:
   try:bridge.rpc('thread/archive',{'threadId':s['thread_id']})
   except Exception:pass
 bridge.close()
