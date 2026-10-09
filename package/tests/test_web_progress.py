import tempfile,unittest,json
from pathlib import Path
from web_ui.progress import snapshot
from flow_runtime.stage_labels import message
class ProgressTests(unittest.TestCase):
 def test_new_files_and_changes_visible(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);(r/'runtime/models').mkdir(parents=True);p=r/'runtime/models/candidate.lib';p.write_text('one')
   a=snapshot(r);self.assertEqual(a['files'][0]['stage'],'SPICE模型文件')
   p.write_text('changed model');b=snapshot(r);self.assertNotEqual(a['files'][0]['size'],b['files'][0]['size'])
   (r/'runtime/secret.key').write_text('not exposed');self.assertEqual(len(snapshot(r)['files']),1)
 def test_chinese_lifecycle(self):
  self.assertIn('测试电路构建】开始',message('circuit_build','running'))
  self.assertIn('回退',message('model_patch','rolled_back'))
