"""Import exactly one pilot. Re-run with --analyze / --register / --validate as needed."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,engine,runner,store
P=store.ROOT/'pilot'
def imported():
    paper,_=catalog.import_paper('Quantitative Spatial Economics.pdf',(P/'author'/'Quantitative Spatial Economics.pdf').read_bytes())
    provenance=json.loads((P/'provenance.json').read_text(encoding='utf-8-sig'))
    for item in provenance['author_files']:
        path=P/item['path']
        if path.suffix=='.pdf':continue
        if store.digest(path.read_bytes())!=item['sha256']:raise ValueError('Author file hash mismatch')
        catalog.add_asset(paper['id'],path.name,path.read_bytes(),'code' if path.suffix=='.m' else 'reference')
    for name,role in [('inputs.mat','data'),('reference.mat','reference'),('provenance.json','reference')]:catalog.add_asset(paper['id'],name,(P/name).read_bytes(),role)
    return catalog.get('paper',paper['id'])
def register(paper):
    method=next((m for m in paper['methods'] if '30' in m['name'] and '网格' in m['name']),None)
    if not method:
        raise ValueError('Complete PDF analysis, then select the numerical experiment method')
    methods=catalog.get('method',method['id']);assets={a['name']:a for a in paper['assets']}
    fns=['Hlandprice.m','Hpindex.m','Hrealw.m','Hwelfaregains.m','solveHLwCtyOpen.m','solveHLwCtyOpen_E.m']
    ids=[assets[n]['id'] for n in fns+['Helpman_E.m','Grid30.mat','inputs.mat','reference.mat','provenance.json']]
    paths={assets[n]['id']:'author/functions/'+n for n in fns}
    paths[assets['Helpman_E.m']['id']]='author/Helpman_E.m'
    spec={'source_kind':'author_adapter','entrypoint':'econlab_qse','asset_ids':ids,'author_code_assets':[assets[n]['id'] for n in fns+['Helpman_E.m']],'bundle_paths':paths,'input_asset':assets['inputs.mat']['id'],'reference_asset':assets['reference.mat']['id'],
        'parameters':[{'name':'country_reduction','type':'number','min':0,'max':100,'default':100,'description':'跨国边界额外摩擦降低比例','unit':'%'},{'name':'location_reduction','type':'number','min':0,'max':100,'default':0,'description':'各地点边界额外摩擦降低比例','unit':'%'}],
        'validation_fields':json.loads((P/'provenance.json').read_text(encoding='utf-8-sig'))['reference_fields'],
        'validation_scope':'2017 Quantitative Spatial Economics · §3.9作者30×30网格实验：基准、取消国界摩擦、取消地点边界摩擦；对照作者Grid30.mat中15组×900个数值。非整篇综述全部模型的复现。',
        'provenance_note':'原始求解函数保持作者版本；适配层只载入作者输入、调用原函数和导出结果。作者代码距离指数0.33，论文正文为0.375；此处明确验证作者代码包版本，未静默改写原模型。输入与参考输出分别从作者Grid30.mat按字段拆分。','timeout_seconds':1800}
    old=next((t for t in paper['tools'] if t['spec'].get('source_kind')=='author_adapter'),None)
    if old:
        if old['method_id']!=method['id']:
            old=catalog.update('tool',old['id'],{'method_id':method['id']},old['revision'],'bootstrap','Bind author experiment to the specific 30x30 counterfactual method')
            old=catalog.review('tool',old['id'],'confirmed','开发验证：来源映射核对','核对工具属于同一论文的30×30网格实验，代码与输入未改，数值验证按新方法映射重新执行。',old['revision'])
        return catalog.get('tool',old['id'])
    tool=catalog.create_tool(paper['id'],method['id'],'QSE 2017 · 作者空间均衡与边界实验',spec,(P/'econlab_qse.m').read_text(encoding='utf-8-sig'),'matlab','bootstrap')
    # This is a traceable engineering inspection, not an expert economic-method endorsement.
    return catalog.review('tool',tool['id'],'confirmed','开发验证：原始代码核对','核对provenance文件哈希及未改动的6个作者函数；适配层仅装载输入并调用原函数，不读取参考输出参与求解。数值基准由独立验证运行判定。',tool['revision'])
def main():
    a=argparse.ArgumentParser();a.add_argument('--analyze',action='store_true');a.add_argument('--resume',action='store_true');a.add_argument('--register',action='store_true');a.add_argument('--validate',action='store_true');args=a.parse_args()
    catalog.init();paper=imported();print('PAPER',paper['id'],'PAGES',paper['page_count'],flush=True)
    if args.analyze or args.resume:
        job=catalog.job('analysis',paper['id']);catalog.job_update(job['id'],status='running')
        try:
            result=engine.analyze(job['id'],paper['id'],resume=args.resume);catalog.job_update(job['id'],status='completed',stage='完整PDF分析完成',progress={'result':result});print(json.dumps(result),flush=True)
        except Exception as e:catalog.job_update(job['id'],status='failed',error=str(e));raise
        finally:engine.bridge.close()
        paper=catalog.get('paper',paper['id'])
    if args.register or args.validate:
        tool=register(paper);print('TOOL',tool['id'],flush=True)
        if args.validate:print(json.dumps(runner.execute(tool['id'],{},True,paper['id']),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
