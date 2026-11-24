"""Host boundary, persistent task recovery and immutable benchmark regressions."""
import copy,json,queue,shutil,sys,unittest
from pathlib import Path
from unittest.mock import patch
import fitz
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,store,execution_agent as agent,engine,runner
from fastapi.testclient import TestClient
from app import app

class ExecutionTask(unittest.TestCase):
 def setUp(self):
  self.previous=store.DATA;store.DATA=store.ROOT/'.local/checks'/store.uid();catalog.init()
  p=fitz.open();p.new_page().insert_text((40,50),'Published experiment: x = 2; y = x cubed; y = 8.')
  self.paper,_=catalog.import_paper('paper.pdf',p.tobytes());p.close();self.pid=self.paper['id']
  self.refs=[{'page':1,'quote':'Published experiment: x = 2;'}]
  self.method=catalog.create_method(self.pid,'Cube',{'problem':'Compute cubic output','assumptions':'Exact arithmetic','equations':'y=x^3','evidence':self.refs})
  self.data=catalog.add_asset(self.pid,'raw.json',b'{"x":2}','data');self.ref=catalog.add_asset(self.pid,'ref.json',b'{"y":[8]}','reference')
  self.job=catalog.job('reproduction',self.pid);self.task=agent.Task(self.job['id'],self.pid)
  self.plan={'method_id':self.method['id'],'experiment':'Published x=2 cube output','summary':'Exact model','version_notes':[],'selected_asset_ids':[self.data['id']],'derivations':[],'reference_asset':self.ref['id'],'reference_name':'','validation_fields':['y']}
 def tearDown(self):
  root=store.DATA;store.DATA=self.previous
  if root.resolve().is_relative_to(store.ROOT/'.local/checks'):shutil.rmtree(root)
 def test_native_execution_profile_and_chat_profile_are_distinct(self):
  from codex_bridge import Bridge
  client=Bridge('agent');calls=[]
  def rpc(name,params,*args,**kwargs):
   calls.append((name,params));return {'thread':{'id':'test'}} if name.startswith('thread/') else {'turn':{'id':'turn'}}
  with patch.object(client,'rpc',side_effect=rpc):client.begin('Inspect files',persistent=True,task=self.task.configuration())
  start=calls[0][1];self.assertEqual(start['sandbox'],'workspace-write');self.assertEqual(start['runtimeWorkspaceRoots'],[str(self.task.root)]);self.assertEqual(start['config']['web_search'],'live');self.assertFalse(start['config']['sandbox_workspace_write']['network_access']);self.assertEqual(len(start['dynamicTools']),4)
  turn=calls[1][1];self.assertEqual(turn['cwd'],str(self.task.root));self.assertEqual(turn['runtimeWorkspaceRoots'],[str(self.task.root)]);self.assertEqual(turn['sandboxPolicy']['writableRoots'],[str(self.task.root)])
  with self.assertRaises(ValueError):Bridge('agent').begin('Unscoped execution')
 def test_references_are_not_staged_and_raw_copy_tampering_is_detected(self):
  rows=self.task.stage();r=next(v for v in rows if v['id']==self.ref['id']);self.assertIsNone(r['path'])
  raw=next(v for v in rows if v['id']==self.data['id']);(self.task.root/raw['path']).write_text('changed')
  with self.assertRaisesRegex(ValueError,'modified'):self.task.stage()
  self.assertEqual(catalog.asset_path(self.data['id']).read_bytes(),b'{"x":2}')
 def test_generated_artifact_cannot_escape_or_publish_reference(self):
  self.task.pin(self.plan);path=self.task.root/'derived'/'input.json';path.write_text('{"x":2}')
  args={'path':'derived/input.json','role':'data','reason':'Preserve actual observation','source_assets':[self.data['id']],'evidence':self.refs}
  a=self.task.publish(args);self.assertEqual(catalog.asset(a['id'])['metadata']['task_id'],self.task.id)
  for name in ('../outside.py','C:/outside.py','materials/'+self.data['id']+'/raw.json'):
   with self.assertRaises(ValueError):self.task.safe_file(name)
  with self.assertRaises(ValueError):self.task.publish({**args,'role':'reference'})
  with self.assertRaises(ValueError):self.task.publish({**args,'source_assets':[self.ref['id']]})
 def test_pinned_reference_cannot_be_changed_or_validation_fields_dropped(self):
  first=self.task.pin(self.plan);self.assertEqual(first,self.task.pin(self.plan))
  with self.assertRaisesRegex(ValueError,'already pinned'):self.task.pin({**self.plan,'validation_fields':['different']})
  other=catalog.add_asset(self.pid,'other-ref.json',b'{"y":[9]}','reference')
  with self.assertRaises(ValueError):self.task.pin({**self.plan,'reference_asset':other['id']})
 def test_restart_keeps_files_plan_and_execution_task(self):
  self.task.pin(self.plan);path=self.task.root/'adapters'/'map.py';path.write_text('print(2)')
  engine.recover();self.assertEqual(agent.get(self.task.id)['status'],'interrupted')
  task=agent.Task(self.job['id'],self.pid);self.assertEqual(task.id,self.task.id);self.assertEqual(task.root,self.task.root);self.assertTrue(path.exists());self.assertEqual(agent.get(task.id)['state']['pin']['reference_asset'],self.ref['id'])
 def test_stop_endpoint_preserves_task_but_prevents_late_commits(self):
  with TestClient(app) as client:
   # Lifespan recovery marks existing jobs interrupted, simulate an active task.
   catalog.job_update(self.job['id'],status='running')
   r=client.post('/api/papers/'+self.pid+'/stop',headers={'x-econlab':'1'},json={})
  self.assertEqual(r.status_code,200);self.assertEqual(catalog.job_get(self.job['id'])['status'],'cancelled')
  with self.assertRaises(ValueError):self.task.pin(self.plan)
  self.assertTrue(self.task.root.exists());self.assertEqual(agent.get(self.task.id)['status'],'interrupted')
 def test_delete_removes_only_this_paper_task_and_memory(self):
  sibling=self.task.root.parent.parent/'0123456789abcdef'
  sibling.mkdir();(sibling/'keep.txt').write_text('keep')
  session=catalog.session('paper_agent',self.pid);catalog.save_session(session['id'],'native-task-thread')
  with patch.object(engine.bridge,'rpc',return_value={}) as archive:engine.delete_paper(self.pid)
  archive.assert_called_with('thread/archive',{'threadId':'native-task-thread'})
  self.assertFalse(self.task.root.exists());self.assertTrue((sibling/'keep.txt').exists());self.assertIsNone(agent.latest(self.pid))
 def test_native_command_failure_and_success_are_recorded_as_real_events(self):
  self.task.on_event({'method':'item/completed','params':{'item':{'id':'cmd1','type':'commandExecution','command':'python adapters/check.py','exitCode':1,'status':'failed','aggregatedOutput':'field missing'}}})
  state=agent.get(self.task.id)['state'];self.assertEqual(state['execution_count'],1)
  import materials
  self.assertEqual(materials.state(self.pid)['status'],'repairing');self.assertTrue((self.task.root/'logs'/'execution.jsonl').is_file())
 def test_new_target_creates_a_separate_workspace(self):
  self.task.pin(self.plan)
  other=agent.Task(self.job['id'],self.pid,{'experiment_request':'A different predeclared experiment'})
  self.assertNotEqual(other.id,self.task.id);self.assertIsNone(agent.get(other.id)['state']['pin']);self.assertTrue(self.task.root.exists())
 def test_matlab_scalar_json_scalar_comparison_keeps_array_shapes_strict(self):
  self.assertTrue(runner.compare({'v':[[8.0]]},{'v':8.0},['v'])['passed'])
  self.assertFalse(runner.compare({'v':[[8.0,9.0]]},{'v':[8.0,9.0]},['v'])['passed'])
  self.assertFalse(runner.compare({'v':[[8.8]]},{'v':8.0},['v'])['passed'])
 def test_search_refresh_preserves_pinned_experiment(self):
  pin=self.task.pin(self.plan);task=agent.Task(self.job['id'],self.pid,{'refresh_search':True})
  self.assertEqual(task.id,self.task.id);self.assertEqual(agent.get(task.id)['state']['pin'],pin)
 def test_quota_failure_preserves_work_for_later_resume(self):
  self.task.pin(self.plan);(self.task.root/'adapters'/'keep.py').write_text('print(2)')
  with patch.object(engine,'ask',side_effect=ValueError('usage limit exceeded')):
   with self.assertRaisesRegex(ValueError,'usage limit'):agent.prepare(self.job['id'],self.pid)
  self.assertEqual(agent.latest(self.pid)['status'],'waiting_account');self.assertTrue((self.task.root/'adapters'/'keep.py').exists());self.assertIsNotNone(agent.latest(self.pid)['state']['pin'])
 def test_stale_or_foreign_execution_id_cannot_change_another_task(self):
  agent.record_validation('0000000000000000',{'id':'tool','spec':{'execution_task_id':self.task.id}},{'run_id':'fake','status':'passed'})
  self.assertEqual(agent.get(self.task.id)['status'],'running')
  agent.record_validation(self.pid,{'id':'tool','spec':{'execution_task_id':'nonexistent'}},{'run_id':'fake','status':'passed'})
 def test_zero_tolerance_is_pinned_before_submission(self):
  invalid={**self.plan,'zero_tolerance_json':'{"y":-1}'}
  with self.assertRaises(ValueError):self.task.pin(invalid)
  pin=self.task.pin({**self.plan,'zero_tolerance_json':'{"y":0.0}'})
  self.assertEqual(pin['zero_tolerance'],{'y':0.0})
 def test_quota_at_resume_does_not_replace_native_thread(self):
  s=catalog.session('paper_agent',self.pid);catalog.save_session(s['id'],'persistent-thread','Keep the source mapping')
  with patch.object(engine.bridge,'begin',side_effect=RuntimeError('usage limit exceeded')) as begin:
   with self.assertRaisesRegex(RuntimeError,'usage limit'):engine.ask(self.job['id'],self.pid,'Continue',task=self.task.configuration())
  self.assertEqual(begin.call_count,1);self.assertEqual(catalog.session('paper_agent',self.pid)['thread_id'],'persistent-thread')
 def test_missing_native_thread_rebuilds_from_durable_memory(self):
  s=catalog.session('paper_agent',self.pid);catalog.save_session(s['id'],'missing-thread','Saved data mapping');catalog.message(s['id'],'user','Earlier mapping discussion')
  q=queue.Queue();q.put({'method':'item/completed','params':{'item':{'type':'agentMessage','text':'Recovered'}}});q.put({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
  with patch.object(engine.bridge,'begin',side_effect=[RuntimeError('no rollout found for thread id'),('new-thread','turn',q,'test')]) as begin:
   self.assertEqual(engine.ask(self.job['id'],self.pid,'Continue',task=self.task.configuration()),'Recovered')
  self.assertIn('Saved data mapping',begin.call_args.args[0]);self.assertIn('Earlier mapping discussion',begin.call_args.args[0]);self.assertEqual(catalog.session('paper_agent',self.pid)['thread_id'],'new-thread')
 def test_repair_receives_actual_formal_failure_and_keeps_reference_outside(self):
  code="def run(inputs,workspace):\n raise RuntimeError('actual fixture adapter failure')\n"
  asset=catalog.add_asset(self.pid,'failure.py',code.encode(),'code')
  spec={'parameters':[],'asset_ids':[self.data['id'],self.ref['id']],'code_assets':[asset['id']],'input_asset':self.data['id'],'reference_asset':self.ref['id'],'validation_fields':['y']}
  tool=catalog.create_tool(self.pid,self.method['id'],'Failing fixture',spec,code)
  catalog.review('tool',tool['id'],'confirmed','Test reviewer','Reviewed controlled failure fixture',tool['revision'])
  run=runner.execute(tool['id'],{},True,self.pid);self.assertEqual(run['status'],'failed')
  self.task.prompt();mission=json.loads((self.task.root/'mission.json').read_text(encoding='utf-8'))
  feedback=next(r for r in mission['formal_run_feedback'] if r['id']==run['id'])
  self.assertIn('actual fixture adapter failure',feedback['result']['stderr']);self.assertEqual(feedback['tool_revision'],tool['revision'])
  self.assertTrue(feedback['files']);self.assertTrue(any(f['path'].endswith('stderr.log') for f in feedback['files']))
  self.assertFalse(any(p.name=='ref.json' for p in (self.task.root/'logs'/'formal-runs').rglob('*')))

 def test_codex_failure_returns_json_instead_of_generic_server_error(self):
  with TestClient(app) as client,patch('app.bridge.models',side_effect=RuntimeError('Codex startup permission denied')):
   response=client.get('/api/models')
  self.assertEqual(response.status_code,503);self.assertEqual(response.json()['detail'],'Codex startup permission denied')
 def test_native_startup_permission_error_keeps_diagnostic_log(self):
  import codex_bridge
  from types import SimpleNamespace
  client=codex_bridge.Bridge()
  def launch(*args,**kwargs):
   kwargs['stderr'].write(b'Error: Permission denied (os error 5)\n');kwargs['stderr'].flush()
   return SimpleNamespace(poll=lambda:1,stdout=[])
  with patch.object(codex_bridge,'executable',return_value=['codex']),patch.object(codex_bridge.subprocess,'Popen',side_effect=launch),patch.object(client,'_rpc',side_effect=RuntimeError('process exited')):
   with self.assertRaisesRegex(RuntimeError,'访问本机运行目录被拒绝'):client.start()
  self.assertIn('os error 5',(store.DATA/'codex-chat.log').read_text())

 def test_pre_pin_inspection_is_draft_not_a_completed_tool(self):
  before=[t['id'] for t in catalog.get('paper',self.pid)['tools']]
  path=self.task.root/'logs'/'source-inspection.json';path.write_text('{"missing":"original data"}')
  asset=self.task.publish({'path':'logs/source-inspection.json','role':'derivation','reason':'Record real missing source','source_assets':[self.data['id']],'evidence':self.refs})
  record=catalog.asset(asset['id']);self.assertTrue(record['metadata']['pre_pin']);self.assertEqual(record['metadata']['provenance'],'agent_inspection_artifact')
  self.assertIsNone(agent.get(self.task.id)['state'].get('pin'))
  import reproduction
  with self.assertRaisesRegex(ValueError,'independent reference'):
   reproduction.derive(self.pid,{'operation':'json_fields','source_asset':asset['id'],'name':'fake-ref.json','role':'reference','fields':['missing'],'values_json':'{}','evidence':self.refs,'reason':'Cannot self-reference a generated result'})
  self.assertEqual([t['id'] for t in catalog.get('paper',self.pid)['tools']],before)

if __name__=='__main__':unittest.main()
