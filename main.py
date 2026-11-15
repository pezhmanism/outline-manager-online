import os,json,secrets,hashlib,hmac,time,threading,subprocess,urllib.request,tarfile,io,html,signal,re
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import parse_qs
os.umask(0o077)
D=Path(os.environ.get('DATA_DIR','/data')); D.mkdir(parents=True,exist_ok=True)
STATE=D/'state.json'
state=json.loads(STATE.read_text()) if STATE.exists() else {'users':[],'password':None,'salt':secrets.token_hex(32),'path':secrets.token_urlsafe(32)}
lock=threading.RLock(); sessions={}; proc=None; csrf=secrets.token_urlsafe(40)
DOMAIN=os.environ.get('VPN_HOST','vpn.example.com').lower()
def save():
 p=D/'state.tmp'; p.write_text(json.dumps(state)); p.replace(STATE)
def cfg():
 return {'web':{'servers':[{'id':'web','listen':['0.0.0.0:18081']}]},'services':[{'listeners':[{'type':'websocket-stream','web_server':'web','path':'/'+state['path']+'/tcp'},{'type':'websocket-packet','web_server':'web','path':'/'+state['path']+'/udp'}],'keys':[{'id':u['id'],'cipher':'chacha20-ietf-poly1305','secret':u['secret']} for u in state['users'] if u['enabled']]}]}
def restart():
 global proc
 with lock:
  save(); (D/'config.json').write_text(json.dumps(cfg()))
  if proc and proc.poll() is None:
   proc.terminate()
   try: proc.wait(5)
   except subprocess.TimeoutExpired: proc.kill(); proc.wait()
  proc=subprocess.Popen([str(D/'outline-ss-server'),'-config',str(D/'config.json')])
def download():
 binary=D/'outline-ss-server'
 if binary.exists(): return
 base='https://github.com/OutlineFoundation/tunnel-server/releases/download/v1.9.2/'
 name='outline-ss-server_1.9.2_linux_x86_64.tar.gz'
 def get(n):
  with urllib.request.urlopen(base+n,timeout=90) as r:return r.read()
 data=get(name); checks=get('checksums.txt').decode()
 expected=next(line.split()[0] for line in checks.splitlines() if line.split()[-1].lstrip('*')==name)
 if not hmac.compare_digest(hashlib.sha256(data).hexdigest(),expected):raise RuntimeError('Download checksum mismatch')
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t:
  m=next(m for m in t.getmembers() if m.isfile() and Path(m.name).name=='outline-ss-server')
  binary.write_bytes(t.extractfile(m).read());binary.chmod(0o700)
def digest(p):return hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(state['salt']),310000).hex()
def key(u):
 return {'transport':{'$type':'tcpudp',**{kind:{'$type':'shadowsocks','endpoint':{'$type':'websocket','url':'wss://'+DOMAIN+'/'+state['path']+'/'+kind},'cipher':'chacha20-ietf-poly1305','secret':u['secret']} for kind in ('tcp','udp')}}}
