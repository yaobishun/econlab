"""Reports reflect actual runs without changing scientific or validation state."""
import copy,io,json,sys,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_paper_workflow as fixtures
import catalog,engine,experiments,reproduction_report as reports,runner,store
from fastapi.testclient import TestClient
from app import app


class ReproductionReport(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PaperWorkflow();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.save()
        self.pid=self.f.pid

    def doc(self):
        return {'models':[{'method_id':self.f.m['id'],'actors':'Households and firms',
          'assumptions':[{'statement':'Fixed input','reason':'Controlled test','origin':'paper','evidence':[{'page':1,'quote':'counterfactual experiment evidence','asset_id':''}]}],
          'derivations':[{'title':'Equilibrium mapping','objective':'Use actual baseline','constraints':'Input identity',
            'steps':[{'formula':'y=b f','explanation':'Apply the policy factor'}],'equilibrium_conditions':['y=b f'],
            'code_links':[{'tool_id':self.f.t['id'],'symbol':'y','location':'tool.py:run'}],'origin':'ai_derivation','evidence':[]}]}],
          'inputs':[{'method_id':self.f.m['id'],'experiment_id':'baseline','name':'base','value':'100','unit':'units','origin':'data',
            'asset_ids':[self.f.input['id']],'evidence':[],'note':'Actual input.json'}],
          'experiments':[{'experiment_id':'baseline','baseline':'factor=1','policy_changes':'None','output_meaning':'Level, not a change','conclusion':'Await actual computation','evidence':[]}],
          'issues':[{'experiment_id':'','stage':'report','issue':'Review the interpretation','evidence':[],'impact':'No change in computed values','resolution':'','revalidation':'Not yet reviewed','status':'open'}]}

    def save(self,doc=None):
        previous=reports.raw(self.pid)
        return reports.save(self.pid,doc or self.doc(),previous['revision'],reports.basis(self.pid),'Test editor','Explain actual fixture')

    def test_read_only_report_exposes_actual_result_not_a_claimed_pass(self):
        self.f.approve();run=experiments.execute(self.pid,'baseline');self.assertEqual(run['status'],'passed')
        before=experiments.summary(self.pid);r=reports.collect(self.pid)
        self.assertFalse(r['summary']['eligible']);self.assertEqual(r['summary']['counts']['passed'],1)
        e=next(e for e in r['experiments'] if e['id']=='baseline')
        self.assertEqual(e['run_id'],run['run_id']);self.assertEqual(e['run_status'],'passed')
        rows=reports.result_rows(self.pid,'baseline','y')
        self.assertEqual(rows['rows'][0]['actual'],100);self.assertEqual(rows['rows'][0]['reference'],100)
        self.assertEqual(rows['rows'][0]['difference'],0)
        self.assertEqual(before['counts'],experiments.summary(self.pid)['counts'])

    def test_manual_report_edit_and_final_review_do_not_grant_validation(self):
        oldtool=catalog.get('tool',self.f.t['id']);before=experiments.summary(self.pid)
        r=self.save();stamp=reports.basis(self.pid)
        reports.review(self.pid,r['revision'],stamp,'accepted','Reviewer','Accept explanation, numeric work still pending')
        after=reports.collect(self.pid)
        self.assertTrue(after['final_reviews'][0]['current']);self.assertFalse(after['summary']['eligible'])
        self.assertEqual(catalog.fingerprint(oldtool),catalog.fingerprint(catalog.get('tool',oldtool['id'])))
        self.assertEqual(before['counts'],after['summary']['counts'])
        with store.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM experiment_approvals').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)

    def test_sources_and_report_revisions_revoke_only_report_review(self):
        r=self.save();stamp=reports.basis(self.pid);reports.review(self.pid,1,stamp,'accepted','Reviewer','Check explanation')
        with self.assertRaisesRegex(ValueError,'conflict'):reports.save(self.pid,self.doc(),0,stamp,'Other','Stale edit')
        self.save();self.assertFalse(reports.collect(self.pid)['final_reviews'][0]['current'])
        t=catalog.get('tool',self.f.t['id']);catalog.update('tool',t['id'],{'code':t['code']+'\n# version change'},t['revision'],'Editor','Real code change')
        r=reports.collect(self.pid);self.assertTrue(r['supplement_stale'])
        with self.assertRaisesRegex(ValueError,'conflict'):reports.review(self.pid,2,stamp,'accepted','Reviewer','Stale source')
        with self.assertRaisesRegex(ValueError,'conflict'):reports.save(self.pid,self.doc(),2,stamp,'Reviewer','Stale source')

    def test_cross_paper_mapping_fake_evidence_and_status_writes_are_rejected(self):
        import fitz
        p=fitz.open();p.new_page().insert_text((50,50),'Unrelated paper evidence.');other=catalog.import_paper('other.pdf',p.tobytes())[0];p.close()
        a=catalog.add_asset(other['id'],'other.json',b'{}','data')
        d=self.doc();d['inputs'][0]['asset_ids']=[a['id']]
        with self.assertRaisesRegex(ValueError,'outside'):self.save(d)
        d=self.doc();d['models'][0]['assumptions'][0]['evidence'][0]['page']=99
        with self.assertRaises(ValueError):self.save(d)
        d=self.doc();d['models'][0]['assumptions'][0]['evidence']=[]
        with self.assertRaisesRegex(ValueError,'evidence'):self.save(d)
        d=self.doc();d['experiments'][0]['passed']=True
        with self.assertRaisesRegex(ValueError,'schema'):self.save(d)

    def test_absent_run_never_falls_back_to_an_old_output(self):
        with self.assertRaisesRegex(ValueError,'No actual run'):reports.result_rows(self.pid,'baseline','y')
        self.f.approve();experiments.execute(self.pid,'baseline')
        t=catalog.get('tool',self.f.t['id']);catalog.update('tool',t['id'],{'code':t['code']+'\n# changed'},t['revision'],'Editor','Actual change')
        with self.assertRaisesRegex(ValueError,'No actual run'):reports.result_rows(self.pid,'baseline','y')

    def test_publication_precision_is_visible_and_raw_error_is_preserved(self):
        e=self.f.doc['experiments'][0]
        a=catalog.add_asset(self.pid,'tiny-input.json',b'{"base":0.0442859}','data');ref=catalog.add_asset(self.pid,'tiny-ref.json',b'{"y":0.04}','reference')
        e.update(input_asset=a['id'],reference_asset=ref['id'],bundle_paths={a['id']:'input.json'},publication_precision={
          'rules':[{'field':'y','index':0,'decimals':2}],'authorized_by':'Test user','reason':'Accept published rounding','evidence':self.f.refs})
        self.f.save();self.f.approve();run=experiments.execute(self.pid,'baseline');self.assertEqual(run['status'],'passed',run)
        row=reports.result_rows(self.pid,'baseline','y')['rows'][0]
        self.assertTrue(row['passed']);self.assertTrue(row['precision_accepted']);self.assertFalse(row['strict_passed']);self.assertGreater(row['relative_error'],.1)

    def test_replay_package_contains_frozen_input_and_no_reference_file(self):
        self.f.approve();run=experiments.execute(self.pid,'baseline');root=store.DATA/'runs'/run['run_id']
        (root/'auth.json').write_text('do not export',encoding='utf-8')
        with zipfile.ZipFile(io.BytesIO(reports.replay_package(self.pid,run['run_id']))) as z:
            self.assertIn('input.json',z.namelist());self.assertIn('tool.py',z.namelist());self.assertIn('RUNNING.txt',z.namelist());self.assertIn('replay.py',z.namelist())
            self.assertNotIn('ref1.json',z.namelist());self.assertNotIn('auth.json',z.namelist())
        with self.assertRaises(ValueError):reports.replay_package('another-paper',run['run_id'])

    def test_ai_report_has_no_execution_tools_and_source_changes_abort_commit(self):
        before=reports.basis(self.pid)
        def ask(jid,pid,prompt,schema,**kw):
            self.assertNotIn('task',kw);self.assertEqual(kw['kind'],'reproduction_report')
            self.assertIn('不重跑',prompt)
            return self.doc()
        with patch.object(engine,'ask',side_effect=ask),patch.object(runner,'execute') as execute:
            result=reports.generate(self.f.j['id'],self.pid,0,before)
        execute.assert_not_called();self.assertEqual(result['revision'],1)
        def conflicting(*args,**kwargs):
            p=catalog.get('paper',self.pid);catalog.update('paper',p['id'],{'title':'Changed source'},p['revision'],'Editor','Source changed')
            return self.doc()
        with patch.object(engine,'ask',side_effect=conflicting):
            with self.assertRaisesRegex(ValueError,'conflict'):reports.generate(self.f.j['id'],self.pid,1,before)
        self.assertEqual(reports.raw(self.pid)['revision'],1)

    def test_api_report_export_pagination_and_schema_guard(self):
        self.f.approve();experiments.execute(self.pid,'baseline');client=TestClient(app)
        r=client.get('/api/papers/'+self.pid+'/report');self.assertEqual(r.status_code,200)
        body={'document':self.doc(),'revision':0,'basis':r.json()['basis'],'actor':'Editor','reason':'Checked description'}
        self.assertEqual(client.put('/api/papers/'+self.pid+'/report',json=body,headers={'X-EconLab':'1'}).status_code,200)
        exported=client.get('/api/papers/'+self.pid+'/report/export');self.assertEqual(exported.status_code,200)
        case=next(v for v in exported.json()['experiments'] if v['id']=='baseline')
        self.assertEqual(case['numeric_results']['y'],{'actual':100,'reference':100,'unit':'units'})
        csv=client.get('/api/papers/'+self.pid+'/report/results.csv',params={'experiment_id':'baseline','field':'y'})
        self.assertEqual(csv.status_code,200);self.assertIn('absolute_error',csv.text)
        page=client.get('/api/papers/'+self.pid+'/report/results',params={'experiment_id':'baseline','field':'y','offset':1,'limit':50})
        self.assertEqual(page.json()['rows'],[])
        self.assertEqual(client.get('/api/papers/'+self.pid+'/report/results',params={'experiment_id':'baseline','field':'y','limit':1000}).status_code,400)

    def test_delete_cascades_report_history_and_reviews(self):
        r=self.save();reports.review(self.pid,1,reports.basis(self.pid),'pending','Reviewer','Continue checks')
        catalog.delete_paper(self.pid)
        with store.db() as c:
            for table in ('reproduction_reports','reproduction_report_history','reproduction_report_reviews'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_csv_escapes_formula_labels_and_preserves_numeric_precision(self):
        self.f.approve();run=experiments.execute(self.pid,'baseline')
        with store.db() as c:
            r=store.decode(c.execute('SELECT result FROM runs WHERE id=?',(run['run_id'],)).fetchone()[0])
            r['outputs']['labels']={'y':['=1+1']}
            c.execute('UPDATE runs SET result=? WHERE id=?',(store.encode(r),run['run_id']))
        csv=reports.result_csv(self.pid,'baseline','y')
        self.assertIn("'=1+1",csv);self.assertIn('100.0',csv)
        row=reports.result_rows(self.pid,'baseline','y')['rows'][0]
        self.assertEqual(row['actual_to_reference_ratio'],1)


if __name__=='__main__':unittest.main()
