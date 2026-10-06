"""Inventory-driven bounded development; reference benchmarks never become delivered models."""
import copy,re
from pathlib import Path
from .state import Fault,Store,read,save,digest,fingerprint,file_lock
from .agents import Agents
from .spice import Simulator,validate_model,model_text,validate_protocol,measure
from .workflow import Workflow,calibration_protocols
from .task import DEFAULT_POLICY
from .contracts import can_extract
from .input_batch import validate_batch

TERMINAL={'authentication','credentials','api_configuration','budget','cache_corrupt','api_recovery_exhausted'}

def queue(packet,inventory):
    result=[]
    for item in inventory['items']:
        if not item.get('applicable',True) or item.get('binding_complete') or item.get('method_binding_complete') or item.get('constraint_audit_status')=='declared_test_setup_checked':continue
        if item.get('kind')!='test':
            result.append({'reference_id':item['id'],'status':'constraint_or_classification_review_required','item':item});continue
        if item.get('bindings'):
            result.append({'reference_id':item['id'],'status':'existing_binding_requires_verification','item':item});continue
        ref=item.get('reference_evidence',{})
        values=ref.get('values',{})
        numeric=values and all(isinstance(v,(float,int)) and not isinstance(v,bool) for v in values.values())
        result.append({'reference_id':item['id'],'status':'development_eligible' if numeric and can_extract(item) else 'reference_or_interface_gap','item':item})
    return result

def reference_model(root,packet,folder):
    if packet.get('configured_task'):
        task=read(root/packet['configured_task']);model=copy.deepcopy(task['model']);model['path']=str((root/model['path']).resolve());return model
    # Reuse a unique previously prepared reference interface, never its qualification status.
    if packet.get('fixture_drafts'):
        cases=read(root/packet['fixture_drafts']).get('cases',[])
        models={fingerprint(c['model']):c['model'] for c in cases}
        if len(models)==1:
            model=copy.deepcopy(next(iter(models.values())))
            model['path']=str((root/model['path']).resolve())
            if digest(model['path'])!=model['sha256']:raise Fault('cache_corrupt','草案参考接口模型哈希变化')
            validate_model(model_text(model['path']),model)
            model['provenance']['role']='reference_interface_benchmark_only'
            model['provenance']['physical_binding_verified']=False
            return model
    refs=[r for r in packet.get('reference_assets',[]) if r.get('role')=='reference_interface_benchmark_only' and Path(r['path']).suffix.lower() in ('.lib','.sub')]
    if len(refs)!=1:raise Fault('model_interface_gap','需要唯一的参考接口模型，不能凭空指定器件模型')
    original=root/refs[0]['path'];text=model_text(original)
    # Select the family entry using the actual source file, preserve all original internal parameters.
    declarations=re.findall(r'(?im)^\s*\.subckt\s+(\S+)\s+([^\r\n]+)',text)
    family=re.split(r'[-_]',packet['device'])[0].upper()
    matches=[(name,pins) for name,pins in declarations if name.upper()==family or packet['device'].upper().startswith(name.upper())]
    if len(matches)>1:
        longest=max(len(name) for name,_ in matches);matches=[m for m in matches if len(m[0])==longest]
    if len(matches)!=1:raise Fault('model_interface_gap','参考入口与型号不能唯一对应',{'declarations':[n for n,_ in declarations]})
    entry,pins=matches[0];ports=re.split(r'(?i)\s+params:',pins)[0].split()
    match=re.search(r'(?ims)^\s*\.subckt\s+'+re.escape(entry)+r'\s+.*?^\s*\.ends(?:\s+[^\r\n]+)?',text)
    block=match.group(0)
    if re.search(r'(?im)^\s*\.(lib|include|inc)\b',block):
        raise Fault('model_dependency_gap','参考模型含外部依赖，尚不能作为自包含执行接口',{'source':str(original),'entry':entry})
    # A wrapper avoids changing numeric/punctuation port labels in vendor internals.
    wrapper='REFERENCE_INTERFACE';declared=['P'+str(i+1) for i in range(len(ports))]
    wrapped='.subckt '+wrapper+' '+' '.join(declared)+'\nXREFERENCE '+' '.join(declared)+' '+entry+'\n.ends '+wrapper+'\n'+block+'\n'
    model={'entry':wrapper,'ports':declared,'declared_ports':declared,'provenance':{'role':'reference_interface_benchmark_only','source_sha256':digest(original),'original_entry':entry,'original_ports':ports,'physical_binding_verified':False}}
    validate_model(wrapped,model)
    path=folder/'reference_interface.lib';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(wrapped)
    model.update(path=str(path.resolve()),sha256=digest(path));return model

