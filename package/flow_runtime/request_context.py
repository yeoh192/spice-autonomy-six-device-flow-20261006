"""Bound wire evidence without removing any acceptance target or regression ID."""
import copy,json
from .state import Fault

def compact(role,context):
    c=copy.deepcopy(context)
    if role not in ('model_optimizer','patch_reviewer','model_diagnoser','model_repair_designer'):
        return c
    shared=c.get('shared_evidence',c.get('candidate_evidence',{}))
    rows=shared.get('tests',[])
    if not rows:return c
    if c.get('model_text') == shared.get('model',{}).get('text'):
        c.pop('model_text',None)
        c['model_text_reference']='shared_evidence.model.text'
    ids={r['test'] for r in rows}
    for key in ('results','cases'):
        entries=c.get(key)
        if isinstance(entries,list) and all(x.get('test',x.get('id')) in ids for x in entries):
            c.pop(key);c[key+'_reference']='See shared_evidence.tests or candidate_evidence.tests by test ID.'
    focus=[r['test'] for r in rows if r.get('result',{}).get('acceptance')!='pass'] or [r['test'] for r in rows]
    offset=(int(c.get('master_cycle',0))*6)%len(focus)
    selected=set((focus+focus)[offset:offset+6])
    # Reviewers retain all actual circuits; optimizer rotates detailed focus only.
    for r in rows:
        r.pop('contract',None)
        if 'manual_records' in r:r['manual_records']=[{k:v for k,v in m.items() if k in ('id','evidence')} for m in r['manual_records']]
        result=r.get('result',{})
        for k in ('artifacts','artifact_hashes'):result.pop(k,None)
        if role=='model_optimizer' and r['test'] not in selected:
            r.pop('actual_test_circuit',None)
            r['circuit_reference']='Full hashed evidence file; detailed focus rotates each master cycle.'
    if role=='model_optimizer':c['focus_test_ids']=sorted(selected)
    c['evidence_rule']='All listed tests and frozen targets remain required for full regression. Detailed focus is planning only; never change acceptance or discard failed tests.'
    return c

def check_size(context,maximum=90000):
    size=len(json.dumps(context,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    if size>maximum:raise Fault('request_context','请求超过字节上限；须缩小证据，未调用API',{'bytes':size,'maximum_bytes':maximum})
    return size


def fit(role, context, maximum=90000):
    """Reduce sampled detail only; preserve targets, circuits and error summaries."""
    c=copy.deepcopy(context)
    size=lambda:len(json.dumps(c,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    if size()<=maximum:return c
    if role in ('model_optimizer','patch_reviewer','model_diagnoser','model_repair_designer'):
        for name in ('shared_evidence','candidate_evidence'):
            for row in c.get(name,{}).get('tests',[]):
                residual=row.get('signed_residual',{})
                if 'comparison_samples' in residual:
                    residual.pop('comparison_samples')
                    residual['detail_reference']='Full comparison samples retained in local hashed evidence.'
                # Result already has scalar/metrics; never duplicate residual detail.
                row.get('result',{}).pop('signed_residual',None)
    if size()>maximum:
        for name in ('shared_evidence','candidate_evidence'):
            for row in c.get(name,{}).get('tests',[]):
                row.get('signed_residual',{}).pop('intervals',None)
    check_size(c,maximum)
    return c
