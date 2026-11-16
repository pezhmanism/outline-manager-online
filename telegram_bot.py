"""Private Telegram administration. Credentials stay in /data/telegram.json."""
import io
import struct
import zlib
from urllib.error import HTTPError

TG_CONFIG = D / 'telegram.json'
TG_OFFSET = D / 'telegram-offset.json'
tg_health = {'status': 'Disabled', 'username': ''}


def tg_load():
    try:
        value = json.loads(TG_CONFIG.read_text())
        if not isinstance(value.get('owner_id'), int) or value['owner_id'] <= 0:
            return {}
        return value
    except (OSError, ValueError, TypeError):
        return {}


def tg_write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.chmod(0o600)
    temp.replace(path)


class TelegramManager:
    def __init__(self, config):
        self.config = config
        self.pending = None
        self.confirmations = {}

    def api(self, method, payload=None, attachment=None):
        if attachment:
            boundary = secrets.token_hex(24)
            body = bytearray()
            for name, value in (payload or {}).items():
                body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+str(value)+'\r\n').encode())
            field, filename, mime, data = attachment
            body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+field+'"; filename="'+filename+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode())
            body.extend(data)
            body.extend(('\r\n--'+boundary+'--\r\n').encode())
            content_type = 'multipart/form-data; boundary='+boundary
        else:
            body = json.dumps(payload or {}).encode()
            content_type = 'application/json'
        req = urllib.request.Request('https://api.telegram.org/bot'+self.config['token']+'/'+method,
                                     data=bytes(body), headers={'Content-Type': content_type})
        with urllib.request.urlopen(req, timeout=40) as response:
            result = json.loads(response.read())
        if not result.get('ok'):
            raise RuntimeError('Telegram request failed')
        return result['result']

    def send(self, text, rows=None):
        payload = {'chat_id': self.config['owner_id'], 'text': text,
                   'parse_mode': 'HTML', 'protect_content': True}
        if rows:
            payload['reply_markup'] = {'inline_keyboard': [
                [dict(text=label, **({'url': action[4:]} if action.startswith('url:') else {'callback_data': action}))
                 for label, action in row] for row in rows]}
        return self.api('sendMessage', payload)

    def menu(self):
        return self.send('<b>Outline Manager Online</b>\nمدیریت خصوصی سرور', [
            [('وضعیت و مصرف', 'status'), ('کلیدها', 'list:0')],
            [('ساخت کلید', 'new'), ('بایگانی', 'archived:0')],
            [('مصرف روزانه', 'daily'), ('تنظیمات سرور', 'settings')],
            [('پشتیبان', 'backup'), ('حساب و رمز عبور', 'url:https://'+ADMIN_HOST+'/#account')],
            [('راه‌اندازی دوبارهٔ موتور', 'ask:restart:server')]])

    def keys(self, archived=False, page=0):
        with lock:
            users = [u for u in state['users'] if bool(u.get('archived')) == archived]
            page = max(0, min(page, max(0, (len(users)-1)//8)))
            rows = [[(u['name'][:45], 'key:'+u['id'])] for u in users[page*8:page*8+8]]
        nav = []
        prefix = 'archived:' if archived else 'list:'
        if page: nav.append(('قبلی', prefix+str(page-1)))
        if len(users) > (page+1)*8: nav.append(('بعدی', prefix+str(page+1)))
        if nav: rows.append(nav)
        rows.append([('منوی اصلی', 'menu')])
        return self.send(('کلیدهای بایگانی‌شده' if archived else 'کلیدها')+' · '+str(len(users)), rows)

    def key(self, uid):
        with lock:
            u = next((u for u in state['users'] if u['id'] == uid), None)
            if not u: return self.send('این کلید پیدا نشد.')
            up, down = usage(uid)
            cap = limit(u)
            status = 'بایگانی' if u.get('archived') else ('فعال' if allowed(u) else 'غیرفعال یا سقف مصرف پر شده')
            text = '<b>'+E(u['name'])+'</b>\n'+status+'\nشناسه: <code>'+uid+'</code>\nمصرف ۳۰ روز: '+human(up+down)+'\nآپلود: '+human(up)+' · دانلود: '+human(down)+'\nسقف: '+('نامحدود' if cap is None else human(cap))
            if u.get('archived'):
                rows = [[('بازیابی به حالت غیرفعال', 'ask:restore:'+uid)]]
            else:
                rows = [[('لینک و QR', 'share:'+uid), ('تغییر نام', 'rename:'+uid)],
                        [('محدودیت حجم', 'limit:'+uid), ('قطع دسترسی' if u['enabled'] else 'فعال‌سازی', 'ask:toggle:'+uid)],
                        [('حذف از فهرست / بایگانی', 'ask:archive:'+uid)]]
            rows.append([('منوی اصلی', 'menu')])
        return self.send(text, rows)

    def prompt(self, action, uid=None):
        self.pending = (action, uid, time.time()+300)
        messages = {'new': 'نام کلید جدید را بفرست.', 'rename': 'نام جدید را بفرست.',
                    'limit': 'سقف مصرف ۳۰ روزه را به GiB بفرست؛ برای نامحدود unlimited و برای پیش‌فرض default بنویس.',
                    'default': 'سقف پیش‌فرض هر کلید را به GiB بفرست؛ برای نامحدود unlimited بنویس.',
                    'server': 'نام جدید سرور را بفرست.'}
        return self.send(messages[action]+'\nلغو: /cancel')

    def input(self, text):
        if not self.pending or self.pending[2] < time.time():
            self.pending = None
            return self.menu()
        action, uid, expiry = self.pending
        try:
            name = text.strip()
            if action in ('new', 'rename', 'server') and not 1 <= len(name) <= (80 if action == 'server' else 60):
                raise ValueError('نام باید بین ۱ تا ۶۰ نویسه باشد.')
            if action in ('limit', 'default'):
                cap = None if name.lower() in ('unlimited', 'نامحدود') else ('default' if name.lower() == 'default' and action == 'limit' else gib(name))
            with lock:
                u = next((u for u in state['users'] if u['id'] == uid), None) if uid else None
                if uid and (not u or u.get('archived')):
                    self.pending = None
                    return self.send('کلید در دسترس نیست؛ فهرست را دوباره باز کن.')
                if action == 'new':
                    u = {'id': secrets.token_hex(8), 'name': name, 'secret': secrets.token_urlsafe(32),
                         'token': secrets.token_urlsafe(40), 'enabled': True}
                    state['users'].append(u)
                elif action == 'rename': u['name'] = name
                elif action == 'server': state['server_name'] = name
                elif action == 'default': state['default_limit'] = cap
                elif action == 'limit':
                    if cap == 'default': u.pop('limit', None)
                    else: u['limit'] = cap
                restart()
            self.pending = None
            self.send('ذخیره شد.')
            return self.key(u['id']) if u else self.settings()
        except (ValueError, OverflowError):
            return self.send('ورودی معتبر نیست؛ نام مناسب یا حجم بین ۰ تا ۱۰۰۰۰۰۰ GiB بفرست. لغو: /cancel')

    def status(self):
        with lock:
            up, down = usage()
            text = '<b>'+E(state['server_name'])+'</b>\nسرور: '+('روشن' if proc and proc.poll() is None else 'متوقف')+'\nموتور: 1.9.2 · پنل: '+VERSION+'\nاندازه‌گیری: '+('فعال' if metrics_ok else 'در دسترس نیست')+'\nآخرین نمونه: '+(time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(sampled_at)) if sampled_at else '—')+'\nکلیدهای فعال: '+str(sum(allowed(u) for u in state['users']))+'\nکلیدهای اخیراً فعال: '+str(len(recent))+'\nمصرف ۳۰ روز: '+human(up+down)+'\nآپلود: '+human(up)+' · دانلود: '+human(down)
        return self.send(text, [[('تازه‌سازی', 'status'), ('منوی اصلی', 'menu')]])

    def settings(self):
        with lock:
            cap = state['default_limit']
            text = 'نام سرور: '+E(state['server_name'])+'\nمحدودیت پیش‌فرض هر کلید: '+('نامحدود' if cap is None else human(cap))+'\nدامنه: '+E(DOMAIN)+'\nپورت عمومی: 443 / WSS'
        return self.send(text, [[('نام سرور', 'server'), ('سقف پیش‌فرض', 'default')], [('منوی اصلی', 'menu')]])

    def confirm(self, action, uid=''):
        token = secrets.token_hex(8)
        self.confirmations = {k:v for k,v in self.confirmations.items() if v[2] > time.time()}
        self.confirmations[token] = (action, uid, time.time()+120)
        return self.send('این عملیات را تأیید می‌کنی؟\nحذف به معنی بایگانی و قطع دسترسی است و قابل بازیابی می‌ماند.' if action == 'archive' else 'این عملیات را تأیید می‌کنی؟',
                         [[('تأیید', 'confirm:'+token), ('لغو', 'menu')]])

    def execute(self, token):
        value = self.confirmations.pop(token, None)
        if not value or value[2] < time.time(): return self.send('تأیید منقضی شده؛ دوباره انتخاب کن.')
        action, uid, _ = value
        if action == 'restart':
            restart()
            return self.status()
        if action == 'backup':
            with lock: data = json.dumps(dict(version=VERSION, state=state, exported_at=int(time.time())), ensure_ascii=False, indent=2).encode()
            return self.api('sendDocument', {'chat_id': self.config['owner_id'], 'protect_content': 'true',
                            'caption': 'پشتیبان شامل کلیدهای دسترسی است؛ آن را خصوصی نگه دار. مصرف SQLite داخل این فایل نیست.'},
                            ('document', 'outline-manager-online-backup.json', 'application/json', data))
        with lock:
            u = next((u for u in state['users'] if u['id'] == uid), None)
            if not u: return self.send('کلید پیدا نشد.')
            if action == 'archive': u['archived'], u['enabled'] = True, False
            elif action == 'restore': u['archived'], u['enabled'] = False, False
            elif action == 'toggle' and not u.get('archived'): u['enabled'] = not u['enabled']
            else: return self.send('عملیات برای وضعیت فعلی کلید معتبر نیست.')
            restart()
        return self.key(uid)

    def share(self, uid):
        with lock:
            u = next((u for u in state['users'] if u['id'] == uid), None)
            if not u or u.get('archived') or not u['enabled']: return self.send('ابتدا کلید را فعال کن.')
            link, name = access_link(u), u['name']
        self.send('<b>'+E(name)+'</b>\n<code>'+E(link)+'</code>\nلینک را با لمس متن کپی کن؛ این لینک رمز دسترسی است.')
        import qrcode
        qr = qrcode.QRCode(box_size=5, border=4)
        qr.add_data(link); qr.make(fit=True)
        matrix = qr.get_matrix(); scale = 5; size = len(matrix)*scale
        raw = b''.join(b'\0'+b''.join((b'\0' if cell else b'\xff')*scale for cell in row) for row in matrix for _ in range(scale))
        def chunk(kind, data): return struct.pack('!I', len(data))+kind+data+struct.pack('!I', zlib.crc32(kind+data)&0xffffffff)
        png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 0, 0, 0, 0))+chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b'')
        return self.api('sendPhoto', {'chat_id': self.config['owner_id'], 'protect_content': 'true'}, ('photo', 'access.png', 'image/png', png))

    def dispatch(self, action):
        if action in ('menu', '/start', '/help', '/cancel'):
            self.pending = None
            if action in ('menu', '/cancel'): self.confirmations.clear()
            return self.menu()
        if action in ('status', '/status'): return self.status()
        if action in ('new', '/new'): return self.prompt('new')
        if action in ('/keys',): return self.keys()
        if action in ('settings', '/settings'): return self.settings()
        if action == 'backup': return self.confirm('backup')
        if action in ('server', 'default'): return self.prompt(action)
        if action == 'daily':
            with lock:
                rows = db.execute("SELECT date(ts,'unixepoch'),SUM(up),SUM(down) FROM usage WHERE ts>? GROUP BY 1 ORDER BY 1 DESC", (int(time.time()-30*86400),)).fetchall()
            return self.send('مصرف روزانه · UTC\n'+'\n'.join(day+' · ↑ '+human(up)+' · ↓ '+human(down) for day,up,down in rows) if rows else 'هنوز مصرفی ثبت نشده است.')
        parts = action.split(':')
        if len(parts) == 2:
            kind, value = parts
            if kind in ('list', 'archived') and value.isdigit(): return self.keys(kind == 'archived', int(value))
            if kind == 'key': return self.key(value)
            if kind == 'share': return self.share(value)
            if kind in ('rename', 'limit'): return self.prompt(kind, value)
            if kind == 'confirm': return self.execute(value)
        if len(parts) == 3 and parts[0] == 'ask' and parts[1] in ('archive', 'restore', 'toggle', 'restart'):
            return self.confirm(parts[1], parts[2])
        return self.send('از دکمه‌های منو استفاده کن. /start')

    def handle(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        sender = callback.get('from', {}) if callback else message.get('from', {})
        chat = message.get('chat', {})
        if sender.get('id') != self.config['owner_id'] or chat.get('id') != self.config['owner_id'] or chat.get('type') != 'private' or sender.get('is_bot'):
            return
        # Ignore queued commands older than five minutes after downtime.
        if not callback and message.get('date', 0) < time.time()-300: return
        if callback:
            self.api('answerCallbackQuery', {'callback_query_id': callback['id']})
            return self.dispatch(callback.get('data', ''))
        text = message.get('text', '')
        if text.startswith('/'):
            return self.dispatch(text.split()[0].split('@')[0])
        return self.input(text)


def telegram_loop():
    manager, current, offset = None, None, None
    while True:
        try:
            config = tg_load()
            if not config.get('enabled'):
                tg_health.update(status='Disabled', username='')
                time.sleep(3); continue
            if config != current:
                manager = TelegramManager(config)
                bot = manager.api('getMe')
                if manager.api('getWebhookInfo').get('url'):
                    tg_health.update(status='Webhook already configured; use a dedicated bot', username='')
                    time.sleep(10); continue
                tg_health.update(status='Connected', username=bot.get('username', ''))
                current = config
                offset = None
                if TG_OFFSET.exists():
                    record = json.loads(TG_OFFSET.read_text())
                    if record.get('bot_id') == bot['id']: offset = record.get('offset')
                # Do not execute old queued commands on first activation.
                if offset is None:
                    updates = manager.api('getUpdates', {'offset': -1, 'timeout': 0, 'allowed_updates': ['message','callback_query']})
                    offset = updates[-1]['update_id']+1 if updates else 0
                bot_id = bot['id']
            updates = manager.api('getUpdates', {'offset': offset, 'timeout': 25, 'allowed_updates': ['message','callback_query']})
            if config != tg_load(): continue
            for update in updates:
                offset = update['update_id']+1
                # Acknowledge locally before mutations: a crash must not create duplicate keys.
                tg_write(TG_OFFSET, dict(bot_id=bot_id, offset=offset))
                try: manager.handle(update)
                except Exception:
                    try: manager.send('عملیات کامل نشد؛ وضعیت کلید را قبل از تلاش مجدد بررسی کن.')
                    except Exception: pass
            tg_health['status'] = 'Connected'
        except Exception:
            # Never log API URLs or exception strings: they can contain the bot token.
            tg_health['status'] = 'Connection error; retrying'
            current = None
            time.sleep(5)


TelegramBaseH = H
class H(TelegramBaseH):
    def page(self, msg=''):
        page = super().page(msg)
        if not state.get('password') or not self.auth(): return page
        config = tg_load()
        page = page.replace('<a href="/backup">', '<a href="/#telegram">Telegram</a><a href="/backup">')
        fields = '<label>Bot token<input type="password" name="token" autocomplete="off" placeholder="Leave blank to keep the current token"></label><label>Your numeric Telegram user ID<input name="owner_id" inputmode="numeric" value="'+E(config.get('owner_id',''))+'" required></label><label><input type="checkbox" name="enabled" value="1"'+(' checked' if config.get('enabled') else '')+'> Enable private Telegram management</label><button>Save Telegram settings</button>'
        return page+'<h2 id="telegram">Telegram bot</h2><article><p>Status: '+E(tg_health['status'])+'</p><p>Bot: '+E(tg_health['username'] or 'Not connected')+'</p>'+form('/telegram-settings', fields)+'<p>Only the specified numeric account ID can use this bot, in a private chat. Keys and requested backups sent through the bot are stored in Telegram chats. Bot chats are not end-to-end encrypted. Password and email changes remain in the secure web account page.</p><p>Use a dedicated BotFather bot. No inbound port or Cloudflare route is needed. Disabling here stops polling within about 40 seconds.</p></article>'

    def do_POST(self):
        if self.path != '/telegram-settings': return super().do_POST()
        host = self.headers.get('Host','').lower()
        if self.server.server_port != 9000 or not self.auth() or host not in (LOCAL_ADMIN_HOST,'localhost:18082','127.0.0.1:18082',ADMIN_HOST):
            return self.send(403, 'Forbidden', 'text/plain')
        if host == ADMIN_HOST and self.headers.get('X-Forwarded-Proto','').lower() != 'https':
            return self.send(403, 'Use HTTPS', 'text/plain')
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 4096: raise ValueError()
            fields = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
            v = lambda k: fields.get(k,[''])[0]
            if not hmac.compare_digest(v('csrf'), csrf): return self.send(403, self.page('Form expired.'))
            config = tg_load()
            token = v('token').strip() or config.get('token','')
            owner = int(v('owner_id'))
            enabled = v('enabled') == '1'
            if owner <= 0 or not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', token): raise ValueError()
            tg_write(TG_CONFIG, dict(token=token, owner_id=owner, enabled=enabled))
            return self.redirect(path='/#telegram')
        except (ValueError, UnicodeError):
            return self.send(400, self.page('Enter a valid BotFather token and numeric Telegram user ID.'))


threading.Thread(target=telegram_loop, daemon=True).start()
