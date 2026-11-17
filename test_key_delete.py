import hmac
import html
import io
import secrets
import threading
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode


class KeyDeletionTests(unittest.TestCase):
    def setUp(self):
        self.env=dict(H=type('Base',(),{}),SimpleCookie=SimpleCookie,hmac=hmac,
            secrets=secrets,time=time,parse_qs=parse_qs,lock=threading.RLock(),
            state={'users':[dict(id='a',name='Alice',secret='PRIVATE'),dict(id='b',name='Bob')]},
            restart=Mock(),db=Mock(),recent={'a','b'},csrf='csrf-test',
            E=lambda v:html.escape(str(v),quote=True),STYLE='',MODERN='',
            ADMIN_HOST='panel.example.com',LOCAL_ADMIN_HOST='localhost:18082',
            form=lambda path,body:'<form>'+body+'</form>')
        exec(compile(Path(__file__).with_name('key_delete.py').read_text(encoding='utf-8'),'key_delete.py','exec'),self.env)

    def post(self, fields, sid='owner', authenticated=True):
        handler=self.env['H']()
        handler.path='/delete-key';handler.server=type('Server',(),{'server_port':9000})()
        body=urlencode(dict(csrf='csrf-test',id='a',**fields)).encode()
        handler.headers={'Host':'panel.example.com','X-Forwarded-Proto':'https','Cookie':'sid='+sid,'Content-Length':str(len(body))}
        handler.rfile=io.BytesIO(body);handler.auth=lambda:authenticated
        handler.send=Mock();handler.redirect=Mock();handler.do_POST()
        return handler

    def test_initial_click_only_requests_confirmation(self):
        handler=self.post({})
        self.assertEqual(len(self.env['state']['users']),2)
        self.env['restart'].assert_not_called()
        self.assertIn('Delete key?',handler.send.call_args.args[1])
        self.assertNotIn('PRIVATE',handler.send.call_args.args[1])

    def test_confirm_removes_only_target_key_and_its_history(self):
        self.post({});token=next(iter(self.env['delete_confirmations']))
        handler=self.post(dict(stage='confirm',confirmation=token,agree='yes'))
        handler.redirect.assert_called_once()
        self.assertEqual([u['id'] for u in self.env['state']['users']],['b'])
        self.env['db'].execute.assert_called_once_with('DELETE FROM usage WHERE uid=?',('a',))
        self.assertEqual(self.env['recent'],{'b'})
        self.assertNotIn(token,self.env['delete_confirmations'])

    def test_cannot_delete_without_confirmation(self):
        handler=self.post(dict(stage='confirm',confirmation='made-up',agree='yes'))
        self.assertEqual(handler.send.call_args.args[0],400)
        self.env['restart'].assert_not_called()

    def test_confirmation_is_bound_to_session(self):
        self.post({});token=next(iter(self.env['delete_confirmations']))
        self.post(dict(stage='confirm',confirmation=token,agree='yes'),sid='other')
        self.env['restart'].assert_not_called()

    def test_unauthenticated_request_is_rejected(self):
        handler=self.post({},authenticated=False)
        self.assertEqual(handler.send.call_args.args[0],403)
        self.assertFalse(self.env['delete_confirmations'])

    def test_expired_confirmation_cannot_delete(self):
        self.env['delete_confirmations']['expired']=('a','owner',0)
        self.post(dict(stage='confirm',confirmation='expired',agree='yes'))
        self.env['restart'].assert_not_called()


if __name__=='__main__':unittest.main()