STYLE='<meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;max-width:850px;margin:40px auto;padding:20px;background:#101827;color:#e5ecf5}input,button{font:inherit;padding:10px;margin:5px;border-radius:8px}article{background:#1d2a40;padding:18px;margin:16px 0;border-radius:14px}code{display:block;overflow-wrap:anywhere;margin:12px 0}button{cursor:pointer;background:#91caff;border:0}a{color:#91caff}</style>'
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def send(self,code,text,typ='text/html; charset=utf-8',cookie=None):
  if typ.startswith('text/html'):text=re.sub(r'(<form\b[^>]*>)',lambda m:m.group(1)+'<input type="hidden" name="csrf" value="'+csrf+'">',text)
  b=text.encode();self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer')
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(b)
 def auth(self):
  c=next((x.strip()[4:] for x in self.headers.get('Cookie','').split(';') if x.strip().startswith('sid=')),None)
  return sessions.get(c,0)>time.time()
 def page(self,msg=''):
  if not state['password']:
   return STYLE+'<h1>Outline Family</h1><h2>Create your admin password</h2><p>Local administration only. Choose at least 8 characters.</p><form method="post" action="/setup"><input type="password" name="password" minlength="8" required autocomplete="new-password"><button>Set password</button></form>'+html.escape(msg)
  if not self.auth():return STYLE+'<h1>Outline Family</h1><form method="post" action="/login"><input type="password" name="password" required autocomplete="current-password"><button>Sign in</button></form>'+html.escape(msg)
  s=STYLE+'<h1>Outline Family</h1><p>Server: '+('Running' if proc and proc.poll() is None else 'Stopped')+' � '+DOMAIN+'</p><p>Adding or changing access briefly reconnects active users.</p><form method="post" action="/add"><input name="name" maxlength="60" placeholder="Family member name" required><button>Create key</button></form>'
  for u in state['users']:
   link='ssconf://'+DOMAIN+'/k/'+u['token']+'#'+urllib.parse.quote(u['name'])
   s+='<article><b>'+html.escape(u['name'])+'</b> � '+('Enabled' if u['enabled'] else 'Disabled')
   if u['enabled']:s+='<code>'+html.escape(link)+'</code><p>Copy this complete link into the Outline app (v1.15+).</p>'
   s+='<form method="post" action="/toggle"><input type="hidden" name="id" value="'+u['id']+'"><button>'+('Disable access' if u['enabled'] else 'Enable access')+'</button></form></article>'
  return s+'<form method="post" action="/logout"><button>Sign out</button></form>'
 def do_GET(self):
  with lock:
   if self.path.startswith('/k/'):
    token=self.path.split('?',1)[0][3:]
    u=next((u for u in state['users'] if u['enabled'] and hmac.compare_digest(u['token'],token)),None)
    if u:return self.send(200,json.dumps(key(u)),'application/json')
    return self.send(404,'Not found','text/plain')
   if self.server.server_port==8000:return self.send(404,'Not found','text/plain')
   if self.path!='/':return self.send(404,'Not found','text/plain')
   self.send(200,self.page())
 def do_POST(self):
  if self.server.server_port!=9000:return self.send(404,'Not found','text/plain')
  host=self.headers.get('Host','');origin=self.headers.get('Origin','')
  if host not in ('localhost:18082','localhost:18082'):return self.send(403,'Unexpected host: '+host,'text/plain')
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size>4096:return self.send(413,'Too large','text/plain')
   fields=parse_qs(self.rfile.read(size).decode()); val=lambda k:fields.get(k,[''])[0]
   if not hmac.compare_digest(val('csrf'),csrf):return self.send(403,'Form expired. Open the panel home page and try again.','text/plain')
   with lock:
    if self.path=='/setup' and not state['password']:
     if len(val('password'))<8:return self.send(400,self.page('Use at least 8 characters.'))
     state['password']=digest(val('password'));save();return self.send(200,self.page())
    if self.path=='/login':
     time.sleep(1)
     if state['password'] and hmac.compare_digest(state['password'],digest(val('password'))):
      sid=secrets.token_urlsafe(40);sessions[sid]=time.time()+3600
      return self.send(200,STYLE+'<p>Signed in. <a href="/">Open panel</a></p>',cookie='sid='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
     return self.send(403,self.page('Incorrect password.'))
    if not self.auth():return self.send(403,self.page())
    if self.path=='/add':
     name=val('name').strip()[:60]
     if not name:return self.send(400,self.page())
     state['users'].append({'id':secrets.token_hex(8),'name':name,'secret':secrets.token_urlsafe(32),'token':secrets.token_urlsafe(40),'enabled':True});restart()
    elif self.path=='/toggle':
     u=next((u for u in state['users'] if u['id']==val('id')),None)
     if not u:return self.send(404,'Not found','text/plain')
     u['enabled']=not u['enabled'];restart()
    elif self.path=='/logout':return self.send(200,STYLE+'<a href="/">Signed out</a>',cookie='sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
    else:return self.send(404,'Not found','text/plain')
    return self.send(200,self.page())
  except Exception as e:
   print(type(e).__name__,flush=True);self.send(500,'Operation failed; inspect local service logs.','text/plain')
exec(compile(Path(__file__).with_name('upgrade.py').read_text(), 'upgrade.py', 'exec'))
print('Downloading and verifying official Outline v1.9.2',flush=True)
download()
restart()
threading.Thread(target=ThreadingHTTPServer(('0.0.0.0',8000),H).serve_forever,daemon=True).start()
print('Local panel ready on port 9000; create your password in the browser.',flush=True)
ThreadingHTTPServer(('0.0.0.0',9000),H).serve_forever()
