"""Local-only, token-authenticated stdlib web UI. No shell execution of uploaded files."""
import argparse, io, json, os, secrets, signal, subprocess, sys, threading, time, zipfile
from pathlib import Path, PurePosixPath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from .export import export,inside
ENGINE=Path(__file__).resolve().parents[1]

def unpack(blob,dest):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        infos=z.infolist()
        if len(infos)>5000 or sum(i.file_size for i in infos)>200_000_000:raise ValueError('ZIP超过5000文件或200MB解压限制')
        for i in infos:
            p=PurePosixPath(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or ':' in i.filename or (i.external_attr>>16)&0o170000==0o120000:raise ValueError('ZIP包含非法路径或符号链接')
        z.extractall(dest)
    tasks=[p for p in dest.rglob('task.json') if '__MACOSX' not in p.parts and 'runs' not in p.parts]
    if len(tasks)!=1:raise ValueError('标准包应包含唯一task.json，请不要打包runs目录')
    return tasks[0]

def safe_task(task,root):
    def walk(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if k=='path' and isinstance(x,str):
                    if Path(x).is_absolute() or ':' in x or '\\' in x:raise ValueError('标准包必须使用相对文件路径')
                    inside(root/x,root)
                walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(task)

class App:
    def __init__(self,root):
        self.root=root;root.mkdir(parents=True,exist_ok=True);self.jobs={};self.lock=threading.Lock();self.token=secrets.token_urlsafe(32)
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():continue
            tasks=list((folder/'input').rglob('task.json'))
            if len(tasks)!=1:continue
            try:
                task=json.loads(tasks[0].read_text());meta=folder/'exports/export_manifest.json'
                exports=json.loads(meta.read_text()) if meta.exists() else None
                log=folder/'console.log'
                self.jobs[folder.name]={'id':folder.name,'device':task.get('device'),'status':exports['workflow_status'] if exports else 'previous_session','task':tasks[0],'folder':folder,'process':None,'exports':exports,'log':log.read_text()[-100000:] if log.exists() else ''}
            except (ValueError,OSError):continue
    def upload(self,blob):
        key=time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3);folder=self.root/key;folder.mkdir();task=unpack(blob,folder/'input')
        data=json.loads(task.read_text());safe_task(data,task.parent)
        self.jobs[key]={'id':key,'device':data.get('device'),'status':'uploaded','task':task,'folder':folder,'process':None,'log':'','exports':None}
        return key
    def start(self,key,config):
        with self.lock:
            if any(j['status'] in ('preflight','running','exporting','stopping') for j in self.jobs.values()):raise ValueError('已有任务运行，请等待结束')
            j=self.jobs[key]
            if j['status'] not in ('uploaded','preflight_completed'):raise ValueError('请重新上传以开始新任务')
            runner=Path(config['runner']).expanduser()
            if not runner.is_file():raise ValueError('LTspice运行器路径不存在')
            task=json.loads(j['task'].read_text());safe_task(task,j['task'].parent)
            task['runner']['argv']=[str(runner),'-b','-ascii','{circuit}']
            task.setdefault('policy',{})['continuous_until_acceptance']=False
            base=config.get('base_url','').strip()
            for role,provider,model in [('design','qwen',config.get('design_model')),('review','glm',config.get('review_model'))]:
                route={'provider':provider,'model':model or ('qwen3.8-max' if role=='design' else 'glm-5.3')}
                if base:route.update(base_url=base,key_env='SPICE_API_KEY' if role=='design' else 'WEB_REVIEW_KEY')
                task['routes'][role]=route
            j['task'].write_text(json.dumps(task,ensure_ascii=False,indent=2));j['status']='preflight'
            env=os.environ.copy();design=config.get('key','');review=config.get('review_key','') or design
            env.update(SPICE_API_KEY=design,WEB_REVIEW_KEY=review,DASHSCOPE_API_KEY=design,GLM_API_KEY=review,PYTHONUNBUFFERED='1')
            threading.Thread(target=self.work,args=(j,env,task,config.get('preflight_only',False),[design,review]),daemon=True).start()
    def work(self,j,env,task,only,keys):
        try:
            code = -1
            for stage in (['preflight'] if only else ['preflight','run']):
                if j['status']=='stopping':break
                j['status']='preflight' if stage=='preflight' else 'running'
                output=j['folder']/('runtime' if stage=='run' else 'preflight')
                kwargs={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
                p=subprocess.Popen([sys.executable,str(ENGINE/'spice_flow.py'),stage,'--task',str(j['task']),'--output',str(output)],cwd=ENGINE,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kwargs);j['process']=p
                for line in iter(p.stdout.readline,b''):
                    text=line.decode('utf-8',errors='replace')
                    for secret in keys:
                        if secret:text=text.replace(secret,'[redacted]')
                    j['log']=(j['log']+text)[-100000:]
                    with (j['folder']/'console.log').open('a',encoding='utf-8') as f:f.write(text)
                code=p.wait();j['process']=None
                if code or j['status']=='stopping':break
            runtime=j['folder']/'runtime'
            if (runtime/'summary.json').exists():
                j['status']='exporting';j['exports']=export(runtime,task);j['status']=j['exports']['workflow_status'] or 'finished'
            else:j['status']='preflight_completed' if only and code==0 else 'stopped_without_report'
        except Exception as e:j['status']='error';j['log']+='\n'+str(e)
        finally:j['process']=None;env.clear();keys.clear()
    def stop(self,key):
        j=self.jobs[key];p=j['process'];j['status']='stopping'
        if p and p.poll() is None:
            if os.name=='nt':p.send_signal(signal.CTRL_BREAK_EVENT)
            else:os.killpg(p.pid,signal.SIGINT)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,body,kind='application/json'):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def authorized(self):
        host=self.headers.get('Host','')
        if host not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):return False
        return self.headers.get('X-Token')==self.server.app.token or parse_qs(urlsplit(self.path).query).get('token')==[self.server.app.token]
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/':return self.send(200,(ENGINE/'web_ui/index.html').read_bytes(),'text/html; charset=utf-8')
        if not self.authorized():return self.send(403,{'error':'需要本地会话令牌'})
        try:
            app=self.server.app
            if path=='/jobs':return self.send(200,[{k:v for k,v in j.items() if k in ('id','device','status','log','exports')} for j in app.jobs.values()])
            if path.startswith('/download/'):
                _,_,key,name=path.split('/',3)
                if not (name.startswith('plots/') and Path(name).suffix in ('.png','.svg','.csv')) and name not in ('results.zip','measurements.csv','candidate.lib','summary.json','export_manifest.json'):raise ValueError('未知输出')
                f=inside(app.jobs[key]['folder']/'exports'/name,app.jobs[key]['folder']/'exports')
                kind='image/png' if f.suffix=='.png' else 'image/svg+xml' if f.suffix=='.svg' else 'application/octet-stream'
                return self.send(200,f.read_bytes(),kind)
            self.send(404,{'error':'未知路径'})
        except Exception as e:self.send(400,{'error':str(e)})
    def do_POST(self):
        if not self.authorized():return self.send(403,{'error':'需要本地会话令牌'})
        try:
            n=int(self.headers.get('Content-Length',0))
            if not 0<n<=50_000_000:raise ValueError('请求大小上限50MB')
            blob=self.rfile.read(n);app=self.server.app;path=urlsplit(self.path).path
            if path=='/upload':
                key=app.upload(blob);task=json.loads(app.jobs[key]['task'].read_text())
                return self.send(200,{'id':key,'routes':task.get('routes',{}),'selection':'indexed' if task.get('template_selection') else 'fixed'})
            data=json.loads(blob)
            if path=='/start':app.start(data['id'],data)
            elif path=='/stop':app.stop(data['id'])
            else:raise ValueError('未知操作')
            self.send(200,{'ok':True})
        except Exception as e:self.send(400,{'error':str(e)})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);p.add_argument('--data',type=Path,default=ENGINE/'web_runs');a=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);server.app=App(a.data.resolve())
    print('Open http://127.0.0.1:%d/#%s'%(server.server_port,server.app.token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:
        for k,j in server.app.jobs.items():
            if j['process']:server.app.stop(k)
    finally:server.server_close()
if __name__=='__main__':main()
