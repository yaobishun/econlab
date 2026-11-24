"""Actual subprocess acceptance for strict paper inventories and persistent controllers."""
import copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import fitz
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,store,experiments,workflow,engine,runner,execution_agent


class PaperWorkflow(unittest.TestCase):
    def setUp(self):
        self.previous=store.DATA;self.root=store.ROOT/'.local'/'checks'/('paper-'+store.uid());store.DATA=self.root
        self.addCleanup(self.cleanup)
        catalog.init();store.set_setting("reproduction_mode","manual");pdf=fitz.open();pdf.new_page().insert_text((40,50),'Table 1 baseline and Table 2 counterfactual experiment evidence.');content=pdf.tobytes();pdf.close()
        self.p=catalog.import_paper('synthetic.pdf',content)[0];self.pid=self.p['id'];self.refs=[{'page':1,'quote':'counterfactual experiment evidence'}]
        self.m=catalog.create_method(self.pid,'Shared synthetic model',{'problem':'Compute a controlled equilibrium','assumptions':'Synthetic fixed input','evidence':self.refs})
        catalog.review('method',self.m['id'],'confirmed','Test reviewer','Checked model',self.m['revision'])
        self.input=catalog.add_asset(self.pid,'input.json',b'{"base":100}','data')
        self.ref1=catalog.add_asset(self.pid,'ref1.json',b'{"y":100}','reference');self.ref2=catalog.add_asset(self.pid,'ref2.json',b'{"y":200}','reference')
        self.code="import json\ndef run(inputs,workspace):\n base=json.loads((workspace/'input.json').read_text())['base']\n dep=workspace/'calibration.json'\n if dep.exists(): base=json.loads(dep.read_text())['y']\n return {'y':base*inputs['factor']}\n"
        a=catalog.add_asset(self.pid,'solver.py',self.code.encode(),'code')
        self.t=catalog.create_tool(self.pid,self.m['id'],'Common solver',{'parameters':[{'name':'factor','type':'number','default':1}],
            'code_assets':[a['id']],'asset_ids':[self.input['id'],a['id']],'input_asset':self.input['id'],'reference_asset':self.ref1['id'],'validation_fields':['y']},self.code)
        catalog.review('tool',self.t['id'],'confirmed','Test reviewer','Checked actual fixture solver and inputs',self.t['revision'])
        with store.db() as c:c.execute('UPDATE papers SET coverage=? WHERE id=?',(store.encode({'completed':store.now(),'mode':'synthetic_fixture'}),self.pid))
        common={'method_id':self.m['id'],'tool_id':self.t['id'],'kind':'numeric','evidence':self.refs,'input_asset':self.input['id'],'validation_fields':['y'],'units':{'y':'units'}}
        self.doc={'pages_audited':[1],'experiments':[
            {**common,'id':'baseline','name':'Baseline calibration','reference_asset':self.ref1['id'],'parameters':{'factor':1},'figures':['Table 1']},
            {**common,'id':'counterfactual','name':'Counterfactual','reference_asset':self.ref2['id'],'parameters':{'factor':2},'figures':['Table 2'],'dependencies':['baseline'],'dependency_bindings':[{'experiment_id':'baseline','path':'calibration.json'}]}],
            'coverage':[{'label':'Table 1','page':1,'experiment_ids':['baseline'],'evidence':self.refs},{'label':'Table 2','page':1,'experiment_ids':['counterfactual'],'evidence':self.refs}]}
        self.j=catalog.job('paper_workflow',self.pid)

    def cleanup(self):
        store.DATA=self.previous
        if self.root.resolve().is_relative_to(store.ROOT/'.local'/'checks'):shutil.rmtree(self.root,ignore_errors=True)

    def save(self,doc=None):
        previous=experiments.raw(self.pid)
        return experiments.save(self.pid,doc or self.doc,previous['revision'] if previous else 0,'Test editor','Complete synthetic inventory')

    def approve(self):
        p=experiments.raw(self.pid);return experiments.review(self.pid,p['revision'],'Test reviewer','Checked all pages, all figures, code, data, reference units and scope')

    def test_publication_precision_revalidates_without_overwriting_failed_run(self):
        source=catalog.add_asset(self.pid,'rounding-input.json',b'{"base":0.0442859}','data')
        reference=catalog.add_asset(self.pid,'rounding-reference.json',b'{"y":0.04}','reference')
        e=self.doc['experiments'][0]
        e.update(input_asset=source['id'],reference_asset=reference['id'],bundle_paths={source['id']:'input.json'})
        self.save();self.approve()
        first=experiments.execute(self.pid,'baseline');self.assertEqual(first['status'],'failed',first)
        plan=experiments.raw(self.pid);e=plan['document']['experiments'][0]
        e['publication_precision']={'rules':[{'field':'y','index':0,'decimals':2}],
            'authorized_by':'Test user','reason':'Accept the published two-decimal rounding interval','evidence':self.refs}
        self.save(plan['document'])
        with self.assertRaises(ValueError):experiments.execute(self.pid,'baseline')
        self.approve();second=experiments.execute(self.pid,'baseline')
        self.assertEqual(second['status'],'passed',second)
        self.assertFalse(second['comparison']['strict_passed'])
        self.assertGreater(second['comparison']['metrics'][0]['max_relative_error'],.10)
        self.assertEqual(second['validation_policy']['publication_precision']['rules'][0]['decimals'],2)
        with store.db() as c:self.assertEqual(c.execute('SELECT status FROM runs WHERE id=?',(first['run_id'],)).fetchone()[0],'failed')
        self.assertFalse(experiments.summary(self.pid)['eligible'],'Other required experiments still gate method use')
        plan=experiments.raw(self.pid);plan['document']['experiments'][0]['publication_precision']['rules'][0]['decimals']=3
        self.save(plan['document'])
        self.assertNotEqual(experiments.summary(self.pid)['experiments'][0]['status'],'passed')

    def test_dependency_batch_and_strict_solve_gate(self):
        self.save();self.approve()
        with self.assertRaises(ValueError):runner.execute(self.t['id'],{})
        first=experiments.execute(self.pid,'baseline');self.assertEqual(first['status'],'passed',first)
        self.assertFalse(experiments.summary(self.pid)['eligible'])
        with self.assertRaises(ValueError):runner.execute(self.t['id'],{})
        result=workflow.run(self.j['id'],self.pid)
        self.assertEqual(result['status'],'complete',result)
        s=experiments.summary(self.pid);self.assertTrue(s['eligible']);self.assertEqual(s['counts']['passed'],2)
        self.assertEqual(runner.execute(self.t['id'],{'factor':3})['outputs']['y'],300)
        self.assertEqual(len(experiments.rows(self.pid)),2,'Resume must not rerun the baseline')
        with store.db() as c:
            run=c.execute('SELECT result FROM runs WHERE id=?',(s['experiments'][1]['run_id'],)).fetchone()
        self.assertEqual(store.decode(run[0])['experiment']['dependencies'][0]['run_id'],first['run_id'])

    def test_single_old_tool_validation_cannot_unlock_paper(self):
        self.assertEqual(runner.execute(self.t['id'],{},True)['status'],'passed')
        with self.assertRaisesRegex(ValueError,'整篇'):runner.execute(self.t['id'],{})
        self.save();self.assertFalse(experiments.summary(self.pid)['eligible'])

    def test_missing_reference_and_omitted_figure_are_not_success(self):
        doc=copy.deepcopy(self.doc);doc['experiments'][1].pop('reference_asset');self.save(doc)
        s=experiments.summary(self.pid);self.assertEqual(s['counts']['missing_materials'],1)
        with self.assertRaises(ValueError):self.approve()
        doc['coverage'].pop()
        with self.assertRaisesRegex(ValueError,'coverage mapping'):self.save(doc)

    def test_code_change_and_plan_revision_revoke_proof(self):
        self.save();self.approve();workflow.run(self.j['id'],self.pid);self.assertTrue(experiments.summary(self.pid)['eligible'])
        changed=catalog.update('tool',self.t['id'],{'code':self.code+'\n# corrected'},self.t['revision'],'Editor','Code change')
        s=experiments.summary(self.pid);self.assertFalse(s['eligible']);self.assertFalse(s['approved']);self.assertEqual(s['experiments'][0]['status'],'stale')
        with self.assertRaises(ValueError):runner.execute(self.t['id'],{})
        catalog.review('tool',changed['id'],'confirmed','Test reviewer','Rechecked code',changed['revision']);self.approve()
        workflow.run(self.j['id'],self.pid);self.assertTrue(experiments.summary(self.pid)['eligible'])
        self.save();self.assertFalse(experiments.summary(self.pid)['approved']);self.assertFalse(experiments.summary(self.pid)['eligible'])

    def test_cycles_foreign_assets_and_host_owned_status_rejected(self):
        doc=copy.deepcopy(self.doc);doc['experiments'][0]['dependencies']=['counterfactual']
        with self.assertRaisesRegex(ValueError,'cycle'):self.save(doc)
        doc=copy.deepcopy(self.doc);doc['experiments'][0]['status']='passed'
        with self.assertRaisesRegex(ValueError,'host-owned'):self.save(doc)
        doc=copy.deepcopy(self.doc);doc['experiments'][0]['reference_asset']='foreign'
        with self.assertRaisesRegex(ValueError,'outside'):self.save(doc)
        doc=copy.deepcopy(self.doc);doc['experiments'][1]['required']=False
        with self.assertRaisesRegex(ValueError,'reason'):self.save(doc)

    def test_restart_and_stop_keep_completed_experiment(self):
        self.save();self.approve();first=experiments.execute(self.pid,'baseline')
        workflow.checkpoint(self.j['id'],self.pid,'running','Pending counterfactual')
        engine.recover();self.assertEqual(workflow.state(self.pid)['status'],'interrupted')
        self.assertEqual(experiments.summary(self.pid)['experiments'][0]['run_id'],first['run_id'])
        engine.stop_paper(self.pid)
        with self.assertRaises(ValueError):engine.gate(self.j['id'],self.pid)
        self.j=catalog.job('resume_workflow',self.pid);self.assertEqual(workflow.run(self.j['id'],self.pid)['status'],'complete')

    def test_numeric_failure_and_qualitative_cannot_fake_completion(self):
        doc=copy.deepcopy(self.doc);doc['experiments'][1]['parameters']['factor']=3;self.save(doc);self.approve();experiments.execute(self.pid,'baseline')
        r=experiments.execute(self.pid,'counterfactual');self.assertEqual(r['status'],'failed');self.assertFalse(experiments.summary(self.pid)['eligible'])
        doc=copy.deepcopy(self.doc);doc['experiments'][1]['kind']='qualitative';self.save(doc);self.approve();experiments.execute(self.pid,'baseline')
        self.assertEqual(experiments.summary(self.pid)['experiments'][1]['status'],'qualitative_review_required');self.assertFalse(experiments.summary(self.pid)['eligible'])

    def test_inventory_review_is_versioned_and_cascades_on_delete(self):
        saved=self.save();self.approve();experiments.remember(self.pid,{'user_goal':'Test scope'},event={'kind':'human_decision','reason':'test'})
        memory=experiments.memory(self.pid);self.assertEqual(memory['inventory_revision'],saved['revision']);self.assertEqual(memory['user_goal'],'Test scope')
        with self.assertRaisesRegex(ValueError,'conflict'):experiments.save(self.pid,self.doc,0,'Editor','stale edit')
        catalog.delete_paper(self.pid)
        with store.db() as c:
            for table in ('experiment_plans','experiment_plan_history','experiment_approvals','experiment_attempts','paper_project_memory','paper_controllers'):self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_curve_shape_is_checked_separately_from_numeric_tolerance(self):
        checks=[{'type':'monotonic','field':'y','direction':'increasing'},{'type':'extremum','field':'y','direction':'max','index':2},{'type':'coverage','field':'y','x_field':'x','min':0,'max':2}]
        self.assertTrue(experiments.curve_compare({'y':[1,2,3],'x':[0,1,2]},checks)['passed'])
        self.assertFalse(experiments.curve_compare({'y':[1,3,2],'x':[0,1,2]},checks)['passed'])
        piecewise=[{'type':'monotonic','field':'y','direction':'increasing','start':0,'stop':2},{'type':'monotonic','field':'y','direction':'decreasing','start':1,'stop':3},{'type':'extremum','field':'y','direction':'max','index':1},checks[-1]]
        self.assertTrue(experiments.curve_compare({'y':[1,3,2],'x':[0,1,2]},piecewise)['passed'])

    def test_qualitative_check_is_recorded_without_numeric_pass(self):
        doc=copy.deepcopy(self.doc);doc['experiments'][1]['kind']='qualitative';self.save(doc);self.approve()
        experiments.execute(self.pid,'baseline')
        p=experiments.raw(self.pid)
        s=experiments.check_qualitative(self.pid,'counterfactual',p['revision'],'Reviewer','Checked the synthetic diagram against original evidence')
        self.assertTrue(s['eligible']);self.assertEqual(s['counts']['passed'],1);self.assertEqual(s['counts']['qualitative_checked'],1)
        self.save(doc);self.assertFalse(experiments.summary(self.pid)['eligible'])

    def test_actual_data_replacement_does_not_stage_default_case_input(self):
        doc=copy.deepcopy(self.doc)
        data=catalog.add_asset(self.pid,'input.json',b'{"base":50}','data')
        doc['experiments'][1].update(input_asset=data['id'],parameters={'factor':4},dependencies=[],dependency_bindings=[])
        self.save(doc);self.approve();experiments.execute(self.pid,'baseline')
        result=experiments.execute(self.pid,'counterfactual')
        self.assertEqual(result['status'],'passed',result)

    def proposal(self):
        proposal=copy.deepcopy(self.doc);proposal['notes']='Complete synthetic inventory'
        for e in proposal['experiments']:
            e['description']='Synthetic experiment';e.setdefault('dependencies',[])
            keys={'id','name','method_id','kind','description','figures','dependencies','evidence'}
            config={k:e.pop(k) for k in list(e) if k not in keys};e['configuration_json']=store.encode(config)
        return proposal

    def test_planner_repairs_actual_contract_errors_and_keeps_all_tables(self):
        good=self.proposal();bad=copy.deepcopy(good)
        bad['experiments'][0]['configuration_json']='{"unexpected_field":true}'
        with patch.object(engine,'ask',side_effect=[good,bad,good]) as ask:result=workflow.plan(self.j['id'],self.pid)
        self.assertEqual(result['revision'],1);self.assertEqual(len(result['document']['experiments']),2)
        self.assertIn('Unknown experiment configuration fields',ask.call_args.args[2])

    def test_inventory_resume_preserves_completed_page_audit(self):
        good=self.proposal()
        with patch.object(engine,'ask',side_effect=[good,ValueError('quota exceeded')]):
            with self.assertRaisesRegex(ValueError,'quota'):workflow.plan(self.j['id'],self.pid)
        with patch.object(engine,'ask',return_value=good) as ask:workflow.plan(self.j['id'],self.pid)
        self.assertEqual(ask.call_count,1,'Completed page audits must not be read again on resume')

    def test_new_appendix_revokes_whole_paper_eligibility(self):
        self.save();self.approve();workflow.run(self.j['id'],self.pid);self.assertTrue(experiments.summary(self.pid)['eligible'])
        pdf=fitz.open();pdf.new_page().insert_text((30,40),'Table A1 contains an additional robustness experiment.');data=pdf.tobytes();pdf.close()
        a=catalog.add_asset(self.pid,'appendix.pdf',data,'appendix')
        self.assertFalse(experiments.summary(self.pid)['eligible'])
        refs=catalog.check_evidence(self.pid,[{'asset_id':a['id'],'page':1,'quote':'additional robustness experiment'}])
        self.assertEqual(refs[0]['asset_id'],a['id']);self.assertEqual(refs[0]['verification'],'text')




    def test_legacy_dependency_notes_preserved_without_inventing_files(self):
        import manifest
        proposal=self.proposal();cfg=store.decode(proposal['experiments'][1]['configuration_json'])
        cfg['dependency_bindings']=['baseline provides calibrated input']
        proposal['experiments'][1]['configuration_json']=store.encode(cfg)
        doc=manifest.document(proposal);case=doc['experiments'][1]
        self.assertEqual(case['dependency_notes'],['baseline provides calibrated input'])
        self.assertEqual(case['dependency_bindings'],[])
        self.assertEqual(case['dependencies'],['baseline'])
        self.save(doc);self.assertFalse(experiments.summary(self.pid)['eligible'])

    def test_partial_inventory_is_visible_and_cannot_unlock_execution(self):
        experiments.save_draft(self.pid,'invalid',self.proposal(),['contract error'])
        result=experiments.public_summary(self.pid)
        self.assertFalse(result['eligible']);self.assertEqual(result['revision'],0)
        self.assertEqual(result['draft']['candidate_count'],2)
        self.assertEqual(result['draft']['errors'],['contract error'])
        with self.assertRaises(ValueError):runner.execute(self.t['id'],{})

    def test_resume_repairs_coverage_only_and_preserves_scientific_entries(self):
        import manifest
        proposal=self.proposal();proposal['coverage'][0]['label']='Table 1 baseline discussion'
        with patch.object(engine,'ask',side_effect=[proposal,proposal,{'coverage':[proposal['coverage'][0],*self.doc['coverage']]}]):
            saved=workflow.plan(self.j['id'],self.pid)
        self.assertEqual(len(saved['document']['experiments']),2)
        self.assertEqual(saved['document']['experiments'][0]['parameters'],{'factor':1})
        self.assertEqual(len(saved['document']['coverage']),3)

    def test_typed_configuration_retains_reported_results_separately(self):
        import manifest
        proposal=self.proposal();row=proposal['experiments'][0]
        cfg=store.decode(row.pop('configuration_json'))
        for key in ('tool_id','input_asset','reference_asset'):cfg.setdefault(key,'')
        for key in ('asset_ids','validation_fields','gaps','dependency_bindings','curve_checks'):cfg.setdefault(key,[])
        for key,entry,field in [('parameters','name','value'),('units','field','unit'),('zero_tolerance','field','value'),('bundle_paths','asset_id','paths')]:
            cfg[key]=[{entry:k,field:v if key!='bundle_paths' or isinstance(v,list) else [v]} for k,v in cfg.get(key,{}).items()]
        row['configuration']=cfg;row['observations']=['Reported y = 100 units'];row['dependency_notes']=[]
        doc=manifest.document(proposal)
        self.assertEqual(doc['experiments'][0]['observations'],['Reported y = 100 units'])
        self.assertEqual(doc['experiments'][0]['parameters'],{'factor':1})
        self.assertEqual(doc['experiments'][0]['validation_fields'],['y'])

    def test_legacy_interface_migration_keeps_design_and_exact_targets(self):
        self.save();legacy=experiments.raw(self.pid)
        first=legacy['document']['experiments'][0]
        first['parameters']={'factor':1,'years':[1993,2005],'shock':'Only recorded member tariffs change'}
        first['units']={'reported_series':'units'}
        with store.db() as c:c.execute('UPDATE experiment_plans SET document=? WHERE paper_id=?',(store.encode(legacy['document']),self.pid))
        self.assertTrue(experiments.separate_legacy_design(self.pid))
        result=experiments.raw(self.pid)['document']['experiments'][0]
        self.assertEqual(result['parameters'],{'factor':1})
        self.assertEqual(result['design'],{'years':[1993,2005],'shock':'Only recorded member tariffs change'})
        self.assertEqual(result['unit_notes'],{'reported_series':'units'});self.assertEqual(result['units'],{})
        self.assertEqual(result['reference_asset'],self.ref1['id']);self.assertEqual(result['validation_fields'],['y'])
        self.assertFalse(experiments.separate_legacy_design(self.pid),'Migration must be idempotent')

    def test_scope_patch_preserves_targets_and_records_comparison_relation(self):
        doc=copy.deepcopy(self.doc);doc['experiments'][1]['dependency_bindings']=[];self.save(doc)
        plan=experiments.raw(self.pid)
        update={'id':'counterfactual','description':'Uses supplied baseline; comparison remains recorded','design_json':'{}','additional_validation_fields':[],
            'dependencies':[],'related_experiments':[{'experiment_id':'baseline','relationship':'shared_procedure','reason':'Uses the same algorithm, not the previous run output'}],
            'dependency_notes':[],'observations':[],'evidence':self.refs}
        patch_doc={'updates':[update],'new_experiments':[],'coverage_updates':[],'notes':'All original figures and targets retained'}
        changed=workflow.apply_scope_patch(self.pid,plan,patch_doc)
        e=changed['experiments'][1];self.assertEqual(e['dependencies'],[]);self.assertEqual(e['related_experiments'][0]['experiment_id'],'baseline')
        self.assertEqual(e['reference_asset'],self.ref2['id']);self.assertEqual(e['validation_fields'],['y']);self.assertEqual(len(changed['experiments']),2)
        update['units_additions']=[{'field':'y','unit':'units'}];update['gaps_additions']=['Need source audit of comparison mapping']
        extended=workflow.apply_scope_patch(self.pid,plan,patch_doc)['experiments'][1]
        self.assertEqual(extended['units'],{'y':'units'});self.assertIn('Need source audit of comparison mapping',extended['gaps'])
        update['units_additions']=[{'field':'y','unit':'percent'}]
        with self.assertRaisesRegex(ValueError,'Keep existing units'):workflow.apply_scope_patch(self.pid,plan,patch_doc)
        update['units_additions']=[]
        update['related_experiments']=[]
        with self.assertRaisesRegex(ValueError,'discard'):workflow.apply_scope_patch(self.pid,plan,patch_doc)
        update['related_experiments']=[{'experiment_id':'baseline','relationship':'comparison','reason':'Retain previous relationship'}]
        update['additional_validation_fields']=['invented_new_reference']
        with self.assertRaisesRegex(ValueError,'independent reference'):workflow.apply_scope_patch(self.pid,plan,patch_doc)

if __name__=='__main__':unittest.main()
