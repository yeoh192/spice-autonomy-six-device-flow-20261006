"""Reusable bounded fixture actions; handbook excitation and measurement stay fixed."""
import copy
import math
import re
from .state import Fault, finite, fingerprint
from .spice import validate_protocol, render


def policy(case):
    p = case['protocol']
    return {'version':'fixture-repair-policy-1',
        'immutable':['temperature_C','device_nodes','measurement','expectation','reference_ids',
                     'original_components','fixture_models','analysis_endpoints_and_frequency'],
        'allowed_actions':['keep','solver_method','refine_grid','ground_reference'],
        'parameter_schemas':{'keep':{},'solver_method':{'method':'trap|gear'},
            'refine_grid':{'step':'DC only: positive value <= original step',
                           'max_step_s':'transient only: positive value <= original max_step_s'},
            'ground_reference':{'nodes':'1..4 existing nonzero node names',
                                'resistance_ohm':'finite 1e9..1e12'}},
        'limits':{'added_resistor_ohm':[1e9,1e12], 'max_added_resistors':4,
                  'relative_sensitivity_limit':1e-4},
        'original_protocol_sha256':fingerprint(p),
        'note':'Ground references are numerical auxiliaries, require independent sensitivity trial. '
               'No original component rewiring, source value, physical port or test threshold changes.'}


def _apply(case, proposal):
    """Declarative operations only; cannot execute code or silently replace conditions."""
    if set(proposal) - {'decision','reason','evidence_ids','unresolved','checks','action','parameters','read_requests','issues'}:
        raise Fault('proposal','修复提案含未知字段；不接受任意protocol、代码或规格修改')
    p = copy.deepcopy(case['protocol'])
    action, args = proposal.get('action','keep'), proposal.get('parameters',{})
    if not isinstance(args,dict): raise Fault('proposal','parameters必须是对象')
    if action == 'keep':
        if args: raise Fault('proposal','keep不能附带修改参数')
    elif action == 'solver_method':
        if set(args) != {'method'} or args['method'] not in ('trap','gear'):
            raise Fault('proposal','只允许trap或gear')
        p['method'] = args['method']
    elif action == 'refine_grid':
        a = p['analysis']; field = 'step' if a['kind']=='dc' else 'max_step_s' if a['kind']=='tran' else None
        if field is None or set(args) != {field}:
            raise Fault('proposal','单点交流频率冻结；DC/瞬态仅允许细化步长')
        value = finite(args[field])
        if not 0 < value <= a[field] or a[field]/value > 100:
            raise Fault('proposal','步长只能缩小，最多100倍')
        a[field] = value
    elif action == 'ground_reference':
        if set(args) != {'nodes','resistance_ohm'} or not isinstance(args['nodes'],list):
            raise Fault('proposal','ground_reference需要节点列表与电阻值')
        value=finite(args['resistance_ohm'])
        nodes=args['nodes'];known={n for c in p['components'] for n in c['nodes']} | set(p['device_nodes'].values())
        if not 1 <= len(nodes) <= 4 or len(set(nodes)) != len(nodes) or not 1e9 <= value <= 1e12:
            raise Fault('proposal','辅助接地数量或阻值越界')
        if any(n not in known or n=='0' for n in nodes):
            raise Fault('proposal','仅允许已存在的非零节点')
        names={c['name'].lower() for c in p['components']}
        for i,n in enumerate(nodes):
            name='RAUTOREF'+str(i+1)
            if name.lower() in names:raise Fault('proposal','辅助元件名与原电路冲突')
            p['components'].append({'kind':'R','name':name,'nodes':[n,'0'],'value':value})
    else:raise Fault('proposal','未实现的受限修复动作：'+str(action))
    validate_protocol(p,case['model'])
    return p


def apply(case, proposal):
    try:
        if not isinstance(proposal,dict):raise Fault('proposal','修复提案必须为对象')
        return _apply(case,proposal)
    except (KeyError,TypeError,ValueError,IndexError) as e:
        raise Fault('proposal','受限动作参数格式错误：'+str(e)) from None


