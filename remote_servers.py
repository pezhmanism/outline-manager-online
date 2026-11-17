"""Standard Outline servers, authenticated by the supplied certificate pin."""
import http.client
import ssl
from urllib.parse import urlsplit, quote

REMOTE_FILE = D / 'remote-servers.json'


def remote_load():
    try:
        return json.loads(REMOTE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def remote_save(servers):
    temp = REMOTE_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(servers), encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(REMOTE_FILE)


def remote_validate(raw):
    config = json.loads(raw)
    url = urlsplit(config['apiUrl'])
    pin = config['certSha256'].lower()
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path.strip('/')
            or not re.fullmatch('[a-f0-9]{64}', pin)
            or any(ord(c)<33 for c in config['apiUrl'])):
        raise ValueError('Invalid connection details')
    # Validate port now rather than letting a later request fail.
    if not 1 <= (url.port or 443) <= 65535: raise ValueError('Invalid port')
    return dict(apiUrl=config['apiUrl'].rstrip('/'), certSha256=pin)


def remote_api(server, method, path, payload=None):
    url = urlsplit(server['apiUrl'])
    # Outline uses a self-signed certificate. Authenticate its exact DER digest
    # BEFORE sending the secret API path or any request bytes.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=4, context=context)
    try:
        conn.connect()
        digest = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(digest, server['certSha256']):
            raise ValueError('Certificate fingerprint does not match. No management request was sent.')
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, url.path.rstrip('/')+path, body=body,
                     headers={'Content-Type':'application/json','Connection':'close'})
        response = conn.getresponse()
        data = response.read(2_000_001)
        if len(data)>2_000_000: raise ValueError('Server response is too large')
        if not 200 <= response.status < 300:
            raise ValueError('Outline server returned HTTP '+str(response.status)+'. Refresh before retrying changes.')
        return json.loads(data) if data else {}
    finally:
        conn.close()


def server_picker(selected='local'):
    links = '<a class="server-choice'+(' selected' if selected=='local' else '')+'" href="/">This ZimaOS server</a>'
    for server in remote_load():
        links += '<a class="server-choice'+(' selected' if selected==server['id'] else '')+'" href="/remote?server='+server['id']+'">'+E(server['label'])+'</a>'
    return '<div class="server-picker"><strong>Servers</strong>'+links+'<a class="server-choice" href="/#servers">+ Add server</a></div>'


REMOTE_STYLE = '<style>.server-picker{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:16px 0}.server-choice{padding:8px 12px;border:1px solid var(--line);border-radius:9px;background:white;font-size:13px}.server-choice.selected{background:#eaf1ff;border-color:#3468ed}textarea{width:100%;min-height:120px;border:1px solid var(--line);border-radius:10px;padding:12px;font:13px monospace;background:white;color:var(--ink)}.remote-actions{display:flex;gap:12px;flex-wrap:wrap}.remote-actions form{flex:1;min-width:200px}</style>'


