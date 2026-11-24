"""Typed experiment interchange; research observations are not runtime bindings.

Mappings use typed entries in model output so Structured Outputs can enforce their
shape. Legacy proposals remain readable without guessing filenames or losing text.
"""
import copy,json

S={'type':'string'};N={'type':'number'};I={'type':'integer'}
def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
def arr(p):return {'type':'array','items':p}
REF=obj({'page':I,'quote':S,'asset_id':S})
CONFIG=obj({'tool_id':S,'input_asset':S,'reference_asset':S,'asset_ids':arr(S),
    'bundle_paths':arr(obj({'asset_id':S,'paths':arr(S)})),
    'parameters':arr(obj({'name':S,'value':{'anyOf':[S,N,{'type':'boolean'}]}})),
    'validation_fields':arr(S),'zero_tolerance':arr(obj({'field':S,'value':N})),
    'units':arr(obj({'field':S,'unit':S})),
    'curve_checks':arr(obj({'field':S,'type':{'type':'string','enum':['monotonic','extremum','coverage']},'direction':S,'x_field':S,'minimum':N,'maximum':N,'index':I,'index_tolerance':I,'start':I,'stop':I})),
    'gaps':arr(S),'dependency_bindings':arr(obj({'experiment_id':S,'path':S})),
    'dependency_materials':arr(obj({'experiment_id':S,'asset_ids':arr(S),'reason':S}))})
EXPERIMENT=obj({'id':S,'name':S,'method_id':S,'kind':{'type':'string','enum':['numeric','curve','qualitative']},
    'description':S,'figures':arr(S),'dependencies':arr(S),'related_experiments':arr(obj({'experiment_id':S,'relationship':{'type':'string','enum':['comparison','shared_procedure']},'reason':S})),'dependency_notes':arr(S),'evidence':arr(REF),
    'observations':arr(S),'unit_notes_json':S,'design_json':S,'configuration':CONFIG})
SCHEMA=obj({'pages_audited':arr(I),'experiments':arr(EXPERIMENT),
    'coverage':arr(obj({'label':S,'page':I,'asset_id':S,'experiment_ids':arr(S),'noncomputational_reason':S,'notes':S,'evidence':arr(REF)})),'notes':S})

INSTRUCTIONS='''units的field必须逐个对应validation_fields；分组单位说明写入unit_notes_json对象字符串，不得把分组名当输出字段。研究情景、年份、政策冲击、固定假设和数组条件写入design_json对象字符串；configuration.parameters只填工具实际声明的标量调用参数，未有工具声明时填[]，不能将研究描述直接当作运行参数。输出必须匹配严格schema，不使用configuration_json字符串。configuration中所有映射均为条目列表：parameters=[{name,value}]、units=[{field,unit}]、zero_tolerance=[{field,value}]、bundle_paths=[{asset_id,paths:[相对路径]}]。未知资产ID填空字符串、未知列表填[]，不能用null或对象代替列表。validation_fields只存可执行输出的字段名；原文数值、百分比、统计口径和不等式说明放observations，不要误当字段名。dependencies仅保存实际前置输入；结果比较或流程复用使用related_experiments={experiment_id,relationship:comparison/shared_procedure,reason}列表，不能因此删除原有实验。dependency_notes保存前置任务的逻辑说明；dependency_bindings仅在已有真实生成文件时填写{experiment_id,path}，不能把中文说明当路径或凭空编文件名。gaps为缺口说明的字符串列表。dependency_materials记录已提供数据快照满足的前置输入，必须引用当前实验实际装载的data资产及理由；前置任务仍保留必验。已关联实验的coverage.noncomputational_reason留空；背景说明放coverage.notes。实验清单即使材料不齐也必须完整保留；不要虚构输入/参考/已运行状态。shape、字段与经济含义不确定时保留观察和缺口。'''

def _unique(entries,key,value):
    result={}
    for entry in entries:
        k=entry[key]
        if k in result:raise ValueError('Duplicate configuration key: '+str(k))
        result[k]=entry[value]
    return result

