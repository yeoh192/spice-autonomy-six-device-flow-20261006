import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flow_runtime import draft_qualification as dq
from flow_runtime.state import Fault, save, digest, fingerprint, read


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = self.root/'runner'; self.runner.write_text('offline runner')
        self.protocol = {'temperature_C':20,'device_nodes':{'P':'P','N':'N'},
            'components':[{'kind':'I','name':'IDRIVE','nodes':['N','P'],'value':{'dc':0}},
                          {'kind':'R','name':'RGROUND','nodes':['N','0'],'value':1e-6}],
            'analysis':{'kind':'dc','source':'IDRIVE','start':0,'stop':.01,'step':.002},
            'measurement':{'mode':'sample','signal':'v(P,N)','at':.01,'scale':100},'checks':[]}
        self.model = {'entry':'RTEST','ports':['P','N'],'declared_ports':['P','N'],'path':'model.lib','sha256':'dummy'}
        self.case = {'id':'r','reference_ids':['ref'],'protocol':self.protocol,'model':self.model,
            'expectation':{'unit':'Ω','limits':{'max':.3}}}
        self.packet = {'device':'synthetic','inventory':'inventory.json','fixture_drafts':'fixture_drafts.json',
            'materials':{'ports':'ports.json','manual':'manual.pdf','manual_page_evidence':'pages.json'},
            'routes':{'design':{'provider':'qwen','model':'test'},'review':{'provider':'glm','model':'test'}},
            'acceptance_standard':{'typical_tolerance_percent':10,'curve_mae_percent':5,'curve_max_error_percent':10}}
        for name,value in [('device_input.json',self.packet),('fixture_drafts.json',{'cases':[self.case]}),
            ('inventory.json',{'items':[{'id':'ref','pdf_pages':[1]}]}),('ports.json',{'ports':['P','N']}),
            ('pages.json',[{'page':1,'text':'Resistance at20C max0.3 ohm'}])]: save(self.root/name,value)
        (self.root/'manual.pdf').write_text('test evidence'); (self.root/'model.lib').write_text('.subckt RTEST P N\nR1 P N .1\n.ends\n')
        self.batch=self.root/'batch.json';save(self.batch,{'devices':[{'input':'device_input.json'}]})
        self.calibration=self.root/'calibration.json'
        self.dispatches=[]

    def approval(self, post=False):
        return {'decision':'approve','checks':dict.fromkeys(['conditions','ports','measurement','scope']+(['results'] if post else []), True),
                'evidence_ids':['ref','actual_circuit'],'unresolved':[]}

    def transport(self, role, route, context, tokens):
        self.dispatches.append((role,route['provider'],context))
        return self.approval('benchmark_result' in context), {'finish_reason':'stop'}

    def run_flow(self, transport=None, resume=False):
        def backend(p, model, folder):
            # Independently generate DC voltage of a resistor under requested current sweep.
            r=next((c['value'] for c in p['components'] if c['name']=='RCAL'), .1)
            a=p['analysis'];xs=[a['start']+i*a['step'] for i in range(6)]
            (folder/'test.log').write_text('offline resistor network')
            rows=['Title: analytic resistor','Plotname: DC transfer characteristic','Flags: real','No. Variables: 3',
                'No. Points: 6','Variables:','0 current current','1 v(p) voltage','2 v(n) voltage','Values:']
            for i,x in enumerate(xs):rows.extend([str(i)+' '+str(x),str(x*r),'0'])
            (folder/'test.raw').write_text('\n'.join(rows)+'\n')
        with patch.object(dq,'validate_batch'), patch.object(dq,'verified_calibration',return_value={'verified':True}):
            return dq.run(self.batch,self.calibration,self.runner,self.root/'out',rounds=2,resume=resume,
                          agent_transport=transport or self.transport,simulation_transport=backend)

    def test_review_cannot_skip_explicit_checks(self):
        x=self.approval();x['checks'].pop('ports')
        with self.assertRaises(Fault):dq.approved(x,['ref','actual_circuit'])

    def test_unknown_citation_rejected(self):
        x=self.approval();x['evidence_ids']=['invented']
        with self.assertRaises(Fault):dq.approved(x,['ref'])

    def test_unresolved_conditions_defer_even_if_approved(self):
        x=self.approval();x['unresolved']=['unknown excitation']
        with self.assertRaises(Fault):dq.approved(x,['ref','actual_circuit'])

    def test_reference_benchmark_never_promotes_inventory_or_live_capability(self):
        before=digest(self.root/'inventory.json');r=self.run_flow()
        self.assertEqual(r['method_qualified'],1)
        self.assertFalse(r['results'][0]['candidate_model_verified'])
        self.assertFalse((self.root/'out/capabilities').exists())
        self.assertEqual(before,digest(self.root/'inventory.json'))
        self.assertEqual([v[1] for v in self.dispatches],['qwen','glm','glm'])

    def test_reviewer_revision_automatically_feedbacks_then_reuses_simulation(self):
        failed=[]
        def transport(role,route,context,tokens):
            self.dispatches.append((role,route['provider'],context))
            if 'benchmark_result' in context and not failed:
                failed.append(True);return {'decision':'revise','reason':'explain actual conditions'},{}
            return self.approval('benchmark_result' in context),{}
        r=self.run_flow(transport)
        self.assertEqual(r['method_qualified'],1)
        self.assertEqual(r['usage']['simulations'],3)
        self.assertTrue(any(d[2]['feedback'] for d in self.dispatches))

    def test_no_simulation_after_deferred_evidence(self):
        r=self.run_flow(lambda *args:({'decision':'defer','reason':'missing test definition'},{}))
        self.assertEqual(r['usage']['simulations'],0)
        self.assertEqual(r['results'][0]['status'],'evidence_gap')

    def test_resume_reuses_qualified_results_no_paid_dispatch(self):
        self.run_flow();n=len(self.dispatches);r=self.run_flow(resume=True)
        self.assertEqual(n,len(self.dispatches));self.assertEqual(r['method_qualified'],1)

    def test_tampered_raw_invalidates_qualified_resume(self):
        r=self.run_flow();p=Path(r['results'][0]['benchmark']['folder'])/'test.raw';p.write_text('tampered')
        with self.assertRaises(Fault):self.run_flow(resume=True)

    def test_api_auth_failure_stops_batch_and_saves_report(self):
        def transport(*args):raise Fault('authentication','bad key')
        r=self.run_flow(transport)
        self.assertEqual(r['status'],'stopped_with_evidence')
        self.assertEqual(r['usage']['api_calls'],1)
        self.assertTrue((self.root/'out/summary.json').is_file())

    def test_frozen_protocol_not_overridden_by_agent(self):
        def transport(role,route,ctx,tokens):
            response=self.approval('benchmark_result' in ctx);response['protocol']={'analysis':'fake'}
            return response,{}
        r=self.run_flow(transport)
        self.assertEqual(r['results'][0]['status'],'revision_budget')
        self.assertEqual(r['usage']['simulations'],0)

    def test_missing_manual_evidence_prevents_api(self):
        save(self.root/'pages.json',[]);r=self.run_flow()
        self.assertEqual(r['results'][0]['status'],'evidence_gap');self.assertEqual(r['usage']['api_calls'],0)

    def test_offline_receipt_cannot_be_live_calibration(self):
        save(self.calibration,{'schema':'ac-calibration-1','status':'calibrated','current_calibration_passed':True,'test_backend':True})
        with self.assertRaises(Fault):dq.verified_calibration(self.calibration,self.runner)

if __name__=='__main__':unittest.main()