def sensitivity_variant(case, proposal):
    if proposal.get('action') != 'ground_reference':return None
    alt=copy.deepcopy(proposal)
    r=alt['parameters']['resistance_ohm']
    alt['parameters']['resistance_ohm']=r*10 if r <= 1e11 else r/10
    return apply(case,alt)


def sensitivity(a,b):
    if 'value' not in a or 'value' not in b:
        raise Fault('capability_gap','当前敏感性比较仅支持标量测量')
    x,y=finite(a['value']),finite(b['value'])
    delta=abs(x-y);limit=max(max(abs(x),abs(y))*1e-4,1e-12)
    if delta>limit:raise Fault('fixture_sensitivity','辅助接地改变了测量；回退候选',
                               {'first':x,'second':y,'delta':delta,'limit':limit})
    return {'first':x,'second':y,'absolute_delta':delta,'absolute_limit':limit,'status':'pass'}


def program_facts(protocol, model, model_text='', ports=None):
    """Facts from the validated AST; syntax opinions cannot alter these records."""
    validate_protocol(protocol,model)
    circuit=render(protocol,model)
    sources=[]
    for c in protocol['components']:
        if c['kind'] in ('V','I'):
            line=next(l for l in circuit.splitlines() if l.startswith(c['name']+' '))
            sources.append({'name':c['name'],'terminal_count':2,'nodes':c['nodes'],
                            'value':c['value'],'actual_line':line,'validated':True})
    declarations={}
    for match in re.finditer(r'(?im)^\s*\.subckt\s+(\S+)\s+([^\r\n]+)\r?\n(.*?)(?=^\s*\.ends\b)',model_text,re.S|re.M):
        declarations[match[1].lower()]={'ports':match[2].split(),'body':match[3]}
    wrapper=declarations.get(model['entry'].lower()); bindings=[]
    if wrapper:
        calls=[l.split() for l in wrapper['body'].splitlines() if l.strip().lower().startswith('x')]
        if len(calls)==1 and calls[0][-1].lower() in declarations:
            call=calls[0];child=declarations[call[-1].lower()]
            if len(call[1:-1])==len(child['ports']):
                semantic=dict(zip(model['declared_ports'],model['ports']))
                for external,physical in zip(call[1:-1],child['ports']):
                    aliases=[]
                    for winding in (ports or {}).get('topology',[]):
                        for group in ('dotted_pins','other_pins'):
                            pins=winding.get(group,[])
                            if physical in [str(pin) for pin in pins]:
                                aliases.append({'winding':winding.get('id'),'terminal_group':group,'pins':pins})
                    bindings.append({'semantic_port':semantic.get(external), 'wrapper_node':external,
                        'child_port':physical,'child_entry':call[-1],'manual_terminal_groups':aliases})
    return {'evidence_id':'program_facts','syntax_valid':True,'actual_sources':sources,
        'wrapper_positional_binding':bindings,
        'binding_scope':'Parsed positional map; manual terminal grouping still reviewed. '
            'Multiple physical pins in a terminal group are not automatically separate windings.',
        'process_metadata':'binding_complete/delivery_eligible/qualification_status are workflow states, '
            'not a physical measurement defect; qualifying this method is exactly the current task.'}


def decision_route(response, stage):
    if response.get('decision') not in ('approve','revise','defer'):
        return {'kind':'response_format','action':'feedback_revision'}
    if response.get('decision')=='approve' and not response.get('unresolved'):
        return {'kind':'approved','action':'validate_checks'}
    issues=response.get('issues',[])
    if not isinstance(issues,list):return {'kind':'response_format','action':'feedback_revision'}
    kinds={i.get('kind') for i in issues if isinstance(i,dict)}
    if kinds & {'syntax','stage','process_status'}:
        return {'kind':'review_dispute','action':'program_facts_then_re_review'}
    if kinds & {'missing_evidence','conditions','ports'} or response.get('read_requests'):
        return {'kind':'evidence_gap','action':'bounded_evidence_read_then_revision'}
    if 'model_deviation' in kinds:return {'kind':'model_deviation','action':'candidate_repair_queue'}
    return {'kind':'review_revision','action':'feedback_revision','stage':stage}
