import json
import unittest
from unittest.mock import patch, MagicMock
from flow_runtime.agents import Agents
from flow_runtime.state import Fault

class ThirdPartyTests(unittest.TestCase):
    def routes(self):
        return {r:dict(provider=p,model=m,base_url='https://llm.goaichat.top/v1',key_env='SPICE_API_KEY') for r,p,m in [('design','qwen','qwen3.8-max'),('review','glm','glm-5.3')]}
    def test_shared_hidden_key_and_generic_payload(self):
        store=MagicMock();store.remaining_seconds.return_value=100
        agent=Agents(store,self.routes())
        with patch.dict('os.environ',{},clear=True),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value='fake-test-key') as prompt:
            agent.credentials(['model_optimizer','patch_reviewer'])
            self.assertEqual(prompt.call_count,1)
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
        with patch('urllib.request.urlopen',return_value=response) as send:
            for role in ['model_optimizer','patch_reviewer']:
                agent._http(role,agent.route(role),{},200)
                req=send.call_args.args[0];payload=json.loads(req.data)
                self.assertEqual(req.full_url,'https://llm.goaichat.top/v1/chat/completions')
                self.assertEqual(req.get_header('Authorization'),'Bearer fake-test-key')
                self.assertNotIn('thinking',payload);self.assertNotIn('enable_thinking',payload)
        self.assertNotIn('fake-test-key',agent._redact('fake-test-key'))
    def test_endpoint_rejects_embedded_credentials(self):
        for base in ['http://example.com/v1','https://key@example.com/v1','https://example.com/v1?key=secret']:
            with self.assertRaises(Fault):Agents.endpoint(dict(provider='qwen',base_url=base))
    def test_official_route_unchanged(self):
        self.assertEqual(Agents.credential_id(dict(provider='qwen')),'qwen')
        self.assertIn('dashscope',Agents.endpoint(dict(provider='qwen')))
