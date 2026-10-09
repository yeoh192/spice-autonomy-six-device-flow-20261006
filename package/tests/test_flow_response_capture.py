import json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from flow_runtime.agents import Agents
from flow_runtime.state import Fault,Store
from flow_runtime.request_context import compact,check_size
class CaptureTests(unittest.TestCase):
 def test_length_response_saved_before_rejection_and_key_redacted(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(d,{},dict(api_calls=2,simulations=0,repairs=0,seconds=10));a=Agents(s,{'design':{'provider':'qwen','model':'fake'}});a.keys['qwen']='secret-test-value'
   raw={'choices':[{'finish_reason':'length','message':{'content':'{"edits":','reasoning_content':'returned explanation secret-test-value'}}]}
   response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(raw).encode()
   folder=Path(d)/'attempt'
   with patch('urllib.request.urlopen',return_value=response),self.assertRaises(Fault) as ex:a._http('model_optimizer',a.route('model_optimizer'),{},4096,folder)
   self.assertEqual(ex.exception.kind,'response_incomplete');self.assertEqual((folder/'content.txt').read_text(),'{"edits":');self.assertNotIn('secret-test-value',(folder/'raw_response.json').read_text());self.assertTrue((folder/'reasoning_content.txt').exists())
 def test_invalid_json_and_missing_choices_saved_without_capture_crash(self):
  with tempfile.TemporaryDirectory() as d:
   s=MagicMock();a=Agents(s,{})
   for body in (b'partial JSON',b'{"choices":null}'):
    a._capture_response(Path(d),body);self.assertEqual((Path(d)/'provider_response.txt').read_bytes(),body)
 def test_compaction_preserves_all_targets_and_rotates_details(self):
  rows=[{'test':str(i),'result':{'execution':'completed','acceptance':'fail','value':i},'expectation':{'typical':i},'actual_test_circuit':'R1 P 0 1','contract':{'fixed':{} }} for i in range(10)]
  c={'shared_evidence':{'tests':rows},'results':[{'test':str(i)} for i in range(10)],'cases':[{'id':str(i)} for i in range(10)]}
  n=compact('model_optimizer',c);self.assertNotIn('results',n);self.assertNotIn('cases',n);self.assertEqual(len(n['shared_evidence']['tests']),10)
  self.assertEqual([r['expectation'] for r in n['shared_evidence']['tests']],[r['expectation'] for r in rows]);self.assertNotEqual(n['focus_test_ids'],compact('model_optimizer',{**c,'master_cycle':1})['focus_test_ids']);self.assertIn('contract',c['shared_evidence']['tests'][0])
 def test_oversize_never_spends_or_calls_transport(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(d,{},dict(api_calls=2,simulations=0,repairs=0,seconds=10));transport=MagicMock();a=Agents(s,{'design':{'provider':'qwen','model':'fake'}},transport)
   with self.assertRaises(Fault) as ex:a.ask('test_designer',{'text':'x'*100000})
   self.assertEqual(ex.exception.kind,'request_context');transport.assert_not_called();self.assertEqual(s.data['usage']['api_calls'],0)
if __name__=='__main__':unittest.main()
