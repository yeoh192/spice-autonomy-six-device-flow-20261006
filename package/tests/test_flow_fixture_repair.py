import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flow_runtime import autonomous_qualification as aq
from flow_runtime import fixture_repair_policy as fp
from flow_runtime.state import Fault, save, read, digest, fingerprint
from flow_runtime.spice import render


class FixtureRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runner=self.root/'runner';self.runner.write_text('offline')
        self.protocol={'temperature_C':20,'device_nodes':{'P':'P','N':'N'},'components':[
            {'kind':'I','name':'IDRIVE','nodes':['N','P'],'value':{'dc':0}},
            {'kind':'R','name':'RGROUND','nodes':['N','0'],'value':1e-6}],
            'analysis':{'kind':'dc','source':'IDRIVE','start':0,'stop':.01,'step':.002},
            'measurement':{'mode':'sample','signal':'v(P,N)','at':.01,'scale':100},'checks':[]}
        self.model={'entry':'RTEST','ports':['P','N'],'declared_ports':['P','N'],'path':'model.lib','sha256':'fake'}
        self.case={'id':'r','reference_ids':['ref'],'protocol':self.protocol,'model':self.model,'expectation':{'unit':'Ω','limits':{'max':.3}}}
        self.packet={'device':'random_resistor','inventory':'inventory.json','fixture_drafts':'fixture_drafts.json',
            'materials':{'ports':'ports.json','manual':'manual.pdf','manual_page_evidence':'pages.json','electrical':'electrical.json','evidence':'evidence.json'},
            'routes':{'design':{'provider':'qwen','model':'test'},'review':{'provider':'glm','model':'test'}},
            'acceptance_standard':{'typical_tolerance_percent':10,'curve_mae_percent':5,'curve_max_error_percent':10},'assets':{}}
        for name,data in [('fixture_drafts.json',{'cases':[self.case]}),('inventory.json',{'items':[{'id':'ref','pdf_pages':[1]}]}),
            ('ports.json',{'pins':[1,2]}),('pages.json',[{'page':1,'text':'resistance at20C max0.3ohm'}]),
            ('electrical.json',{'resistance_max':.3}),('evidence.json',{'source':'manual'})]:
            save(self.root/name,data);self.packet['assets'][name]=digest(self.root/name)
        (self.root/'model.lib').write_text('.subckt RTEST P N\nR1 P N .1\n.ends\n')
        (self.root/'manual.pdf').write_text('offline evidence');save(self.root/'device_input.json',self.packet)
        self.batch=self.root/'batch.json';save(self.batch,{'devices':[{'input':'device_input.json'}]})
        self.calls=[];self.sim_calls=[]

    def approval(self,stage):
        r={'decision':'approve','reason':'verified scope','evidence_ids':['ref','actual_circuit','program_facts'],
            'checks':dict.fromkeys(['conditions','ports','measurement','scope']+(['results'] if stage=='post_trial' else []),True),
            'unresolved':[],'issues':[],'read_requests':[]}
        if stage=='design':r.update(action='keep',parameters={})
        return r

    def agents(self,role,route,context,tokens):
        self.calls.append((role,context));return self.approval(context['stage']),{}

    def backend(self,p,model,folder):
        self.sim_calls.append(p)
        r=next((c['value'] for c in p['components'] if c['name']=='RCAL'),.1)
        a=p['analysis'];xs=[a['start']+i*a['step'] for i in range(round((a['stop']-a['start'])/a['step'])+1)]
        (folder/'test.log').write_text('analytic resistor fixture')
        rows=['Title: independent resistor','Plotname: DC transfer characteristic','Flags: real',
              'No. Variables: 3','No. Points: '+str(len(xs)),'Variables:','0 current current','1 v(p) voltage','2 v(n) voltage','Values:']
        for i,x in enumerate(xs):rows.extend([str(i)+' '+str(x),str(r*x),'0'])
        (folder/'test.raw').write_text('\n'.join(rows)+'\n')

    def run_flow(self,agents=None,backend=None,rounds=3,resume=False):
        with patch.object(aq,'validate_batch'),patch.object(aq,'verified_calibration',return_value={'identity':'offline'}):
            return aq.run(self.batch,self.root/'calibration.json',self.runner,self.root/'out',rounds=rounds,
                resume=resume,agent_transport=agents or self.agents,simulation_transport=backend or self.backend)

    def test_pre_trial_does_not_advertise_future_results(self):
        r=self.run_flow();self.assertEqual(r['method_qualified'],1)
        for role,c in self.calls:
            self.assertEqual('benchmark_result' in c['evidence_ids'],c['stage']=='post_trial')
            self.assertEqual('trial' in c,c['stage']=='post_trial')
        self.assertEqual(r['usage']['simulations'],3)

    def test_ast_disproves_three_node_current_source_claim(self):
        facts=fp.program_facts(self.protocol,self.model)
        source=facts['actual_sources'][0]
        self.assertEqual(source['terminal_count'],2);self.assertEqual(source['actual_line'],'IDRIVE N P 0')

    def test_stage_dispute_automatically_returns_to_agent(self):
        rejected=[]
        def agents(role,route,c,t):
            self.calls.append((role,c))
            if c['stage']=='pre_trial' and not rejected:
                rejected.append(True);return {'decision':'defer','reason':'need benchmark before trial',
                    'issues':[{'kind':'stage','claim':'missing result'}],'unresolved':['benchmark result'],'read_requests':[]},{}
            return self.approval(c['stage']),{}
        r=self.run_flow(agents)
        self.assertEqual(r['method_qualified'],1);self.assertEqual(r['usage']['repairs'],1)
        design=[c for role,c in self.calls if role=='test_designer'][-1]
        self.assertEqual(design['feedback']['fault']['evidence']['route']['kind'],'review_dispute')

    def test_syntax_dispute_gets_program_facts_and_re_review(self):
        rejected=[]
        def agents(role,route,c,t):
            self.calls.append((role,c))
            if c['stage']=='pre_trial' and not rejected:
                rejected.append(True);return {'decision':'revise','reason':'3 nodes','issues':[{'kind':'syntax'}],'unresolved':[]},{}
            return self.approval(c['stage']),{}
        r=self.run_flow(agents);self.assertEqual(r['method_qualified'],1)
        next_design=[c for role,c in self.calls if c['stage']=='design'][-1]
        self.assertEqual(next_design['feedback']['program_facts']['actual_sources'][0]['terminal_count'],2)

    def test_solver_repair_from_execution_error_is_retained_after_real_pipeline(self):
        def backend(p,m,f):
            if p['device_nodes'] and p.get('method')!='gear':raise Fault('execution','solver convergence')
            self.backend(p,m,f)
        def agents(role,route,c,t):
            r=self.approval(c['stage'])
            if c['stage']=='design' and c['feedback']:r.update(action='solver_method',parameters={'method':'gear'})
            return r,{}
        r=self.run_flow(agents,backend)
        self.assertEqual(r['retained_repairs'],1);self.assertEqual(r['results'][0]['protocol']['method'],'gear')
        self.assertEqual(r['usage']['repairs'],1)
        self.assertFalse((self.root/'out/capabilities').exists())

    def test_missing_evidence_is_read_then_automatically_revised(self):
        def agents(role,route,c,t):
            self.calls.append((role,c))
            if c['stage']=='design' and not c['supplemental_evidence']:
                return {'decision':'defer','reason':'need ports','action':'keep','parameters':{},
                        'issues':[{'kind':'ports'}],'read_requests':['ports'],'unresolved':['mapping']},{}
            return self.approval(c['stage']),{}
        r=self.run_flow(agents);self.assertEqual(r['method_qualified'],1)
        self.assertTrue(any(c['supplemental_evidence'].get('ports',{}).get('status')=='read' for _,c in self.calls))

    def test_approval_with_pending_read_request_is_not_silently_passed(self):
        def agents(role,route,c,t):
            r=self.approval(c['stage'])
            if c['stage']=='design' and not c['supplemental_evidence']:r['read_requests']=['ports']
            return r,{}
        r=self.run_flow(agents)
        self.assertEqual(r['method_qualified'],1);self.assertEqual(r['usage']['repairs'],1)

    def test_pending_status_is_not_removed_from_source(self):
        before=digest(self.root/'inventory.json');self.run_flow()
        self.assertEqual(before,digest(self.root/'inventory.json'))

    def test_bad_read_request_does_not_execute_or_crash_loop(self):
        def agents(role,route,c,t):
            self.calls.append((role,c))
            if not c['feedback']:return {'decision':'defer','action':'keep','parameters':{},'read_requests':['../../secret'],'unresolved':['x']},{}
            return self.approval(c['stage']),{}
        r=self.run_flow(agents);self.assertEqual(r['method_qualified'],1)
        self.assertIn('read_request_error',self.calls[-1][1]['supplemental_evidence'])

    def test_reject_protocol_or_threshold_replacement(self):
        for field,value in [('protocol',self.protocol),('expectation',{'limits':{'max':3}}),('shell','rm -rf')]:
            with self.assertRaises(Fault):fp.apply(self.case,{'action':'keep','parameters':{},field:value})

    def test_malformed_action_is_feedback_fault_not_local_crash(self):
        with self.assertRaises(Fault) as e:
            fp.apply(self.case,{'action':'ground_reference','parameters':{'nodes':[{}],'resistance_ohm':1e10}})
        self.assertEqual(e.exception.kind,'proposal')
        with self.assertRaises(Fault) as e:
            aq.evidence_read(self.root,self.packet,[{}])
        self.assertEqual(e.exception.kind,'proposal')

    def test_budget_failure_preserves_report(self):
        def agents(*a):raise Fault('budget','API budget exhausted')
        r=self.run_flow(agents)
        self.assertEqual(r['status'],'stopped_with_evidence')
        self.assertEqual(r['terminal']['kind'],'budget')
        self.assertTrue((self.root/'out/summary.json').is_file())

    def test_step_may_refine_not_coarsen(self):
        new=fp.apply(self.case,{'action':'refine_grid','parameters':{'step':.001}})
        self.assertEqual(new['analysis']['stop'],.01);self.assertEqual(new['measurement'],self.protocol['measurement'])
        with self.assertRaises(Fault):fp.apply(self.case,{'action':'refine_grid','parameters':{'step':.004}})

    def test_original_sources_temperature_and_port_mapping_cannot_change(self):
        p=fp.apply(self.case,{'action':'solver_method','parameters':{'method':'gear'}})
        for field in ('components','temperature_C','device_nodes','measurement','analysis'):
            self.assertEqual(p[field],self.protocol[field])

    def test_high_resistance_anchor_repair_has_mandatory_sensitivity_trial(self):
        def agents(role,route,c,t):
            r=self.approval(c['stage'])
            if c['stage']=='design':r.update(action='ground_reference',parameters={'nodes':['P'],'resistance_ohm':1e10})
            return r,{}
        r=self.run_flow(agents)
        self.assertEqual(r['usage']['simulations'],4);self.assertEqual(r['retained_repairs'],1)
        self.assertEqual(r['results'][0]['sensitivity']['status'],'pass')

    def test_bad_sensitivity_rolls_back_and_cannot_register(self):
        def agents(role,route,c,t):
            r=self.approval(c['stage'])
            if c['stage']=='design':r.update(action='ground_reference',parameters={'nodes':['P'],'resistance_ohm':1e10})
            return r,{}
        def backend(p,m,f):
            self.backend(p,m,f)
            anchors=[c for c in p['components'] if c['name']=='RAUTOREF1']
            if anchors and anchors[0]['value']>1e10:
                text=(f/'test.raw').read_text();text=text.replace('0.001\n','0.002\n');(f/'test.raw').write_text(text)
        r=self.run_flow(agents,backend,rounds=1)
        self.assertEqual(r['retained_repairs'],0);self.assertEqual(r['results'][0]['fault']['kind'],'fixture_sensitivity')

    def test_grounding_cannot_create_unknown_nodes_or_low_resistance(self):
        for params in ({'nodes':['NEW'],'resistance_ohm':1e10},{'nodes':['P'],'resistance_ohm':100},{'nodes':['0'],'resistance_ohm':1e10}):
            with self.assertRaises(Fault):fp.apply(self.case,{'action':'ground_reference','parameters':params})

    def test_failed_post_review_does_not_retain_repair(self):
        def agents(role,route,c,t):
            if c['stage']=='post_trial':return {'decision':'revise','issues':[{'kind':'measurement'}],'unresolved':['wrong method']},{}
            r=self.approval(c['stage'])
            if c['stage']=='design':r.update(action='solver_method',parameters={'method':'gear'})
            return r,{}
        r=self.run_flow(agents,rounds=1)
        self.assertEqual(r['method_qualified'],0);self.assertEqual(r['retained_repairs'],0)

    def test_valid_resume_no_new_api_or_simulation(self):
        self.run_flow();n=len(self.calls);m=len(self.sim_calls);r=self.run_flow(resume=True)
        self.assertEqual(len(self.calls),n);self.assertEqual(len(self.sim_calls),m)
        self.assertEqual(r['method_qualified'],1)

    def test_tampered_qualified_trial_blocks_resume(self):
        r=self.run_flow();(Path(r['results'][0]['benchmark']['folder'])/'test.raw').write_text('bad')
        with self.assertRaises(Fault):self.run_flow(resume=True)

    def test_round_budget_stops_with_specific_gap_not_success(self):
        r=self.run_flow(lambda *a:({'decision':'defer','action':'keep','parameters':{},'unresolved':['unknown physical condition']},{}),rounds=2)
        self.assertEqual(r['results'][0]['status'],'repair_budget_with_gaps')
        self.assertEqual(r['usage']['repairs'],1);self.assertEqual(r['usage']['simulations'],0)

    def test_model_deviation_queued_without_editing_reference_model(self):
        self.case['expectation']['limits']['max']=.01;save(self.root/'fixture_drafts.json',{'cases':[self.case]})
        before=digest(self.root/'model.lib');r=self.run_flow()
        self.assertEqual(r['method_qualified'],1);self.assertEqual(before,digest(self.root/'model.lib'))
        queue=r['results'][0]['model_repair_required']
        self.assertEqual(queue['status'],'candidate_model_required_before_model_repair')
        self.assertTrue(queue['reference_model_must_not_be_modified'])

    def test_auth_fault_is_terminal_and_report_saved(self):
        def agents(*a):raise Fault('authentication','bad key')
        r=self.run_flow(agents)
        self.assertEqual(r['status'],'stopped_with_evidence');self.assertEqual(r['usage']['api_calls'],1)
        self.assertTrue((self.root/'out/summary.json').exists())

    def test_wrapper_alias_facts_are_generic_not_device_name_based(self):
        text='.subckt ANY 3 5 6 9\nLx 3 5 1m\n.ends\n.subckt WRAP A B C D\nX1 A B C D ANY\n.ends\n'
        m={'entry':'WRAP','declared_ports':['A','B','C','D'],'ports':['x','y','z','w']}
        p=copy.deepcopy(self.protocol);p['device_nodes']={'x':'P','y':'N','z':'A','w':'B'}
        facts=fp.program_facts(p,m,text,{'topology':[{'id':'secondary','dotted_pins':[6,7],'other_pins':[9,10]}]})
        last=facts['wrapper_positional_binding'][-1]
        self.assertEqual(last['semantic_port'],'w');self.assertEqual(last['manual_terminal_groups'][0]['pins'],[9,10])

if __name__=='__main__':unittest.main()
