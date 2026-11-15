import ast, io, json, time, secrets, hmac, hashlib, re, unittest
from email.message import Message
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

source=ast.parse(Path(__file__).with_name('upgrade.py').read_text())
handler_class=[n for n in source.body if isinstance(n,ast.ClassDef)][-1]

class Base:
    def send(self,code,text,typ='text/html',cookie=None):self.result=(code,text,cookie)
    def auth(self):return self.logged_in
    def page(self,msg=''):return msg
    def do_POST(self):self.delegated=True
    def send_response(self,code):self.status=code
    def send_header(self,k,v):self.response_headers[k]=v
    def end_headers(self):pass

ns=dict(H2=Base,SimpleCookie=SimpleCookie,hmac=hmac,time=time,secrets=secrets,re=re,parse_qs=parse_qs,
        E=str,STYLE='',MODERN='',VERSION='3.0.0',ADMIN_HOST='panel.example.com',LOCAL_ADMIN_HOST='localhost:18082',save=lambda:None)
exec(compile(ast.Module(body=[handler_class],type_ignores=[]),'account','exec'),ns)
H=ns['H']

class Tests(unittest.TestCase):
    def setUp(self):
        self.saved=[]
        ns.update(state={'admin_email':'owner@example.com','password':'oldpassword'},csrf='csrf',sessions={'old':time.time()+100},login_attempts={},digest=lambda p:p,save=lambda:self.saved.append(True),lock=__import__('threading').RLock())
    def request(self,path,form,host='localhost:18082',logged_in=False):
        h=H.__new__(H);h.path=path;h.command='POST';h.server=SimpleNamespace(server_port=9000)
        h.client_address=('192.168.1.20',123);h.headers=Message()
        payload=urlencode(dict(csrf='csrf',**form)).encode()
        h.headers['Host']=host;h.headers['Content-Length']=str(len(payload));h.headers['X-Forwarded-Proto']='https'
        h.rfile=io.BytesIO(payload);h.response_headers={};h.logged_in=logged_in;h.result=None
        h.do_POST();return h
    def test_email_login_direct_redirect(self):
        h=self.request('/login',{'email':'OWNER@example.com','password':'oldpassword'})
        self.assertEqual(h.status,303);self.assertEqual(h.response_headers['Location'],'/')
        self.assertIn('HttpOnly',h.response_headers['Set-Cookie'])
    def test_remote_login_secure_cookie(self):
        h=self.request('/login',{'email':'owner@example.com','password':'oldpassword'},host='panel.example.com')
        self.assertIn('Secure',h.response_headers['Set-Cookie'])
    def test_password_change_requires_current_and_invalidates_sessions(self):
        h=self.request('/account-password',{'current_password':'oldpassword','new_password':'newpassword','confirm_password':'newpassword'},logged_in=True)
        self.assertEqual(ns['state']['password'],'newpassword');self.assertEqual(ns['sessions'],{});self.assertEqual(h.status,303)
    def test_wrong_current_password_does_not_change(self):
        h=self.request('/account-password',{'current_password':'wrong','new_password':'newpassword','confirm_password':'newpassword'},logged_in=True)
        self.assertEqual(ns['state']['password'],'oldpassword');self.assertEqual(h.result[0],403)
    def test_new_password_mismatch_rejected(self):
        h=self.request('/account-password',{'current_password':'oldpassword','new_password':'newpassword','confirm_password':'mismatch'},logged_in=True)
        self.assertEqual(h.result[0],400);self.assertEqual(ns['state']['password'],'oldpassword')
    def test_unknown_host_rejected(self):
        h=self.request('/login',{'email':'owner@example.com','password':'oldpassword'},host='evil.example')
        self.assertEqual(h.result[0],403)
    def test_delegation_keeps_external_cookie_context(self):
        h=self.request('/add',{'name':'test'},host='panel.example.com',logged_in=True)
        self.assertTrue(h.delegated);self.assertTrue(h.external());self.assertEqual(h.headers['Host'],'localhost:18082')

if __name__ == '__main__':
    unittest.main()
