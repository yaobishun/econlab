import sys,unittest,shutil
from pathlib import Path
from unittest.mock import patch
import fitz
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,store,runner,engine
from fastapi.testclient import TestClient
from app import app

def pdf(text):
 d=fitz.open();p=d.new_page();p.insert_text((50,50),text);v=d.tobytes();d.close();return v
class CurrentEdition(unittest.TestCase):
 def setUp(self):
  self.old=store.DATA;self.root=store.ROOT/'.local'/'checks'/store.uid();self.root.mkdir(mode=0o777,parents=True);store.DATA=self.root;catalog.init();self.p,_=catalog.import_paper('first.pdf',pdf('The spatial equilibrium model assumes free labor mobility.'))
 def tearDown(self):
  store.DATA=self.old
  if self.root.resolve().is_relative_to(store.ROOT/'.local'/'checks'):shutil.rmtree(self.root)
 def method(self,pid=None):
  pid=pid or self.p['id'];return catalog.create_method(pid,'Spatial equilibrium',{'problem':'trade welfare','assumptions':'free labor mobility','equations':'w=p','evidence':[{'paper_id':pid,'page':1,'quote':'spatial equilibrium model'}]})
 def tool(self):
  m=self.method();code=catalog.add_asset(self.p['id'],'author.py',b'def reference_model(x): return [x]','code');a=catalog.add_asset(self.p['id'],'input.json',b'{}','data');r=catalog.add_asset(self.p['id'],'reference.json',b'{"answer":[100.0]}','reference');t=catalog.create_tool(self.p['id'],m['id'],'Test tool',{'parameters':[{'name':'x','type':'number','min':0,'max':100,'default':100}],'asset_ids':[a['id'],r['id'],code['id']],'author_code_assets':[code['id']],'input_asset':a['id'],'reference_asset':r['id'],'validation_fields':['answer']},"def run(inputs,workspace):\n return {'answer':[inputs['x']]}\n");return catalog.review('tool',t['id'],'confirmed','Tester','Inspected fixture code',t['revision'])
 def test_three_links_duplicate_and_text(self):
  other,dup=catalog.import_paper('same.pdf',catalog.asset_path(self.p['original_asset']).read_bytes());self.assertTrue(dup);self.assertEqual(other['id'],self.p['id']);self.assertEqual(len(self.p['methods']),1);self.assertEqual(len(self.p['tools']),1);self.assertIn('labor mobility',catalog.page(self.p['id'],1)['text'])
 def test_revision_conflict(self):
  r=catalog.update('paper',self.p['id'],{'title':'Human edit'},1,'Editor');self.assertEqual(len(r['history']),2)
  with self.assertRaisesRegex(ValueError,'conflict'):catalog.update('paper',self.p['id'],{'title':'Overwrite'},1,'Other')
 def test_evidence_and_protected_fields(self):
  with self.assertRaises(ValueError):catalog.update('paper',self.p['id'],{'card':{'research_question':{'text':'x','evidence':[{'page':1,'quote':'fabricated quotation'}]}}},1,'Editor')
  with self.assertRaises(ValueError):catalog.update('paper',self.p['id'],{'review_status':'confirmed'},1,'Editor')
  self.assertEqual(catalog.check_evidence(self.p['id'],[{'page':1,'quote':'visually checked quote'}],True)[0]['verification'],'visual')
 def test_strict_threshold_zero_nonfinite(self):
  self.assertFalse(runner.compare({'x':[110.]},{'x':[100.]},['x'])['passed']);self.assertTrue(runner.compare({'x':[109.9]},{'x':[100.]},['x'])['passed']);self.assertFalse(runner.compare({'x':[0.]},{'x':[0.]},['x'])['passed']);self.assertTrue(runner.compare({'x':[.001]},{'x':[0.]},['x'],{'x':.01})['passed']);self.assertFalse(runner.compare({'x':[float('nan')]},{'x':[1.]},['x'])['passed'])
 def test_input_contract(self):
  for v in [{'x':True},{'x':float('inf')},{'x':101},{'shell':'whoami'}]:
   with self.assertRaises(ValueError):runner.parameters({'parameters':[{'name':'x','max':100}]},v)
 def test_execution_validation_and_invalidation(self):
  t=self.tool()
  with self.assertRaises(ValueError):runner.execute(t['id'],{})
  r=runner.execute(t['id'],{},True);self.assertEqual(r['status'],'passed',r);self.assertEqual(runner.execute(t['id'],{'x':90})['status'],'passed');r=catalog.update('tool',t['id'],{'code':t['code']+'\n# edited'},t['revision'],'Editor');self.assertNotEqual(r['validation_status'],'verified');self.assertEqual(r['review_status'],'draft')
 def test_asset_scope(self):
  t=self.tool();p2,_=catalog.import_paper('other.pdf',pdf('Another paper'));a=catalog.add_asset(p2['id'],'x.json',b'{}','data')
  with self.assertRaises(ValueError):catalog.update('tool',t['id'],{'spec':{**t['spec'],'input_asset':a['id']}},t['revision'],'Editor')
 def test_shared_delete(self):
  m=self.method();p2,_=catalog.import_paper('second.pdf',pdf('The spatial equilibrium model also appears here.'));catalog.create_method(p2['id'],m['name'],{**m['spec'],'evidence':[{'paper_id':p2['id'],'page':1,'quote':'spatial equilibrium model'}]});catalog.delete_paper(self.p['id']);m=catalog.get('method',m['id']);self.assertEqual([p['id'] for p in m['sources']],[p2['id']]);self.assertTrue(all(e['paper_id']==p2['id'] for e in m['spec']['evidence']))
 def test_delete_orphans_memory(self):
  s=catalog.session('paper',self.p['id']);catalog.message(s['id'],'user','Question');catalog.delete_paper(self.p['id']);self.assertEqual(catalog.stats(),{'papers':0,'methods':0,'tools':0,'verified_tools':0})
  with store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
 def test_scoped_ai_mutation(self):
  r=engine.apply_actions(self.p['id'],[{'op':'update_paper','entity_id':self.p['id'],'expected_revision':1,'payload_json':'{"review_status":"confirmed"}','reason':'invalid'}]);self.assertFalse(r[0]['applied'])
 def test_atomic_analysis_preserves_edits(self):
  revs={m['id']:m['revision'] for m in self.p['methods']};catalog.update('paper',self.p['id'],{'title':'Human edit'},1,'Editor')
  with self.assertRaises(ValueError):catalog.reconcile_extraction(self.p['id'],1,{}, {},[{'name':'Model','spec':{'evidence':[]}}],revs)
  self.assertEqual(catalog.get('paper',self.p['id'])['title'],'Human edit')
 def test_full_pdf_coverage_and_cancellation(self):
  import pdf_visual
  d=fitz.open()
  for i in range(7):d.new_page()
  p=store.DATA/'scan.pdf';d.save(p);d.close()
  pages=[item['page'] for batch in pdf_visual.batches(p) for item in batch];self.assertEqual(pages,list(range(1,8)))
  j=catalog.job('analysis',self.p['id']);engine.cancel_paper(self.p['id'])
  with self.assertRaises(ValueError):engine.gate(j['id'],self.p['id'])
 def test_restore_creates_new_revision(self):
  p=catalog.update('paper',self.p['id'],{'title':'Changed'},1,'Editor');restored=catalog.restore('paper',p['id'],1,2,'Editor');self.assertEqual(restored['title'],self.p['title']);self.assertEqual(restored['revision'],3)
 def test_solver_receives_actual_current_validation_proof(self):
  t=self.tool();run=runner.execute(t['id'],{},True);self.assertEqual(run['status'],'passed')
  def answer(jid,pid,prompt,schema,**kwargs):
   self.assertIn('validation_proof',prompt);self.assertIn(run['run_id'],prompt)
   return {'explanation':'test plan','selected_method_id':t['method_id'],'selected_tool_id':t['id'],'missing_information':[],'applicability':[],'parameters_json':'{}'}
  j=catalog.job('solve')
  with patch.object(engine,'ask',side_effect=answer):plan=engine.solve_plan(j['id'],None,'trade welfare',{})
  self.assertEqual(plan['selected_tool_id'],t['id'])
  catalog.update('tool',t['id'],{'code':t['code']+'# changed'},t['revision'],'Editor')
  with patch.object(engine,'ask',return_value={'explanation':'stale plan','selected_method_id':t['method_id'],'selected_tool_id':t['id'],'missing_information':[],'applicability':[],'parameters_json':'{}'}):plan=engine.solve_plan(j['id'],None,'trade welfare',{})
  self.assertEqual(plan['selected_tool_id'],'');self.assertTrue(plan['missing_information'])
 def test_compact_chat_and_full_transcript(self):
  session=catalog.session('paper',self.p['id']);catalog.message(session['id'],'user','用户追问：hello\n当前三个库：large context','chat');catalog.message(session['id'],'assistant','Visible answer','display');catalog.message(session['id'],'assistant','Page analysis','analysis')
  with TestClient(app) as c:
   compact=c.get('/api/papers/'+self.p['id']+'/messages').json();self.assertEqual(len(compact['messages']),2);self.assertNotIn('large context',compact['messages'][0]['text']);self.assertEqual(compact['total_count'],3)
   full=c.get('/api/papers/'+self.p['id']+'/transcript');self.assertEqual(len(full.json()['messages']),3);self.assertIn('attachment',full.headers['content-disposition'])
 def test_reanalysis_rename_keeps_reviewed_tool(self):
  t=self.tool();m=catalog.get('method',t['method_id']);p=catalog.get('paper',self.p['id']);revs={x['id']:x['revision'] for x in p['methods']}
  catalog.reconcile_extraction(p['id'],p['revision'],{}, {},[{'existing_method_id':m['id'],'name':'Renamed equilibrium method','spec':m['spec']}],revs)
  kept=catalog.get('tool',t['id']);self.assertEqual(kept['code'],t['code']);self.assertEqual(kept['method_id'],m['id']);self.assertEqual(catalog.get('method',m['id'])['name'],'Renamed equilibrium method');self.assertNotEqual(kept['validation_status'],'verified')
 def test_unmatched_reanalysis_preserves_manual_content(self):
  t=self.tool();p=catalog.get('paper',self.p['id']);revs={m['id']:m['revision'] for m in p['methods']}
  catalog.reconcile_extraction(p['id'],p['revision'],{}, {},[{'name':'Different discovered method','spec':{'problem':'different','evidence':[]}}],revs)
  self.assertEqual(catalog.get('tool',t['id'])['code'],t['code']);self.assertIn('reconciliation_note',catalog.get('method',t['method_id'])['spec'])
 def test_new_source_invalidates_previous_proof(self):
  t=self.tool();self.assertEqual(runner.execute(t['id'],{},True)['status'],'passed');m=catalog.get('method',t['method_id']);p2,_=catalog.import_paper('another.pdf',pdf('The spatial equilibrium model is also used.'))
  catalog.create_method(p2['id'],m['name'],{**m['spec'],'evidence':[{'paper_id':p2['id'],'page':1,'quote':'spatial equilibrium model'}]})
  self.assertEqual(catalog.get('tool',t['id'])['validation_status'],'stale');self.assertEqual([p['id'] for p in catalog.get('tool',t['id'])['sources']],[self.p['id']])
 def test_verified_tool_supersedes_only_untouched_same_source_candidate(self):
  t=self.tool();auto=catalog.create_tool(self.p['id'],t['method_id'],'Automatic candidate',{},actor='Codex');human=catalog.create_tool(self.p['id'],t['method_id'],'Manual candidate',{},actor='manual:editor');self.assertEqual(runner.execute(t['id'],{},True)['status'],'passed')
  with self.assertRaises(ValueError):catalog.get('tool',auto['id'])
  self.assertEqual(catalog.get('tool',human['id'])['name'],'Manual candidate')
 def test_native_resume(self):
  from codex_bridge import Bridge
  b=Bridge();calls=[]
  def rpc(method,params=None):
   calls.append((method,params));return {'thread':{'id':'persisted'}} if method=='thread/resume' else {'turn':{'id':'turn'}}
  with patch.object(b,'rpc',side_effect=rpc):b.begin('continue',thread_id='persisted',persistent=True)
  self.assertEqual(calls[0],('thread/resume',{'threadId':'persisted'}))
 def test_http_guard_and_removed_routes(self):
  with TestClient(app) as c:
   self.assertEqual(c.get('/api/bootstrap').status_code,200);self.assertEqual(c.get('/').status_code,200);self.assertEqual(c.get('/api/graph').status_code,404);self.assertEqual(c.put('/api/records/paper/'+self.p['id'],json={}).status_code,403)
   r=c.put('/api/records/paper/'+self.p['id'],headers={'X-EconLab':'1'},json={'patch':{'title':'API edit'},'revision':1,'actor':'Test'});self.assertEqual(r.status_code,200,r.text)
if __name__=='__main__':unittest.main()