def sample_oracles(protocol):
    m=protocol['measurement'];a=protocol['analysis']
    if m['mode']!='sample' or a['kind'] not in ('dc','tran'):raise Fault('capability_gap','该测量需另行独立校准，禁止套用直流电阻校准')
    matched=re.fullmatch(r'v\(([^,)]+)(?:,([^,)]+))?\)',m['signal'],re.I)
    if not matched:raise Fault('capability_gap','本版样本校准仅支持节点电压或差分电压')
    pos,neg=matched.groups();neg=neg or '0'
    if pos.lower()==neg.lower():raise Fault('proposal','校准测量节点相同')
    out=[]
    for target in (.4,.8):
        components=[{'kind':'V','name':'VORACLE','nodes':[pos,neg],'value':{'dc':target}}]
        if neg!='0' and pos!='0':components.append({'kind':'V','name':'VCM','nodes':[neg,'0'],'value':{'dc':.3}})
        at=m['at']
        if a['kind']=='dc':
            analysis={'kind':'dc','source':'VDUMMY','start':at-1,'stop':at+1,'step':.1}
            components.append({'kind':'V','name':'VDUMMY','nodes':['ORACLE_DUMMY','0'],'value':{'dc':0}})
        else:
            if not 0<=at<=a['stop_s']:raise Fault('proposal','采样时刻超出分析范围')
            analysis=copy.deepcopy(a)
        pr={'device_nodes':{},'temperature_C':25,'components':components,'analysis':analysis,'measurement':copy.deepcopy(m),'checks':[]}
        expected=target*m.get('sign',1)*m.get('scale',1)
        if m.get('absolute'):expected=abs(expected)
        out.append((pr,expected))
    return out

class Developer(Workflow):
    def calibrate(self,case):
        mode=case['protocol']['measurement']['mode']
        if mode=='sample':oracles=sample_oracles(case['protocol'])
        elif mode.startswith('ac_') or mode in ('capacitance','integral_to_crossing'):oracles=calibration_protocols(case)
        else:raise Fault('capability_gap','该接口尚无独立校准，不能登记资格：'+mode)
        receipts=[]
        for p,expected in oracles:
            validate_protocol(p,self.task['model'])
            tol=max(abs(expected)*1e-4,1e-15)
            c={'id':'oracle_'+fingerprint(p)[:12],'protocol':p,'expectation':{'unit':'oracle_native','limits':{'min':expected-tol,'max':expected+tol}}}
            result=self.evaluate_one(c,self.model_path,allow_recovery=False)
            if result.get('execution')!='completed' or result.get('acceptance')!='pass':raise Fault('calibration','独立已知电路校准失败',result)
            receipts.append({'expected':expected,'tolerance':tol,'result':result})
        return receipts

