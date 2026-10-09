from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parent
manifest=json.loads((root/'manifest.json').read_text())
for name,expected in manifest['files'].items():
 path=(root/name).resolve()
 if not path.is_relative_to(root):raise SystemExit('非法文件路径: '+name)
 if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise SystemExit('输入文件校验失败: '+name)
print('UC2845输入包哈希校验通过；文件数：'+str(len(manifest['files'])))
