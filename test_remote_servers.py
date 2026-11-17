import hashlib
import hmac
import html
import json
import re
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = dict(D=Path(self.temp.name), Path=Path, json=json, re=re,
            hashlib=hashlib, hmac=hmac, secrets=secrets, lock=threading.RLock(),
            H=type('Base',(),{}), E=lambda x: html.escape(str(x),quote=True),
            state={'password':'hash'}, STYLE='', MODERN='', SCRIPT='',
            ADMIN_HOST='panel.example.com', parse_qs=parse_qs,
            form=lambda path,body:'<form method="post" action="'+path+'">'+body+'</form>',
            human=lambda n:str(n))
        exec(compile(Path(__file__).with_name('remote_servers.py').read_text(encoding='utf-8'),'remote_servers.py','exec'),self.env)
        self.config = dict(apiUrl='https://server.example:1234/SECRET', certSha256=hashlib.sha256(b'certificate').hexdigest())

    def tearDown(self): self.temp.cleanup()

    def test_connection_requires_https_pin_and_secret_path(self):
        for config in [dict(self.config,apiUrl='http://server.example/SECRET'),
                       dict(self.config,certSha256='bad'),
                       dict(self.config,apiUrl='https://server.example/'),
                       dict(self.config,apiUrl='https://user:pass@server.example/SECRET'),
                       dict(self.config,apiUrl='https://server.example/SECRET?token=bad')]:
            with self.assertRaises(ValueError): self.env['remote_validate'](json.dumps(config))
        self.assertEqual(self.env['remote_validate'](json.dumps(self.config)),self.config)

    def test_pin_is_checked_before_api_secret_is_sent(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b'wrong certificate'
        with patch('http.client.HTTPSConnection',return_value=connection):
            with self.assertRaises(ValueError): self.env['remote_api'](self.config,'GET','/server')
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_matching_pin_allows_exact_endpoint(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b'certificate'
        response = connection.getresponse.return_value
        response.status = 200; response.read.return_value = b'{"name":"Test"}'
        with patch('http.client.HTTPSConnection',return_value=connection):
            result=self.env['remote_api'](self.config,'GET','/server')
        self.assertEqual(result['name'],'Test')
        self.assertEqual(connection.request.call_args.args[:2],('GET','/SECRET/server'))

    def test_redirects_are_not_followed(self):
        connection = Mock();connection.sock.getpeercert.return_value=b'certificate'
        response=connection.getresponse.return_value;response.status=302;response.read.return_value=b''
        with patch('http.client.HTTPSConnection',return_value=connection):
            with self.assertRaises(ValueError):self.env['remote_api'](self.config,'GET','/server')
        self.assertEqual(connection.request.call_count,1)

    def test_picker_does_not_leak_connection_credentials(self):
        self.env['remote_save']([dict(self.config,id='abc',label='<My server>')])
        picker=self.env['server_picker']()
        self.assertNotIn('SECRET',picker)
        self.assertNotIn(self.config['certSha256'],picker)
        self.assertIn('&lt;My server&gt;',picker)

    def test_remote_keys_are_collapsed_and_delete_needs_confirmation(self):
        self.env['remote_api']=Mock(side_effect=[{'name':'Demo'}, {'accessKeys':[{'id':'0','name':'Alice','accessUrl':'ss://demo'}]}, {'bytesTransferredByUserId':{'0':100}}])
        handler=self.env['H']()
        page=handler.remote_page(dict(self.config,id='abc',label='Demo'))
        self.assertIn('<details class="key-card">',page)
        self.assertNotIn('<details class="key-card" open',page)
        self.assertIn('name="confirm" value="yes" required',page)
        self.assertNotIn('SECRET',page)

    def test_new_endpoints_reject_unauthenticated_posts(self):
        handler=self.env['H']();handler.path='/servers/add'
        handler.headers={'Host':'panel.example.com','X-Forwarded-Proto':'https'}
        handler.server=type('Server',(),{'server_port':9000})()
        handler.auth=lambda:False;handler.send=Mock()
        self.env['remote_api']=Mock()
        handler.do_POST()
        self.assertEqual(handler.send.call_args.args[0],403)
        self.env['remote_api'].assert_not_called()


if __name__=='__main__':unittest.main()
