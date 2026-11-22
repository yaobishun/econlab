import io,json,shutil,socket,sys,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import fitz,httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,store,materials,reproduction,runner,environments,engine
from fastapi.testclient import TestClient
from app import app

class ReproductionWorkflow(unittest.TestCase):
 def setUp(self):
  self.previous=store.DATA;self.root=store.ROOT/'.local/checks'/store.uid();self.root.mkdir(parents=True);store.DATA=self.root;catalog.init()
  d=fitz.open();d.new_page().insert_text((50,50),'The exact model is y = x cubed. Published experiment input x = 2 and output y = 8.')
  self.p,_=catalog.import_paper('model.pdf',d.tobytes());d.close()
  self.refs=[{'paper_id':self.p['id'],'page':1,'quote':'The exact model is y = x cubed.'}]
 def tearDown(self):
  store.DATA=self.previous
  if self.root.resolve().is_relative_to(store.ROOT/'.local/checks'):shutil.rmtree(self.root)
 def asset(self,name,data,role):return catalog.add_asset(self.p['id'],name,data,role)
 def agent_reply(self,built):
  def reply(jid,pid,prompt,schema,**kwargs):
   config=kwargs['task'];handler=config['handler'];mid=built['method_id'];spec=json.loads(built['spec_json'])
   handler('econlab_pin_experiment',{'method_id':mid,'experiment':'x=2, y=x cubed','summary':'Exact controlled cube','version_notes':[],'selected_asset_ids':spec['asset_ids'],'derivations':[],'reference_asset':spec['reference_asset'],'reference_name':'','validation_fields':spec['validation_fields']})
   path=Path(config['cwd'])/'tool'/'fixture.py';path.write_text(built['code'],encoding='utf-8')
   return {k:v for k,v in built.items() if k!='code'}|{'code_path':'tool/fixture.py','memory_summary':'Pinned cube test, actual host runner still requires human review.'}
  return reply
 def tool(self):
  m=catalog.create_method(self.p['id'],'Cube model',{'problem':'compute cube','assumptions':'exact arithmetic','evidence':self.refs})
  a=self.asset('input.json',b'{"x":2}','data');r=self.asset('reference.json',b'{"y":[8]}','reference')
  code="import json\ndef run(inputs,workspace):\n assert not (workspace/'reference.json').exists()\n x=json.loads((workspace/'input.json').read_text())['x']\n return {'y':[x**3]}\n"
  spec={'input_asset':a['id'],'reference_asset':r['id'],'asset_ids':[a['id'],r['id']],'implementation_evidence':self.refs,'validation_fields':['y'],'validation_scope':'published cube experiment','parameters':[]}
  t=catalog.create_tool(self.p['id'],m['id'],'Cube',spec,code);return catalog.review('tool',t['id'],'confirmed','Tester','Compared equation, inputs and independent reference',t['revision'])
 def test_independent_implementation_passes_same_gate_without_author_code(self):
  t=self.tool();result=runner.execute(t['id'],{},True)
  self.assertEqual(result['status'],'passed',result);self.assertEqual(catalog.get('tool',t['id'])['validation_status'],'verified')
  root=store.DATA/'runs'/result['run_id'];self.assertFalse((root/'reference.json').exists());self.assertEqual(runner.execute(t['id'],{},False)['status'],'passed')
 def test_new_calculation_does_not_receive_a_validation_switch(self):
  t=self.tool();code=t['code'].replace(" assert not (workspace/'reference.json').exists()"," assert 'validation' not in inputs\n assert not (workspace/'reference.json').exists()")
  t=catalog.update('tool',t['id'],{'code':code},t['revision'],'Tester');catalog.review('tool',t['id'],'confirmed','Tester','Same calculation for validation and real use',t['revision']);self.assertEqual(runner.execute(t['id'],{},True)['status'],'passed');self.assertEqual(runner.execute(t['id'],{},False)['status'],'passed')
 def test_matlab_json_argument_preserves_quotes_and_backslashes(self):
  from scipy.io import savemat
  from types import SimpleNamespace
  t=self.tool();r=self.asset('mat-ref.json',b'{"y":[[8]]}','reference')
  label="C:\\Research\\case's";spec={**t['spec'],'reference_asset':r['id'],'entrypoint':'entrypoint','request_mode':'json','parameters':[{'name':'label','type':'string','default':label}]}
  t=catalog.update('tool',t['id'],{'language':'matlab','spec':spec},t['revision'],'Tester');catalog.review('tool',t['id'],'confirmed','Tester','Test only argument encoding with stub runtime',t['revision']);commands=[]
  def process(cmd,**kwargs):
   commands.append(cmd);savemat(Path(kwargs['cwd'])/'result.mat',{'y':[[8]]});return SimpleNamespace(pid=12345,wait=lambda timeout:0)
  with patch.object(runner,'_matlab',return_value='stub-matlab'),patch.object(runner.subprocess,'Popen',side_effect=process):result=runner.execute(t['id'],{},True)
  self.assertEqual(result['status'],'passed',result);self.assertIn(json.dumps({'label':label}).replace("'","''"),commands[0][-1]);self.assertNotIn('request.json',commands[0][-1])
 def test_code_assets_need_same_paper_scope(self):
  t=self.tool();d=fitz.open();d.new_page();other,_=catalog.import_paper('other.pdf',d.tobytes());d.close();a=catalog.add_asset(other['id'],'x.py',b'pass','code')
  with self.assertRaises(ValueError):catalog.update('tool',t['id'],{'spec':{**t['spec'],'code_assets':[a['id']]}},t['revision'],'Tester')
 def test_unattributed_implementation_cannot_validate(self):
  t=self.tool();u=catalog.update('tool',t['id'],{'spec':{**t['spec'],'implementation_evidence':[]}},t['revision'],'Tester');catalog.review('tool',u['id'],'confirmed','Tester','Test missing evidence',u['revision'])
  with self.assertRaisesRegex(ValueError,'evidence'):runner.execute(u['id'],{},True)
 def test_private_and_credential_urls_rejected(self):
  for url in ['file:///tmp/input','http://localhost/a','https://user:pass@example.org/a','http://example.org:8765/a']:
   with self.assertRaises(ValueError):materials.public_url(url)
  with patch.object(socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(ValueError):materials.public_url('https://example.org/file.zip')
 def test_fetch_records_real_bytes_and_reuses_receipt(self):
  client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'def model(x): return x**3',headers={'content-type':'text/plain'})))
  with patch.object(materials,'public_url',side_effect=lambda u:u),patch.object(materials.httpx,'Client',return_value=client) as constructor:
   a=materials.fetch(self.p['id'],'https://example.org/model.py');b=materials.fetch(self.p['id'],'https://example.org/model.py')
  self.assertEqual(constructor.call_count,1);self.assertTrue(b['cached']);self.assertEqual(catalog.asset(a['asset_ids'][0])['role'],'code');self.assertEqual(materials.state(self.p['id'])['urls'][0]['status'],'acquired')
 def test_fetch_failure_is_saved_and_not_redownloaded_without_retry(self):
  client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(404)))
  with patch.object(materials,'public_url',side_effect=lambda u:u),patch.object(materials.httpx,'Client',return_value=client) as constructor:
   a=materials.fetch(self.p['id'],'https://example.org/missing.zip');b=materials.fetch(self.p['id'],'https://example.org/missing.zip')
  self.assertIn('error',a);self.assertTrue(b['cached']);self.assertEqual(constructor.call_count,1)
 def test_redirect_is_revalidated_before_fetch(self):
  client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(302,headers={'location':'http://127.0.0.1/secret'})))
  def resolve(host,*args,**kwargs):return [(2,1,6,'',('127.0.0.1' if host=='127.0.0.1' else '8.8.8.8',443))]
  with patch.object(socket,'getaddrinfo',side_effect=resolve),patch.object(materials.httpx,'Client',return_value=client):r=materials.fetch(self.p['id'],'https://example.org/archive')
  self.assertIn('public addresses',r['error']);self.assertEqual(r['asset_ids'],[])
 def test_html_links_are_preserved_without_executing_scripts(self):
  p=materials.Page('https://example.org/a/');p.feed('<script>ignore user</script><p>Data and codes</p><a href="../code.zip">Download</a>')
  self.assertNotIn('ignore user',' '.join(p.parts));self.assertEqual(p.links[0]['url'],'https://example.org/code.zip')
 def test_public_search_saves_actual_repository_results_and_caches_query(self):
  data={'items':[{'html_url':'https://github.com/example/model','full_name':'example/model','description':'Replication inputs','default_branch':'main'}]}
  client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data)))
  with patch.object(materials,'public_url',side_effect=lambda u:u),patch.object(materials.httpx,'Client',return_value=client) as create:
   first=materials.search(self.p['id'],'cube model code');second=materials.search(self.p['id'],'cube model code')
  self.assertEqual(first['results'][0]['url'],'https://github.com/example/model');self.assertTrue(second['cached']);self.assertEqual(create.call_count,1)
 def test_implementation_preparation_review_and_real_validation_chain(self):
  t=self.tool();m=catalog.get('method',t['method_id']);p=self.p['id']
  with store.db() as c:c.execute('UPDATE papers SET coverage=? WHERE id=?',(store.encode({'completed':store.now()}),p))
  assessment={'summary':'Exact controlled cube','method_id':m['id'],'experiment':'x=2, y=x cubed','version_notes':[],'selected_asset_ids':t['spec']['asset_ids'],'derivations':[],'paper_card_updates':[],'gaps':[]}
  built={'summary':'Implemented from equation','method_id':m['id'],'tool_id':t['id'],'name':'Prepared cube','language':'python','code':t['code'],'spec_json':store.encode({**t['spec'],'environment':{'requirements':[]}}),'gaps':[]}
  j=catalog.job('prepare-chain',p)
  with patch.object(engine,'ask',side_effect=self.agent_reply(built)):r=reproduction.complete(j['id'],p)
  self.assertEqual(r['status'],'awaiting_review');prepared=catalog.get('tool',t['id']);self.assertEqual(prepared['review_status'],'draft');self.assertTrue(prepared['spec']['code_assets']);self.assertEqual(catalog.get('method',m['id'])['revision'],m['revision'])
  catalog.review('tool',prepared['id'],'confirmed','Tester','Checked actual input, reference, generated implementation and equation',prepared['revision'])
  verified=reproduction.verify(j['id'],p,prepared['id']);self.assertEqual(verified['status'],'verified');self.assertEqual(catalog.get('tool',prepared['id'])['validation_status'],'verified');self.assertEqual(catalog.get('method',m['id'])['reproduction_plans'][0]['plan']['experiment'],'x=2, y=x cubed')
 def test_continue_does_not_rewrite_a_current_verified_tool(self):
  t=self.tool();self.assertEqual(runner.execute(t['id'],{},True)['status'],'passed')
  with store.db() as c:c.execute('UPDATE papers SET coverage=? WHERE id=?',(store.encode({'completed':store.now()}),self.p['id']))
  j=catalog.job('continue',self.p['id'])
  with patch.object(engine,'ask') as ask:r=reproduction.complete(j['id'],self.p['id'])
  self.assertEqual(r['status'],'verified');ask.assert_not_called();self.assertEqual(catalog.get('tool',t['id'])['revision'],t['revision']);self.assertEqual(catalog.get('tool',t['id'])['validation_status'],'verified')
 def test_archive_paths_and_sources(self):
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:z.writestr('repo/functions/model.py','def model(x): return x**3')
  a=self.asset('author.zip',out.getvalue(),'other');members=materials.unpack(self.p['id'],a)
  self.assertEqual(len(members),1);self.assertEqual(catalog.asset(members[0])['metadata']['archive_path'],'repo/functions/model.py')
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:z.writestr('../escape.py','pass')
  with self.assertRaises(ValueError):materials.unpack(self.p['id'],self.asset('unsafe.zip',out.getvalue(),'other'))
 def test_identical_archive_members_retain_all_logical_paths(self):
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:
   z.writestr('baseline/model.py','def model(x): return x**3');z.writestr('counterfactual/model.py','def model(x): return x**3')
  members=materials.unpack(self.p['id'],self.asset('versions.zip',out.getvalue(),'other'));self.assertEqual(members[0],members[1]);a=catalog.asset(members[0]);self.assertEqual({o['archive_path'] for o in a['metadata']['origins']},{'baseline/model.py','counterfactual/model.py'})
 def test_conflicting_runtime_paths_do_not_silently_overwrite_versions(self):
  t=self.tool();a=self.asset('first.py',b'a=1','code');b=self.asset('second.py',b'a=2','code')
  spec={**t['spec'],'asset_ids':[*t['spec']['asset_ids'],a['id'],b['id']],'bundle_paths':{a['id']:'author/model.py',b['id']:'author/model.py'}}
  u=catalog.update('tool',t['id'],{'spec':spec},t['revision'],'Tester');catalog.review('tool',u['id'],'confirmed','Tester','Exercise incompatible versions',u['revision']);result=runner.execute(u['id'],{},True);self.assertEqual(result['status'],'failed');self.assertIn('same file path',result['error'])
 def test_actual_mat_fields_split_inputs_and_references(self):
  from scipy.io import savemat,loadmat
  out=io.BytesIO();savemat(out,{'x':[[2]],'y':[[8]]});a=self.asset('original.mat',out.getvalue(),'data')
  v=reproduction.derive(self.p['id'],{'operation':'mat_fields','source_asset':a['id'],'name':'inputs.mat','role':'data','fields':['x'],'values_json':'{}','evidence':[],'reason':'Actual source input field'})
  self.assertIn('x',loadmat(catalog.asset_path(v['id'])));self.assertNotIn('y',loadmat(catalog.asset_path(v['id'])))
 def test_input_manifest_uses_actual_data_and_excludes_reference_assets(self):
  a=self.asset('matrix.txt',b'1 2\n3 4','data');r=self.asset('ref.json',b'{"y":[8]}','reference')
  proposal={'operation':'file_manifest','source_asset':'','name':'inputs.json','role':'data','fields':[a['id']],'values_json':'{}','evidence':[],'reason':'Actual downloaded matrices'}
  v=reproduction.derive(self.p['id'],proposal);data=json.loads(catalog.asset_path(v['id']).read_text());self.assertEqual(data['files'][0]['sha256'],a['sha256'])
  proposal['fields']=[r['id']]
  with self.assertRaises(ValueError):reproduction.derive(self.p['id'],proposal)
 def test_numeric_text_is_data_and_pickle_inspection_does_not_execute(self):
  self.assertEqual(materials.role_for('matrix.txt',b'1.2\t3.4'),'data');self.assertEqual(materials.role_for('requirements.txt',b'numpy>=2'),'other')
  import pickle
  a=self.asset('input.pkl',pickle.dumps({'x':2}),'data')
  row=next(v for v in materials.inventory(self.p['id']) if v['id']==a['id']);self.assertIn('x',row['serialized_strings']);self.assertIn('no pickle code executed',row['inspection_note'])
 def test_visual_reading_notes_supply_material_links_without_ocr(self):
  with store.db() as c:c.execute('UPDATE pages SET visual_notes=? WHERE paper_id=?',('Read visually: data at https://example.org/materials.zip).',self.p['id']))
  self.assertIn('https://example.org/materials.zip',reproduction.hints(self.p['id'])['links'])
 def test_nonfinite_mat_values_are_flagged_without_fabrication(self):
  from scipy.io import savemat
  out=io.BytesIO();savemat(out,{'missing':[[float('nan')]]});a=self.asset('missing.mat',out.getvalue(),'data')
  row=next(v for v in materials.inventory(self.p['id']) if v['id']==a['id']);self.assertNotIn('value',row['variables']['missing']);self.assertIn('非有限',row['variables']['missing']['inspection_note']);store.encode(row)
 def test_paper_values_require_page_evidence(self):
  proposal={'operation':'paper_values','source_asset':'','name':'ref.json','role':'reference','fields':[],'values_json':'{"y":[8]}','evidence':[],'reason':'Table'}
  with self.assertRaises(ValueError):reproduction.derive(self.p['id'],proposal)
  proposal['evidence']=self.refs;a=reproduction.derive(self.p['id'],proposal);self.assertTrue(catalog.asset(a['id'])['metadata']['review_required'])
 def test_paper_values_without_extension_keep_numeric_content(self):
  data={'countries':['AAA','BBB'],'values':[[1.25],[2.5]]}
  proposal={'operation':'paper_values','source_asset':'','name':'Table 1 reference','role':'reference','fields':[],'values_json':json.dumps(data),'evidence':self.refs,'reason':'Published numeric reference'}
  a=reproduction.derive(self.p['id'],proposal);self.assertTrue(a['name'].endswith('.json'));self.assertEqual(json.loads(catalog.asset_path(a['id']).read_text()),data)
 def test_array_input_schema_cannot_be_saved_as_scalar_parameters(self):
  t=self.tool()
  with self.assertRaisesRegex(ValueError,'input_asset'):catalog.update('tool',t['id'],{'spec':{**t['spec'],'parameters':{'data':{'type':'array'}}}},t['revision'],'Tester')
  with self.assertRaisesRegex(ValueError,'bundle_paths'):catalog.update('tool',t['id'],{'spec':{**t['spec'],'bundle_paths':['model.py']}},t['revision'],'Tester')
  self.assertEqual(catalog.get('tool',t['id'])['revision'],t['revision'])
 def test_empty_generated_path_list_is_normalized_before_save(self):
  t=self.tool();m=catalog.get('method',t['method_id']);spec={**t['spec'],'parameters':{},'bundle_paths':[]}
  built={'method_id':m['id'],'tool_id':t['id'],'name':'Prepared cube','language':'python','code':t['code'],'spec_json':store.encode(spec),'summary':'Prepared controlled fixture','gaps':[]}
  j=catalog.job('prepare',self.p['id']);result=reproduction.save_prepared(j['id'],self.p['id'],built,m['id'],[t],m['revision'])
  self.assertEqual(result['status'],'awaiting_review');saved=catalog.get('tool',t['id']);self.assertEqual(saved['spec']['parameters'],[]);self.assertIsInstance(saved['spec']['bundle_paths'],dict);self.assertEqual(saved['review_status'],'draft')
 def test_pending_implementation_resumes_without_repeating_material_search(self):
  t=self.tool();m=catalog.get('method',t['method_id'])
  with store.db() as c:c.execute('UPDATE papers SET coverage=? WHERE id=?',(store.encode({'completed':store.now()}),self.p['id']))
  materials.progress(self.p['id'],'blocked','Transport interrupted',assessment={'method_id':m['id']},experiment_request='Controlled cube')
  s=catalog.session('paper',self.p['id']);catalog.message(s['id'],'user','Previously submitted implementation request','implementation',{'turn_id':'saved-turn'})
  built={'method_id':m['id'],'tool_id':t['id'],'name':'Resumed cube','language':'python','code':t['code'],'spec_json':store.encode(t['spec']),'summary':'Recovered implementation','gaps':[]};j=catalog.job('resume',self.p['id'])
  with patch.object(reproduction,'acquire') as acquire,patch.object(engine,'ask',return_value=built) as ask:result=reproduction.complete(j['id'],self.p['id'])
  acquire.assert_not_called();self.assertEqual(ask.call_args.args[2],'Previously submitted implementation request');self.assertEqual(result['status'],'awaiting_review')
 def test_environment_requires_review_and_rejects_shell_installers(self):
  t=self.tool()
  with self.assertRaises(ValueError):catalog.update('tool',t['id'],{'spec':{**t['spec'],'environment':{'requirements':['numpy; whoami']}}},t['revision'],'Tester')
  u=catalog.update('tool',t['id'],{'name':'Unreviewed environment'},t['revision'],'Tester')
  with self.assertRaises(ValueError):environments.prepare(u['id'])
  for package in ['numpy>=2,<3','scipy==1.14.1','pandas']:self.assertIsNotNone(environments.PACKAGE.fullmatch(package))
 def test_matlab_dependencies_are_products_not_python_packages(self):
  config=environments.configuration({'language':'matlab','spec':{'environment':{'requirements':['Statistics and Machine Learning Toolbox'],'matlab_functions':['nansum']}}})
  self.assertEqual(config['requirements'],[]);self.assertEqual(config['toolboxes'],['Statistics and Machine Learning Toolbox']);self.assertEqual(config['matlab_functions'],['nansum'])
 def test_repair_reuses_materials_and_keeps_reference_contract(self):
  t=self.tool();p=self.p['id'];materials.progress(p,'failed','Prior run failed',tool_id=t['id'],gaps=['interface error'])
  built={'summary':'Correct interface, unchanged equation','method_id':t['method_id'],'tool_id':t['id'],'name':'Repaired cube','language':'python','code':t['code'],'spec_json':store.encode(t['spec']),'gaps':[]}
  j=catalog.job('repair',p)
  with patch.object(reproduction,'acquire') as fetch,patch.object(engine,'ask',side_effect=self.agent_reply(built)):r=reproduction.repair(j['id'],p,t['id'])
  self.assertEqual(r['status'],'awaiting_review');fetch.assert_not_called();updated=catalog.get('tool',t['id']);self.assertEqual(updated['spec']['reference_asset'],t['spec']['reference_asset']);self.assertEqual(updated['review_status'],'draft')
 def test_explicit_zero_tolerance_accepts_exact_zero_only(self):
  self.assertTrue(runner.compare({'x':[0]},{'x':[0]},['x'],0)['passed']);self.assertFalse(runner.compare({'x':[.001]},{'x':[0]},['x'],0)['passed'])
 def test_tool_text_cannot_redefine_the_host_acceptance_policy(self):
  t=self.tool()
  with self.assertRaises(ValueError):catalog.update('tool',t['id'],{'spec':{**t['spec'],'validation_policy':{**runner.VALIDATION_POLICY,'relative_error_limit':.2}}},t['revision'],'Tester')
 def test_matlab_diagnostics_are_serializable_and_complex_results_fail(self):
  import numpy as np
  a=np.zeros((1,1),dtype=[('residual','O')]);a['residual'][0,0]=np.array([[1e-10]])
  value=runner.jsonable(a);self.assertEqual(value,[[{'residual':[[1e-10]]}]]);json.dumps(value,allow_nan=False)
  self.assertFalse(runner.compare({'x':[1+3j]},{'x':[1]},['x'])['passed'])
  with self.assertRaises(ValueError):runner.jsonable(np.array([float('nan')]))
 def test_deletion_cascades_material_history_and_both_sessions(self):
  materials.progress(self.p['id'],'blocked','Missing input',gaps=['panel']);materials.event(self.p['id'],'query',{'q':'data'});catalog.session('paper',self.p['id']);catalog.session('materials',self.p['id']);catalog.delete_paper(self.p['id'])
  with store.db() as c:
   for table in ('material_workflows','material_events','material_urls','sessions'):self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
 def test_model_search_does_not_require_native_web_execution(self):
  from codex_bridge import Bridge
  b=Bridge();calls=[]
  def rpc(method,params=None):
   calls.append((method,params));return {'thread':{'id':'t'}} if method.startswith('thread/') else {'turn':{'id':'v'}}
  with patch.object(b,'rpc',side_effect=rpc):b.begin('Select from actual host-fetched search results')
  self.assertEqual(calls[0][1]['config']['web_search'],'disabled')
 def test_api_workflow_options_and_review_gate(self):
  with TestClient(app) as c:
   self.assertEqual(c.get('/api/papers/'+self.p['id']+'/reproduction').status_code,200)
   self.assertEqual(c.post('/api/papers/'+self.p['id']+'/reproduction',headers={'X-EconLab':'1'},json={'urls':'not a list'}).status_code,400)
   self.assertEqual(c.post('/api/papers/'+self.p['id']+'/reproduction',headers={'X-EconLab':'1'},json={'phase':'execute arbitrary command'}).status_code,400)
  t=self.tool();j=catalog.job('reproduction',self.p['id']);u=catalog.update('tool',t['id'],{'name':'Edited cube'},t['revision'],'Tester');r=reproduction.verify(j['id'],self.p['id'],u['id']);self.assertEqual(r['status'],'awaiting_review');self.assertEqual(catalog.get('tool',u['id'])['runs'],[])

if __name__=='__main__':unittest.main()