def typed_config(cfg):
    out={k:copy.deepcopy(cfg[k]) for k in ('tool_id','input_asset','reference_asset','asset_ids','validation_fields','gaps','dependency_bindings')}
    out['dependency_materials']=copy.deepcopy(cfg.get('dependency_materials',[]))
    out['bundle_paths']=_unique(cfg['bundle_paths'],'asset_id','paths')
    out['parameters']=_unique(cfg['parameters'],'name','value')
    out['units']=_unique(cfg['units'],'field','unit')
    out['zero_tolerance']=_unique(cfg['zero_tolerance'],'field','value')
    out['curve_checks']=[]
    for r in cfg['curve_checks']:
        rule={'type':r['type'],'field':r['field']}
        if r['type']=='coverage':rule.update(x_field=r['x_field'],min=r['minimum'],max=r['maximum'])
        else:rule['direction']=r['direction']
        if r['type']=='extremum':rule.update(index=r['index'],index_tolerance=r['index_tolerance'])
        if r['type']=='monotonic' and r['stop']>r['start']:rule.update(start=r['start'],stop=r['stop'])
        out['curve_checks'].append(rule)
    return out

def document(answer):
    doc={k:copy.deepcopy(answer[k]) for k in ('coverage','pages_audited','notes')};doc['experiments']=[]
    for item in doc['coverage']:
        # Legacy field also held background comments about non-computational
        # portions of a page alongside linked experiments; preserve the comment.
        if item.get('experiment_ids') and item.get('noncomputational_reason'):
            item['notes']='; '.join(filter(None,[item.get('notes',''),item['noncomputational_reason']]))
            item['noncomputational_reason']=''
    for item in answer['experiments']:
        e=copy.deepcopy(item)
        if 'unit_notes_json' in e:
            e['unit_notes']=json.loads(e.pop('unit_notes_json'))
            if not isinstance(e['unit_notes'],dict):raise ValueError('Unit notes must be an object')
        if 'design_json' in e:
            design=json.loads(e.pop('design_json'))
            if not isinstance(design,dict):raise ValueError('Scientific design must be an object')
            e['design']=design
        if 'configuration' in e:
            cfg=typed_config(e.pop('configuration'))
        else:
            cfg=json.loads(e.pop('configuration_json'))
            if not isinstance(cfg,dict):raise ValueError('Experiment configuration must be an object')
            allowed={'tool_id','input_asset','reference_asset','asset_ids','bundle_paths','parameters','validation_fields','zero_tolerance','units','curve_checks','gaps','dependency_bindings','dependency_materials'}
            if set(cfg)-allowed:raise ValueError('Unknown experiment configuration fields')
            # Preserve logical descriptions from legacy outputs separately;
            # never invent a filesystem path to satisfy the executable contract.
            bindings=cfg.get('dependency_bindings',[])
            if isinstance(bindings,dict):
                e.setdefault('dependency_notes',[]).extend(str(k)+': '+str(v) for k,v in bindings.items());cfg['dependency_bindings']=[]
            elif isinstance(bindings,list):
                notes=[v for v in bindings if isinstance(v,str)]
                e.setdefault('dependency_notes',[]).extend(notes);cfg['dependency_bindings']=[v for v in bindings if not isinstance(v,str)]
        if 'configuration_json' in item:
            grouped={k:v for k,v in cfg.get('units',{}).items() if k not in cfg.get('validation_fields',[])}
            if grouped:
                e.setdefault('unit_notes',{}).update(grouped)
                cfg['units']={k:v for k,v in cfg['units'].items() if k not in grouped}
        e.update(cfg);doc['experiments'].append(e)
    return doc

def preserve_coverage(document,parts):
    """Union audited observations by immutable source/page/label, without guessing.

    Synthesis can add canonical figure labels but must not erase earlier page
    observations. Links only use stable experiment IDs retained in the document.
    """
    rows={}
    for item in [*(i for part in parts for i in part['coverage']),*document['coverage']]:
        key=(item.get('asset_id',''),item['page'],item['label'])
        if key not in rows:rows[key]=copy.deepcopy(item);continue
        row=rows[key]
        row['experiment_ids']=list(dict.fromkeys([*row.get('experiment_ids',[]),*item.get('experiment_ids',[])]))
        row['evidence']=list({json.dumps(e,sort_keys=True):e for e in [*row.get('evidence',[]),*item.get('evidence',[])]}.values())
        notes=[row.get('notes',''),item.get('notes',''),row.get('noncomputational_reason',''),item.get('noncomputational_reason','')]
        if row['experiment_ids']:
            row['notes']='; '.join(dict.fromkeys(filter(None,notes)));row['noncomputational_reason']=''
    document['coverage']=list(rows.values())
    # Comments alongside linked computations are observations, not exclusions.
    for row in document['coverage']:
        if row.get('experiment_ids') and row.get('noncomputational_reason'):
            row['notes']='; '.join(filter(None,[row.get('notes',''),row['noncomputational_reason']]))
            row['noncomputational_reason']=''
    return document