RemoteBaseH = H
class H(RemoteBaseH):
    def page(self, msg=''):
        page = super().page(msg)
        if not state.get('password') or not self.auth(): return page
        page = page.replace('<nav>', server_picker()+'<nav>', 1)
        return page+REMOTE_STYLE+'<details id="servers" class="section-card"><summary>Add a standard Outline server</summary><div class="section-body"><p>Paste the management connection JSON from Outline Manager or the server installer. This is not an ss:// access key.</p>'+form('/servers/add','<label>Display name<input name="label" maxlength="80" required></label><label>Connection JSON<textarea name="connection" autocomplete="off" spellcheck="false" placeholder=\'{"apiUrl":"https://…/secret","certSha256":"…"}\' required></textarea></label><button>Verify and add server</button>')+'<p>Connection details stay on this server and are not shown again. The certificate fingerprint must match. The remote management port must be reachable from ZimaOS.</p></div></details>'

    def remote_page(self, server):
        root = '/remote?server='+server['id']
        page = STYLE+MODERN+REMOTE_STYLE+'<header><h1>Outline Manager Online</h1><a href="/">Local dashboard</a></header>'+server_picker(server['id'])
        try:
            info = remote_api(server,'GET','/server')
            keys = remote_api(server,'GET','/access-keys').get('accessKeys',[])
            try:
                transfer = remote_api(server,'GET','/metrics/transfer').get('bytesTransferredByUserId',{})
                usage_note = 'Transfer reported by this Outline server; it is not the local rolling history.'
            except Exception:
                transfer = {}; usage_note = 'Transfer metrics currently unavailable.'
            page += '<h2>'+E(info.get('name',server['label']))+'</h2><p class="muted">Standard Outline · '+E(info.get('version',''))+' · '+str(len(keys))+' keys</p><p class="muted">'+usage_note+'</p>'
            hidden = '<input type="hidden" name="server" value="'+server['id']+'">'
            page += form('/remote/action',hidden+'<input type="hidden" name="action" value="create"><input name="name" placeholder="New key name" maxlength="60" required><button>Create key</button>')
            for key in keys:
                uid = str(key['id'])
                cap = key.get('dataLimit', info.get('accessKeyDataLimit',{})).get('bytes')
                base = hidden+'<input type="hidden" name="id" value="'+E(uid)+'">'
                page += '<details class="key-card"><summary class="key-summary"><span class="key-name">'+E(key.get('name') or 'Key '+uid)+'</span><span class="key-transfer">'+(human(transfer[uid]) if uid in transfer else '—')+' / '+('Unlimited' if cap is None else human(cap))+'</span><span class="pill">Outline</span></summary><div class="key-body"><p>Key ID: '+E(uid)+' · Port: '+E(key.get('port',''))+'</p><details><summary>Share · access link</summary><code id="remote-'+E(uid)+'">'+E(key.get('accessUrl',''))+'</code><button type="button" data-copy="remote-'+E(uid)+'" onclick="copyKey(this.dataset.copy)">Copy access key</button></details><div class="remote-actions">'
                page += form('/remote/action',base+'<input type="hidden" name="action" value="rename"><label>Name<input name="name" value="'+E(key.get('name',''))+'" maxlength="60" required></label><button>Rename</button>')
                page += form('/remote/action',base+'<input type="hidden" name="action" value="limit"><label>Limit (GiB)<input name="limit" type="number" min="0" max="1000000" step="0.01" value="'+('' if cap is None else str(cap/1073741824))+'" placeholder="Blank removes individual limit"></label><button>Save limit</button>')
                page += '</div><details><summary>Delete key permanently</summary><p>This revokes the key and cannot be restored from this panel.</p>'+form('/remote/action',base+'<input type="hidden" name="action" value="delete"><label><input type="checkbox" name="confirm" value="yes" required> Confirm permanent deletion</label><button>Delete permanently</button>')+'</details></div></details>'
            page += '<details class="section-card"><summary>Remote server settings</summary><div class="section-body">'
            page += form('/remote/action',hidden+'<input type="hidden" name="action" value="server-name"><label>Server name<input name="name" value="'+E(info.get('name',''))+'" maxlength="80" required></label><button>Save server name</button>')
            cap = info.get('accessKeyDataLimit',{}).get('bytes')
            page += form('/remote/action',hidden+'<input type="hidden" name="action" value="default-limit"><label>Default per-key limit (GiB)<input name="limit" type="number" min="0" max="1000000" step="0.01" value="'+('' if cap is None else str(cap/1073741824))+'" placeholder="Blank removes default"></label><button>Save default</button>')+'</div></details>'
        except Exception as error:
            message = str(error) if isinstance(error,ValueError) else 'Cannot reach this server. Verify its address, management port and certificate fingerprint.'
            page += '<p class="error">'+E(message)+'</p><a href="'+root+'">Retry connection</a>'
        page += '<details class="section-card"><summary>Remove from this panel</summary><div class="section-body"><p>This only forgets the connection. The server and its keys keep running.</p>'+form('/servers/remove','<input type="hidden" name="server" value="'+server['id']+'"><button>Forget server connection</button>')+'</div></details>'+SCRIPT
        return page

    def do_GET(self):
        if self.path.split('?',1)[0] != '/remote': return super().do_GET()
        if self.server.server_port != 9000 or not self.auth(): return self.redirect(path='/')
        sid = parse_qs(urlsplit(self.path).query).get('server',[''])[0]
        server = next((s for s in remote_load() if s['id']==sid),None)
        return self.send(200,self.remote_page(server)) if server else self.send(404,'Unknown server','text/plain')

    def do_POST(self):
        if self.path not in ('/servers/add','/servers/remove','/remote/action'): return super().do_POST()
        host = self.headers.get('Host','').lower()
        if self.server.server_port!=9000 or not self.auth() or host not in (globals().get('LOCAL_ADMIN_HOST','localhost:18082'),'localhost:18082','127.0.0.1:18082',ADMIN_HOST):
            return self.send(403,'Forbidden','text/plain')
        if host==ADMIN_HOST and self.headers.get('X-Forwarded-Proto','').lower()!='https':
            return self.send(403,'Use HTTPS','text/plain')
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192: raise ValueError('Invalid form')
            fields = parse_qs(self.rfile.read(size).decode(),keep_blank_values=True)
            v = lambda key: fields.get(key,[''])[0]
            if not hmac.compare_digest(v('csrf'),csrf): raise ValueError('Form expired; reload the page.')
            servers = remote_load()
            if self.path=='/servers/add':
                config = remote_validate(v('connection'))
                label = v('label').strip()
                if not 1<=len(label)<=80: raise ValueError('Enter a display name')
                remote_api(config,'GET','/server')
                config.update(id=secrets.token_hex(8),label=label)
                with lock:
                    servers = remote_load()
                    if any(s['apiUrl']==config['apiUrl'] for s in servers): raise ValueError('This server is already added')
                    servers.append(config); remote_save(servers)
                return self.redirect(path='/remote?server='+config['id'])
            server = next((s for s in servers if s['id']==v('server')),None)
            if not server: raise ValueError('Unknown server')
            if self.path=='/servers/remove':
                with lock: remote_save([s for s in remote_load() if s['id']!=server['id']])
                return self.redirect(path='/#servers')
            action = v('action')
            key_path = '/access-keys/'+quote(v('id'),safe='')
            if action in ('create','rename','server-name'):
                name = v('name').strip()
                if not 1<=len(name)<=80: raise ValueError('Enter a valid name')
                remote_api(server,'POST' if action=='create' else 'PUT', '/access-keys' if action=='create' else ('/name' if action=='server-name' else key_path+'/name'),dict(name=name))
            elif action in ('limit','default-limit'):
                path = '/server/access-key-data-limit' if action=='default-limit' else key_path+'/data-limit'
                cap = gib(v('limit')) if v('limit').strip() else None
                remote_api(server,'DELETE' if cap is None else 'PUT',path, None if cap is None else dict(limit=dict(bytes=cap)))
            elif action=='delete' and v('confirm')=='yes': remote_api(server,'DELETE',key_path)
            else: raise ValueError('Invalid operation')
            return self.redirect(path='/remote?server='+server['id'])
        except Exception as error:
            message = str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else 'Operation did not complete. Check connection details and refresh the server before retrying.'
            # Do not echo connection JSON, response bodies or URLs containing API secrets.
            return self.send(400,STYLE+MODERN+'<p class="error">'+E(message)+'</p><a href="/">Back to dashboard</a>')
