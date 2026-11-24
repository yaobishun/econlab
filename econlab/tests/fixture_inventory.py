"""Explicit complete inventories for tiny synthetic test models, never production approval."""
import catalog,store,experiments


def approve_fixture(tool):
    tool=catalog.get('tool',tool['id']);pid=tool['sources'][0]['id'];method=catalog.get('method',tool['method_id'])
    catalog.review('method',method['id'],'confirmed','Synthetic test reviewer','Checked synthetic model and exact evidence',method['revision'])
    with store.db() as c:c.execute('UPDATE papers SET coverage=? WHERE id=?',(store.encode({'completed':store.now(),'fixture':True}),pid))
    evidence=method['spec']['evidence'];spec=tool['spec']
    doc={'pages_audited':list(range(1,catalog.get('paper',pid)['page_count']+1)),'coverage':[],
         'experiments':[{'id':'fixture','name':'Complete synthetic model experiment','method_id':method['id'],'tool_id':tool['id'],'evidence':evidence,
             **{k:spec[k] for k in ('input_asset','reference_asset','validation_fields','zero_tolerance','asset_ids','bundle_paths') if k in spec},
             'units':{k:'fixture units' for k in spec['validation_fields']}}]}
    old=experiments.raw(pid);saved=experiments.save(pid,doc,old['revision'] if old else 0,'Test fixture','Declare the complete synthetic scope')
    experiments.review(pid,saved['revision'],'Synthetic test reviewer','Checked every synthetic experiment, input, reference and code')
    return experiments.execute(pid,'fixture')
