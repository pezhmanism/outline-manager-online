"""Loaded by the original panel before its server startup."""
import sqlite3, shutil, math

VERSION = '2.0.0'
if STATE.exists() and not (D/'state-before-v2.json').exists():
    shutil.copy2(STATE, D/'state-before-v2.json')
state['server_name'] = 'Outline Manager Online'
save()
state.setdefault('default_limit', None)
db = sqlite3.connect(str(D/'usage.sqlite3'), check_same_thread=False)
db.execute('PRAGMA journal_mode=WAL')
db.execute('CREATE TABLE IF NOT EXISTS usage(ts INTEGER, uid TEXT, up REAL, down REAL)')
db.execute('CREATE INDEX IF NOT EXISTS usage_idx ON usage(uid,ts)')
db.commit()
previous, recent = {}, set()
applied_keys = set()
metrics_ok, sampled_at = False, 0

def usage(uid=None):
    query = 'SELECT COALESCE(SUM(up),0),COALESCE(SUM(down),0) FROM usage WHERE ts>?'
    args = [int(time.time()-30*86400)]
    if uid is not None:
        query += ' AND uid=?'
        args.append(uid)
    return db.execute(query, args).fetchone()

def limit(u):
    return u.get('limit', state['default_limit'])

def allowed(u):
    cap = limit(u)
    return u['enabled'] and not u.get('archived') and (cap is None or sum(usage(u['id'])) < cap)

def parse_metrics(raw):
    result = {}
    for line in raw.splitlines():
        if line.startswith('#') or '{' not in line:
            continue
        metric, rest = line.split('{', 1)
        labels, value = rest.split('}', 1)
        labels = tuple(sorted(re.findall(r'(\w+)="([^"\n]*)"', labels)))
        if not dict(labels).get('access_key'):
            continue
        try:
            number = float(value.strip().split()[0])
            if math.isfinite(number) and number >= 0:
                result[(metric, labels)] = number
        except (ValueError, IndexError):
            continue
    return result

def collect():
    global metrics_ok, sampled_at, recent
    try:
        with urllib.request.urlopen('http://127.0.0.1:19090/metrics', timeout=3) as response:
            current = parse_metrics(response.read().decode())
        now, active = int(time.time()), set()
        for ident, value in current.items():
            metric, labels = ident
            labels = dict(labels)
            delta = value-previous.get(ident, 0)
            if delta < 0:
                delta = value
            uid, direction = labels['access_key'], labels.get('dir')
            if metric == 'shadowsocks_data_bytes' and direction in ('p>t', 'c<p') and delta:
                db.execute('INSERT INTO usage VALUES(?,?,?,?)', (now, uid, delta if direction=='p>t' else 0, delta if direction=='c<p' else 0))
            if metric == 'shadowsocks_tunnel_time_seconds' and delta > 0.1:
                active.add(uid)
        previous.clear()
        previous.update(current)
        recent, sampled_at, metrics_ok = active, now, True
        db.execute('DELETE FROM usage WHERE ts<?', (now-31*86400,))
        db.commit()
    except Exception:
        metrics_ok = False

base_cfg = cfg
def cfg():
    config = base_cfg()
    config['services'][0]['keys'] = [dict(id=u['id'], cipher='chacha20-ietf-poly1305', secret=u['secret']) for u in state['users'] if allowed(u)]
    return config

def restart():
    global proc, applied_keys
    with lock:
        if proc and proc.poll() is None:
            collect()
        save()
        (D/'config.json').write_text(json.dumps(cfg()))
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        previous.clear()
        proc = subprocess.Popen([str(D/'outline-ss-server'), '-config', str(D/'config.json'), '-metrics', '127.0.0.1:19090'])
        applied_keys = {u['id'] for u in state['users'] if allowed(u)}

def monitor():
    while True:
        time.sleep(10)
        with lock:
            collect()
            if applied_keys != {u['id'] for u in state['users'] if allowed(u)} or (proc and proc.poll() is not None):
                restart()

E = lambda value: html.escape(str(value), quote=True)
def human(number):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if number < 1024:
            return f'{number:.1f} {unit}'
        number /= 1024
    return f'{number:.1f} PiB'

def gib(raw):
    number = float(raw)
    if not math.isfinite(number) or not 0 <= number <= 1_000_000:
        raise ValueError('Limit must be between 0 and 1000000 GiB')
    return int(number*1073741824)

