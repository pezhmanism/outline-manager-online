"""Authorization, mutation semantics and secret handling without Telegram calls."""
import ast
import hashlib
import hmac
import html
import io
import json
import math
import re
import secrets
import tempfile
import threading
import time
import unittest
import urllib.request
from urllib.parse import parse_qs
from pathlib import Path
from unittest.mock import Mock


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        state = dict(password='hash', server_name='Demo', default_limit=None, users=[])
        self.restart = Mock()
        self.env = dict(D=Path(self.temp.name), Path=Path, json=json, secrets=secrets,
                        urllib=urllib, time=time, threading=threading, re=re, hmac=hmac, parse_qs=parse_qs,
                        state=state, lock=threading.RLock(), restart=self.restart,
                        H=type('Base', (), {}), E=lambda x: html.escape(str(x), quote=True),
                        usage=lambda uid=None: (10, 20), limit=lambda u: u.get('limit', state['default_limit']),
                        allowed=lambda u: u['enabled'] and not u.get('archived'),
                        human=lambda n: str(n), gib=lambda s: self.gib(s),
                        access_link=lambda u: 'ssconf://vpn.example.com/k/'+u['token'],
                        ADMIN_HOST='panel.example.com', LOCAL_ADMIN_HOST='localhost:18082',
                        DOMAIN='vpn.example.com', VERSION='test', proc=None, metrics_ok=True,
                        sampled_at=0, recent=set())
        source = ast.parse(Path(__file__).with_name('telegram_bot.py').read_text(encoding='utf-8'))
        source.body = source.body[:-1]  # Worker thread belongs to the running service.
        exec(compile(source, 'telegram_bot.py', 'exec'), self.env)
        self.bot = self.env['TelegramManager'](dict(owner_id=123, token='not-a-real-token', enabled=True))
        self.bot.api = Mock(return_value=True)

    def tearDown(self):
        self.temp.cleanup()

    def gib(self, value):
        n = float(value)
        if not math.isfinite(n) or not 0 <= n <= 1_000_000: raise ValueError()
        return int(n*1073741824)

    def update(self, text, sender=123, chat=123, kind='private'):
        return dict(message=dict(date=int(time.time()), text=text,
                    chat=dict(id=chat, type=kind), **{'from': dict(id=sender)}))

    def create(self):
        self.bot.handle(self.update('/new'))
        self.bot.handle(self.update('Alice'))
        return self.env['state']['users'][0]

    def test_unauthorized_and_group_updates_are_silent(self):
        for sender, chat, kind in [(99,99,'private'),(123,-1,'group'),(99,123,'private')]:
            self.bot.handle(self.update('/new',sender,chat,kind))
        self.bot.api.assert_not_called()
        self.assertIsNone(self.bot.pending)
        self.assertFalse(self.env['state']['users'])

    def test_callback_checks_sender_and_private_chat(self):
        self.bot.handle({'callback_query': {'id':'x','from':{'id':99},
            'message':{'chat':{'id':123,'type':'private'}},'data':'new'}})
        self.bot.api.assert_not_called()

    def test_create_does_not_send_credentials_until_requested(self):
        u = self.create()
        self.assertTrue(u['enabled'])
        self.assertGreater(len(u['secret']), 30)
        payloads = repr(self.bot.api.call_args_list)
        self.assertNotIn(u['secret'], payloads)
        self.assertNotIn(u['token'], payloads)
        self.assertEqual(self.restart.call_count, 1)

    def test_limit_and_default_and_invalid_values(self):
        u = self.create()
        self.bot.prompt('limit',u['id']); self.bot.input('2.5')
        self.assertEqual(u['limit'], int(2.5*1073741824))
        self.bot.prompt('limit',u['id']); self.bot.input('NaN')
        self.assertEqual(u['limit'], int(2.5*1073741824))
        self.bot.input('unlimited'); self.assertIsNone(u['limit'])
        self.bot.prompt('limit',u['id']); self.bot.input('default')
        self.assertNotIn('limit',u)

    def test_archive_requires_single_use_confirmation(self):
        u = self.create()
        self.bot.dispatch('ask:archive:'+u['id'])
        self.assertFalse(u.get('archived'))
        token = next(iter(self.bot.confirmations))
        self.bot.dispatch('confirm:'+token)
        self.assertTrue(u['archived']); self.assertFalse(u['enabled'])
        before = self.restart.call_count
        self.bot.dispatch('confirm:'+token)
        self.assertEqual(before, self.restart.call_count)
        self.bot.confirm('restore',u['id'])
        self.bot.execute(next(iter(self.bot.confirmations)))
        self.assertFalse(u['archived']); self.assertFalse(u['enabled'])

    def test_expired_confirmation_and_cancel(self):
        u = self.create()
        self.bot.confirmations['old'] = ('archive',u['id'],time.time()-1)
        self.bot.execute('old'); self.assertFalse(u.get('archived'))
        self.bot.confirm('archive',u['id']); self.bot.dispatch('/cancel')
        self.assertFalse(self.bot.confirmations)

    def test_old_messages_do_not_mutate(self):
        update = self.update('/new'); update['message']['date'] = int(time.time())-1000
        self.bot.handle(update)
        self.assertIsNone(self.bot.pending)

    def test_config_missing_owner_fails_closed(self):
        self.env['tg_write'](self.env['TG_CONFIG'],dict(token='test',enabled=True))
        self.assertEqual(self.env['tg_load'](),{})

    def test_qr_is_a_png_and_share_is_protected(self):
        u = self.create(); self.bot.api.reset_mock()
        self.bot.share(u['id'])
        calls = self.bot.api.call_args_list
        self.assertEqual(calls[0].args[0], 'sendMessage')
        self.assertTrue(calls[0].args[1]['protect_content'])
        self.assertTrue(calls[-1].args[2][-1].startswith(b'\x89PNG\r\n\x1a\n'))

    def test_backup_is_not_sent_without_confirmation(self):
        self.bot.dispatch('backup')
        self.assertFalse(any(c.args[0]=='sendDocument' for c in self.bot.api.call_args_list))

    def test_settings_never_render_saved_token(self):
        secret = '123456:THIS_IS_A_PRIVATE_BOT_TOKEN_123456'
        self.env['tg_write'](self.env['TG_CONFIG'],dict(token=secret, owner_id=123, enabled=True))
        base = self.env['TelegramBaseH']
        base.page = lambda self, msg='': '<a href="/backup">Backup</a>'
        base.auth = lambda self: True
        self.env['form'] = lambda path, fields: '<form>'+fields+'</form>'
        handler = self.env['H']()
        page = handler.page()
        self.assertNotIn(secret, page)
        self.assertIn('123', page)
        self.assertIn('type="password"', page)

    def test_settings_reject_untrusted_host(self):
        handler = self.env['H']()
        handler.path = '/telegram-settings'
        handler.server = type('Server',(),{'server_port':9000})()
        handler.auth = lambda: True
        handler.headers = {'Host':'attacker.example'}
        handler.send = Mock()
        handler.do_POST()
        self.assertEqual(handler.send.call_args.args[0],403)
        self.assertFalse(self.env['TG_CONFIG'].exists())

    def test_remote_settings_save_on_legacy_deployment_without_local_host(self):
        self.env.pop('LOCAL_ADMIN_HOST')
        self.env['csrf'] = 'test-csrf'
        handler = self.env['H']()
        handler.path = '/telegram-settings'
        handler.server = type('Server',(),{'server_port':9000})()
        handler.auth = lambda: True
        body = b'token=123456%3Aabcdefghijklmnopqrstuvwxyz123456&owner_id=123&enabled=1&csrf=test-csrf'
        handler.headers = {'Host':'panel.example.com','X-Forwarded-Proto':'https','Content-Length':str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.redirect = Mock()
        handler.send = Mock()
        handler.do_POST()
        handler.send.assert_not_called()
        handler.redirect.assert_called_once_with(path='/#telegram')
        self.assertEqual(self.env['tg_load']()['owner_id'],123)
        self.assertTrue(self.env['tg_load']()['enabled'])


if __name__ == '__main__':
    unittest.main()