def run(batch,runner,output,check_only=False,resume=False,max_items=6,transport=None,simulation_transport=None,coverage=None):
    batch=Path(batch).resolve();runner=Path(runner).resolve();output=Path(output).resolve();validate_batch(batch)
    if not 1<=max_items<=50:raise Fault('input','每器件本轮开发上限为1至50项')
    code={f.name:digest(f) for f in Path(__file__).parent.glob('*.py')}
    identity=fingerprint({'batch':digest(batch),'runner':digest(runner),'code':code,'max_items':max_items,'coverage':digest(Path(coverage)/'summary.json') if coverage else None,'test_backend':bool(transport or simulation_transport)})
    if output.exists() and not resume:raise Fault('input','请使用新开发目录或--resume')
    output.mkdir(parents=True,exist_ok=True)
    manifest=read(batch);report={'default_per_device_budgets':{'api_calls':max_items*18,'simulations':max_items*12,'seconds':1200},'schema':'inventory-development-1','status':'prepared' if check_only else 'completed_with_gaps','devices':[],'full_batch_delivery':False,'reference_models_modified':False,'test_backend':bool(transport or simulation_transport)}
    with file_lock(output/'.development.lock'):
        for row in manifest['devices']:
            root=(batch.parent/row['input']).parent;packet=read(root/'device_input.json');inventory=read(root/packet['inventory'])
            if coverage:
                from .coverage_binding import load_overlay
                inventory=load_overlay(root,packet,inventory,coverage)
            jobs=queue(packet,inventory)
            dev={'device':packet['device'],'queue':[{'reference_id':j['reference_id'],'status':j['status']} for j in jobs],'qualified':[],'results':[],'delivery_passed':False};report['devices'].append(dev)
            save(output/'summary.json',report)
            folder=output/row['folder']
            if check_only:
                try:
                    model=reference_model(root,packet,folder)
                    dev['model_interface_preflight']={'status':'self_contained_interface_prepared','entry':model['entry'],'ports':model['ports']}
                except (Fault,KeyError,ValueError,TypeError,OSError) as e:
                    dev['model_interface_preflight']={'status':'blocked','fault':e.record() if isinstance(e,Fault) else {'kind':'local_interface_error','message':str(e)}}
                print(packet['device']+'：未绑定记录 '+str(len(jobs))+'；可进入条件整理 '+str(sum(j['status']=='development_eligible' for j in jobs))+'；接口 '+dev['model_interface_preflight']['status'],flush=True)
                continue
            try:
                model=reference_model(root,packet,folder)
                limits={'api_calls':max_items*18,'simulations':max_items*12,'repairs':max_items*8,'seconds':1200}
                store=Store(folder,fingerprint({'identity':identity,'device':packet['device'],'model':model}),limits,resume and (folder/'state.json').exists())
                dev['budgets']=limits
                assets={k:{'path':str((root/v).resolve()),'sha256':digest(root/v)} for k,v in packet['materials'].items()}
                task={'device':packet['device'],'model':model,'cases':[],'inventory':inventory,'routes':packet['routes'],'policy':copy.deepcopy(DEFAULT_POLICY),'budgets':limits,'acceptance_standard':packet['acceptance_standard'],'reference_assets':assets}
                task['policy']['workers']=1
                agents=Agents(store,task['routes'],transport);sim=Simulator(store,{'argv':[str(runner),'-b','-ascii','{circuit}'],'timeout_seconds':120},model,transport=simulation_transport) if simulation_transport else Simulator(store,{'argv':[str(runner),'-b','-ascii','{circuit}'],'timeout_seconds':120},model)
                workflow=Developer(task,store,agents,sim)
                # Per-record evidence includes only corresponding pages and pin records.
                pages=read(root/packet['materials']['manual_page_evidence']);ports=read(root/packet['materials']['ports'])
                eligible=[j for j in jobs if j['status']=='development_eligible']
                for j in eligible[:max_items]:
                    item=copy.deepcopy(j['item']);item['evidence']=item.get('evidence',[])+['manual_pages','ports']
                    item['supplied_manual_pages']=[p for p in pages if p.get('page') in item.get('pdf_pages',[])];item['supplied_ports']=ports
                    item['execution_role']='reference_interface_benchmark_only; no device delivery claim'
                    print(packet['device']+'：自动开发 '+item['id'],flush=True)
                    result=workflow.develop_one(item)
                    if result.get('case'):
                        dev['qualified'].append({'reference_id':item['id'],'case':result['case'],'role':'reference_interface_benchmark_only'})
                        if not any(c['id']==result['case']['id'] for c in workflow.cases):workflow.cases.append(result['case'])
                        workflow.checkpoint()
                    dev['results'].append({'reference_id':item['id'],'result':result});save(output/'summary.json',report)
                dev['remaining_budget_queue']=[j['reference_id'] for j in eligible[max_items:]]
                store.finish('capability_development_with_gaps')
            except Fault as e:
                dev['fault']=e.record();save(output/'summary.json',report)
                if e.kind in TERMINAL:report['status']='stopped_with_evidence';break
            except (KeyError,ValueError,TypeError,OSError) as e:
                dev['fault']={'kind':'local_interface_error','message':str(e)}
            save(output/'summary.json',report)
        save(output/'summary.json',report)
    return report