def form(path, body):
    return '<form method="post" action="'+path+'">'+body+'</form>'

def action(label, path, uid):
    return form(path, '<input type="hidden" name="id" value="'+E(uid)+'"><button>'+E(label)+'</button>')

def access_link(u):
    return 'ssconf://'+DOMAIN+'/k/'+u['token']+'#'+urllib.parse.quote(u['name'])

CSS = '<style>body{max-width:1150px}header,.row,.stats,nav{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.stats article{flex:1;min-width:140px}nav{padding:20px 0}input,select{max-width:90%;background:#111e31;color:#e5ecf5;border:1px solid #4e6680}summary{cursor:pointer;padding:10px 0}small,.muted{color:#b4c5db}label{display:inline-block;margin:8px}.qr{width:190px;background:white;padding:12px;border-radius:12px}.pill{padding:6px 12px;background:#314a65;border-radius:20px}progress{width:100%}table{width:100%;text-align:left}td,th{padding:10px;border-bottom:1px solid #314158}code{background:#101827;padding:12px}</style>'
SCRIPT = '''<script>function copyKey(id){const v=document.getElementById(id).textContent;const t=document.createElement('textarea');t.value=v;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();alert('Copied');}</script>'''
BaseH = H
class H(BaseH):
    def page(self, msg=''):
        if not state['password'] or not self.auth():
            return super().page(msg)
        up, down = usage()
        s = STYLE+CSS+SCRIPT+'<header><h1>'+E(state['server_name'])+'</h1><span class="pill">Panel '+VERSION+'</span></header><nav><a href="#keys">Access keys</a><a href="#usage">Data usage</a><a href="#settings">Settings</a><a href="/backup">Download backup</a></nav>'
        stats = [('SERVER', 'Running' if proc and proc.poll() is None else 'Stopped'), ('ENABLED KEYS', sum(allowed(u) for u in state['users'])), ('30-DAY TRANSFER', human(up+down)), ('RECENTLY ACTIVE KEYS', len(recent))]
        s += '<div class="stats">'+''.join('<article><small>'+name+'</small><h2>'+str(value)+'</h2></article>' for name,value in stats)+'</div>'
        s += '<p class="muted">Traffic history starts with this upgrade. Samples every 10 seconds. TCP bytes appear when connections close: statistics and limits can lag long downloads. Active keys are not a device count. Key changes briefly reconnect users.</p><h2 id="keys">Access keys</h2>'
        s += form('/add', '<input name="name" maxlength="60" placeholder="Family member name" required><button>Create key</button>')
        for u in state['users']:
            if u.get('archived'):
                continue
            uid, cap = u['id'], limit(u)
            a,b = usage(uid)
            status = 'Enabled' if allowed(u) else ('Disabled' if not u['enabled'] else 'Data limit reached')
            s += '<article><header><h2>'+E(u['name'])+'</h2><span class="pill">'+status+'</span></header><small>Key ID: '+uid+'</small><p>30-day transfer: <b>'+human(a+b)+'</b> · Upload '+human(a)+' · Download '+human(b)+'</p>'
            if cap is not None:
                s += '<progress value="'+str(min(a+b,cap))+'" max="'+str(max(1,cap))+'"></progress><p>Limit: '+human(cap)+' / rolling 30 days</p>'
            else:
                s += '<p>Limit: Unlimited</p>'
            if u['enabled']:
                s += '<details><summary>Share · access link and QR</summary><code id="key-'+uid+'">'+E(access_link(u))+'</code><button type="button" onclick="copyKey(\'key-'+uid+'\')">Copy access key</button><p>Import into Outline Client 1.15 or later.</p><img class="qr" alt="Outline access QR code" src="/qr/'+uid+'"></details>'
            mode = 'default' if 'limit' not in u else ('unlimited' if u['limit'] is None else 'custom')
            options = ''.join('<option value="'+v+'"'+(' selected' if mode==v else '')+'>'+label+'</option>' for v,label in [('default','Use server default'),('custom','Custom limit'),('unlimited','Unlimited')])
            value = str(u['limit']/1073741824) if u.get('limit') is not None else ''
            s += '<details><summary>Edit name and data limit</summary>'+form('/edit','<input type="hidden" name="id" value="'+uid+'"><label>Name <input name="name" value="'+E(u['name'])+'" maxlength="60" required></label><select name="mode">'+options+'</select><label>Limit GiB <input name="limit" type="number" min="0" step="0.01" value="'+value+'"></label><button>Save key</button>')+'</details><div class="row">'+action('Disable access' if u['enabled'] else 'Enable access','/toggle',uid)+action('Archive key','/archive',uid)+'</div></article>'
        archived = [u for u in state['users'] if u.get('archived')]
        if archived:
            s += '<details><summary>Archived keys</summary>'+''.join('<article>'+E(u['name'])+action('Restore (disabled)','/restore',u['id'])+'</article>' for u in archived)+'</details>'
        rows = db.execute("SELECT date(ts,'unixepoch'),SUM(up),SUM(down) FROM usage WHERE ts>? GROUP BY 1 ORDER BY 1 DESC", (int(time.time()-30*86400),)).fetchall()
        s += '<h2 id="usage">Daily transfer · last 30 days (UTC)</h2><table><tr><th>Date</th><th>Upload</th><th>Download</th></tr>'+''.join('<tr><td>'+day+'</td><td>'+human(a)+'</td><td>'+human(b)+'</td></tr>' for day,a,b in rows)+('</table>' if rows else '<tr><td>No traffic measured yet.</td></tr></table>')
        default = '' if state['default_limit'] is None else str(state['default_limit']/1073741824)
        s += '<h2 id="settings">Server settings</h2><article>'+form('/settings','<label>Server name <input name="name" value="'+E(state['server_name'])+'" maxlength="80" required></label><label>Default limit per key (GiB) <input type="number" name="limit" min="0" step="0.01" value="'+default+'" placeholder="Unlimited"></label><button>Save settings</button>')+'<p>Blank default = unlimited. Each key has its own rolling 30-day allowance.</p><p>Hostname: '+DOMAIN+'<br>Public port: 443 / WSS<br>Outline engine: 1.9.2 · Panel: '+VERSION+'<br>Metrics: '+('Collecting' if metrics_ok else 'Waiting / unavailable')+'</p><p>This WebSocket deployment shares port 443; changing per-key ports is not applicable. Routes remain in Cloudflare. No anonymous usage reporting is enabled.</p><p>Archive revokes access and keeps a recoverable record. Backup includes access credentials; keep it private.</p></article>'+form('/logout','<button>Sign out</button>')
        return s

    def do_GET(self):
        if self.path not in ('/backup','/api/status') and not self.path.startswith('/qr/'):
            return super().do_GET()
        with lock:
            if self.server.server_port!=9000 or not self.auth():
                return self.send(403,'Sign in through the local panel.','text/plain')
            if self.path=='/api/status':
                return self.send(200,json.dumps(dict(version=VERSION,metrics_ok=metrics_ok,sampled_at=sampled_at,keys=len(state['users']))),'application/json')
            if self.path=='/backup':
                return self.send(200,json.dumps(dict(version=VERSION,state=state,exported_at=int(time.time())),indent=2),'application/octet-stream')
            u = next((u for u in state['users'] if u['id']==self.path[4:] and not u.get('archived')),None)
            if not u:
                return self.send(404,'Not found','text/plain')
            try:
                import qrcode, qrcode.image.svg
                image=qrcode.make(access_link(u),image_factory=qrcode.image.svg.SvgPathImage)
                return self.send(200,image.to_string().decode(),'image/svg+xml')
            except ImportError:
                return self.send(503,'QR unavailable; copy the access link instead.','text/plain')

    def do_POST(self):
        if self.path not in ('/edit','/settings','/archive','/restore'):
            return super().do_POST()
        if self.server.server_port!=9000 or not self.auth():
            return self.send(403,'Sign in through the local panel.','text/plain')
        if self.headers.get('Host') not in ('localhost:18082','localhost:18082'):
            return self.send(403,'Unexpected host','text/plain')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:
                return self.send(413,'Invalid form','text/plain')
            fields=parse_qs(self.rfile.read(size).decode(),keep_blank_values=True)
            v=lambda k:fields.get(k,[''])[0]
            if not hmac.compare_digest(v('csrf'),csrf):
                return self.send(403,'Form expired. Return to the home page.','text/plain')
            with lock:
                if self.path=='/settings':
                    name=v('name').strip()[:80]
                    cap=gib(v('limit')) if v('limit').strip() else None
                    if not name:
                        raise ValueError('Name is required')
                    state['server_name'],state['default_limit']=name,cap
                else:
                    u=next((u for u in state['users'] if u['id']==v('id')),None)
                    if not u:
                        return self.send(404,'Unknown key','text/plain')
                    if self.path=='/edit':
                        name=v('name').strip()[:60]
                        mode=v('mode')
                        cap=gib(v('limit')) if mode=='custom' else None
                        if not name or mode not in ('default','custom','unlimited'):
                            raise ValueError('Invalid key settings')
                        u['name']=name
                        if mode=='default':
                            u.pop('limit',None)
                        else:
                            u['limit']=cap
                    elif self.path=='/archive':
                        u['archived'],u['enabled']=True,False
                    else:
                        u['archived'],u['enabled']=False,False
                restart()
                return self.send(200,self.page())
        except ValueError as error:
            return self.send(400,STYLE+'<p>'+E(error)+'</p><a href="/">Back to panel</a>')

