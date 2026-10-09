import io,json,tempfile,unittest,zipfile
from pathlib import Path
from web_ui.server import unpack,safe_task
from web_ui.export import export
class WebTests(unittest.TestCase):
 def archive(self,name,body='{}'):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr(name,body)
  return b.getvalue()
 def test_archive_path_escape(self):
  with tempfile.TemporaryDirectory() as d:
   for name in ('../task.json','/task.json','C:/task.json','x\\task.json'):
    with self.assertRaises(ValueError):unpack(self.archive(name),Path(d))
 def test_standard_package_and_references(self):
  with tempfile.TemporaryDirectory() as d:
   p=unpack(self.archive('device/task.json'),Path(d));self.assertTrue(p.exists())
   safe_task({'model':{'path':'models/c.lib'}},p.parent)
   for path in ('../secret','/etc/passwd','C:/secret'):
    with self.assertRaises(ValueError):safe_task({'model':{'path':path}},p.parent)
 def test_csv_model_status_and_bundle(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'runtime';(root/'models').mkdir(parents=True)
   (root/'models/input_model.lib').write_text('* original');(root/'engineering_candidate.lib').write_text('* original')
   (root/'summary.json').write_text(json.dumps({'workflow_status':'stopped_with_evidence','model':str(root/'engineering_candidate.lib'),'results':[{'test':'a','execution':'completed','acceptance':'pending','value':4,'unit':'V'}]}))
   meta=export(root,{'cases':[{'id':'a','expectation':{'typical':5}}]})
   self.assertFalse(meta['model']['changed_from_input']);self.assertEqual(meta['workflow_status'],'stopped_with_evidence')
   out=root.parent/'exports';self.assertIn('-20.0',(out/'measurements.csv').read_text())
   with zipfile.ZipFile(out/'results.zip') as z:self.assertIn('candidate.lib',z.namelist())