threading.Thread(target=monitor,daemon=True).start()

# Version 3: local and Cloudflare-hosted administration, account settings and UI.
from http.cookies import SimpleCookie
VERSION = '3.0.0'
ADMIN_HOST = os.environ.get('ADMIN_HOST', 'panel.example.com').lower()
LOCAL_ADMIN_HOST = os.environ.get('LOCAL_ADMIN_HOST', 'localhost:18082').lower()
state.setdefault('admin_email', os.environ.get('ADMIN_EMAIL', 'admin@example.com'))
save()
login_attempts = {}
MODERN = '''<style>
:root{color-scheme:light;--bg:#f5f7fb;--ink:#17233c;--muted:#69758b;--line:#e3e9f3;--accent:#3468ed}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink);max-width:1160px;margin:32px auto;padding:20px}
h1,h2{letter-spacing:-.035em;line-height:1.25}h1{font-size:30px}h2{font-size:21px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{justify-content:space-between}nav{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 20px;margin:20px 0;gap:22px}nav a{font-weight:600;font-size:14px}
article{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 4px 22px #243c6b05;padding:24px;margin:16px 0}.stats{gap:16px}.stats article{padding:20px}.stats h2{font-size:29px;margin:8px 0 0}.stats small{font-size:11px;letter-spacing:.09em;font-weight:700}
small,.muted{color:var(--muted)}input,select{font:inherit;max-width:100%;background:#fff;color:var(--ink);padding:11px 13px;border:1px solid #d4ddec;border-radius:10px;margin:6px 0}input:focus,select:focus{outline:3px solid #3468ed20;border-color:var(--accent)}button{font:600 14px system-ui;border:0;background:var(--accent);color:white;padding:12px 17px;border-radius:10px;cursor:pointer;margin:6px 4px 6px 0}button:hover{filter:brightness(.94)}label{display:block;margin:12px 0}label input{display:block;width:100%}details{border-top:1px solid var(--line);margin-top:14px;padding-top:6px}summary{font-weight:600;cursor:pointer}code{background:#f4f7fd;color:#334767;border-radius:10px;font-size:13px;overflow-wrap:anywhere;white-space:normal}td,th{border-bottom:1px solid var(--line);padding:12px}th{color:var(--muted);font-size:12px}.pill{background:#eaf1ff;color:#3468ed;font-size:12px;font-weight:700;border-radius:20px}progress{accent-color:var(--accent)}.qr{width:190px;padding:10px;background:white;border:1px solid var(--line)}.account-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.account-grid form{min-width:0}.login{max-width:430px;margin:8vh auto;background:white;padding:36px;border:1px solid var(--line);border-radius:24px;box-shadow:0 20px 70px #243c6b0a}.login input{width:100%;margin-bottom:14px}.login button{width:100%}.eyebrow{color:var(--accent);font-size:12px;font-weight:750;letter-spacing:.12em;text-transform:uppercase}.notice{padding:12px 16px;border-radius:10px;background:#eef3ff;color:#37527a}.error{color:#b53238;background:#fff0f0;padding:10px 14px;border-radius:10px}
@media(max-width:650px){body{padding:14px;margin:10px auto}.stats article{min-width:40%;margin:0}.stats h2{font-size:23px}article{padding:18px}.account-grid{grid-template-columns:1fr;gap:8px}.login{padding:26px;margin:5vh auto}nav{gap:14px}h1{font-size:25px}.row form{flex:1}.row button{width:100%}}
</style>'''
H2 = H
class H(H2):
    def external(self):
        return getattr(self,'_external',False) or self.headers.get('Host','').lower()==ADMIN_HOST

    def redirect(self,cookie=None,path='/'):
        self.send_response(303)
        self.send_header('Location',path)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length','0')
        if cookie:
            self.send_header('Set-Cookie',cookie+('; Secure' if self.external() else ''))
        self.end_headers()

    def send(self,code,text,typ='text/html; charset=utf-8',cookie=None):
        # POST/Redirect/GET prevents duplicate key creation on refresh.
        if code==200 and self.command=='POST' and self.path in ('/add','/toggle','/edit','/settings','/archive','/restore'):
            return self.redirect()
        if cookie and self.external() and 'Secure' not in cookie:
            cookie+='; Secure'
        if typ.startswith('text/html'):
            text=text.replace('<a href="#','<a href="/#')
            if '<html' not in text:
                text='<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Outline Manager Online</title></head><body>'+text+'</body></html>'
        return super().send(code,text,typ,cookie)

    def page(self,msg=''):
        if not state['password']:
            return super().page(msg)+MODERN
        if not self.auth():
            message='<p class="error">'+E(msg)+'</p>' if msg else ''
            return STYLE+MODERN+'<main class="login"><div class="eyebrow">Outline Manager Online</div><h1>Welcome back</h1><p class="muted">Sign in to manage your family\'s access.</p>'+message+form('/login','<label>Email<input type="email" name="email" autocomplete="username" value="'+E(state['admin_email'])+'" required></label><label>Password<input type="password" name="password" autocomplete="current-password" required></label><button>Sign in</button>')+'<p class="muted">Outline Manager Online · '+VERSION+'</p></main>'
        page=super().page(msg)+MODERN
        page=page.replace('<a href="/backup">','<a href="/#account">Account</a><a href="/backup">')
        account='<h2 id="account">Your account</h2><article><p class="muted">Remote administration: https://'+ADMIN_HOST+'</p><div class="account-grid"><section><h2>Sign-in email</h2>'+form('/account-email','<label>Email<input type="email" name="email" value="'+E(state['admin_email'])+'" autocomplete="username" required></label><label>Current password<input type="password" name="current_password" autocomplete="current-password" required></label><button>Update email</button>')+'</section><section><h2>Change password</h2>'+form('/account-password','<label>Current password<input type="password" name="current_password" autocomplete="current-password" required></label><label>New password<input type="password" name="new_password" minlength="8" autocomplete="new-password" required></label><label>Confirm new password<input type="password" name="confirm_password" minlength="8" autocomplete="new-password" required></label><button>Change password</button>')+'<p class="muted">At least 8 characters. Updating your account signs out all sessions.</p></section></div></article>'
        return page+account

    def do_GET(self):
        if self.server.server_port==9000 and self.path=='/login':
            return self.redirect()
        if self.server.server_port==9000 and self.path in ('/add','/edit','/toggle','/archive','/restore','/settings','/account-password','/account-email'):
            return self.redirect()
        return super().do_GET()

    def do_POST(self):
        if self.server.server_port!=9000:
            return super().do_POST()
        host=self.headers.get('Host','').lower()
        if host not in (LOCAL_ADMIN_HOST,'localhost:18082','127.0.0.1:18082',ADMIN_HOST):
            return self.send(403,'Unexpected host','text/plain')
        self._external=host==ADMIN_HOST
        if self.external() and self.headers.get('X-Forwarded-Proto','').lower()!='https':
            return self.send(403,'Use HTTPS for remote administration.','text/plain')
        if self.path not in ('/login','/logout','/account-password','/account-email'):
            # Older handlers enforce the local Host; the external host has already
            # passed the explicit allowlist above. CSRF is still checked by them.
            self.headers.replace_header('Host','localhost:18082')
            return super().do_POST()
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:
                return self.send(413,'Invalid form','text/plain')
            fields=parse_qs(self.rfile.read(size).decode(),keep_blank_values=True)
            v=lambda k:fields.get(k,[''])[0]
            if not hmac.compare_digest(v('csrf'),csrf):
                return self.send(403,self.page('Form expired. Please try again.'))
            with lock:
                peer=self.headers.get('CF-Connecting-IP','') if self.external() else self.client_address[0]
                now=time.time()
                attempts=[t for t in login_attempts.get(peer,[]) if now-t<900]
                if len(attempts)>=8:
                    return self.send(429,self.page('Too many attempts. Try again in 15 minutes.'))
                if self.path=='/login':
                    valid=hmac.compare_digest(v('email').strip().lower(),state['admin_email'].lower())
                    valid=hmac.compare_digest(digest(v('password')),state['password']) and valid
                    if not valid:
                        attempts.append(now);login_attempts[peer]=attempts
                        time.sleep(1)
                        return self.send(403,self.page('Email or password is incorrect.'))
                    login_attempts.pop(peer,None)
                    sid=secrets.token_urlsafe(40);sessions[sid]=now+3600
                    return self.redirect('sid='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
                if not self.auth():
                    return self.send(403,self.page('Please sign in.'))
                if self.path=='/logout':
                    c=SimpleCookie();c.load(self.headers.get('Cookie',''))
                    if 'sid' in c:sessions.pop(c['sid'].value,None)
                    return self.redirect('sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if not hmac.compare_digest(digest(v('current_password')),state['password']):
                    attempts.append(now);login_attempts[peer]=attempts
                    return self.send(403,STYLE+MODERN+'<main class="login"><h1>Account update failed</h1><p>Current password is incorrect.</p><a href="/#account">Return to account</a></main>')
                if self.path=='/account-password':
                    new=v('new_password')
                    if len(new)<8 or new!=v('confirm_password'):
                        raise ValueError('Use at least 8 characters and matching new passwords.')
                    state['password']=digest(new)
                else:
                    email=v('email').strip().lower()
                    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:
                        raise ValueError('Enter a valid email address.')
                    state['admin_email']=email
                save();sessions.clear();login_attempts.pop(peer,None)
                return self.redirect('sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        except ValueError as error:
            return self.send(400,STYLE+MODERN+'<main class="login"><h1>Check your details</h1><p>'+E(error)+'</p><a href="/#account">Return to account</a></main>')

# Branding assets are public; account data and access keys remain private.
HBrandBase = H
class H(HBrandBase):
    def send(self,code,text,typ='text/html; charset=utf-8',cookie=None):
        if typ.startswith('text/html'):
            brand_host=globals().get('ADMIN_HOST','')
            tags='<link rel="icon" type="image/svg+xml" sizes="any" href="/assets/favicon.svg?v=2"><link rel="apple-touch-icon" href="/assets/thumbnail.png"><meta property="og:title" content="Outline Manager Online"><meta property="og:image" content="https://'+E(brand_host)+'/assets/thumbnail.png">'
            if '<head>' in text:text=text.replace('<head>','<head>'+tags,1)
            else:text=tags+text
        return super().send(code,text,typ,cookie)

    def page(self,msg=''):
        content=super().page(msg)
        content=content.replace('<h1>','<h1 class="brand-heading"><img src="/assets/logo.svg" alt="Outline Manager Online" width="44" height="44">',1)
        content+='<style>.brand-heading{display:flex;align-items:center;gap:14px}.brand-heading img{flex:none;border-radius:10px}.login .brand-heading{align-items:center}</style>'
        return content

    def do_GET(self):
        assets={'/favicon.ico':('favicon.svg','image/svg+xml'),'/assets/favicon.svg':('favicon.svg','image/svg+xml'),'/assets/logo.svg':('logo.svg','image/svg+xml'),'/assets/thumbnail.png':('thumbnail.png','image/png')}
        if self.path.split('?',1)[0] in assets:
            if self.server.server_port!=9000:return self.send(404,'Not found','text/plain')
            name,mime=assets[self.path.split('?',1)[0]]
            path=D/name
            if not path.exists():path=Path(__file__).with_name('assets')/name
            if not path.exists():return self.send(404,'Not found','text/plain')
            data=path.read_bytes();self.send_response(200)
            self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','public, max-age=3600');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(data);return
        return super().do_GET()
