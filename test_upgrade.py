import ast
import math
import re
import sqlite3
import time
import unittest
import urllib.request
from unittest.mock import patch
from pathlib import Path

tree = ast.parse(Path(__file__).with_name('upgrade.py').read_text())
functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('parse_metrics','gib','usage','allowed','limit','collect')], type_ignores=[])
ns = dict(math=math,re=re,time=time)
exec(compile(functions,'upgrade.py','exec'),ns)

class Tests(unittest.TestCase):
    def setUp(self):
        ns['db']=sqlite3.connect(':memory:')
        ns['db'].execute('CREATE TABLE usage(ts INTEGER,uid TEXT,up REAL,down REAL)')
        ns['state']={'default_limit':None}
    def test_metrics_parse(self):
        result=ns['parse_metrics']('# comment\nshadowsocks_data_bytes{dir="p>t",access_key="abc",proto="tcp"} 123\nshadowsocks_data_bytes{dir="c<p",access_key="abc",proto="udp"} 456\nother{access_key=""} 99')
        self.assertEqual(sorted(result.values()),[123,456])
    def test_rolling_window(self):
        ns['db'].executemany('INSERT INTO usage VALUES(?,?,?,?)',[(int(time.time()-31*86400),'a',999,999),(int(time.time()),'a',10,20),(int(time.time()),'b',100,200)])
        self.assertEqual(ns['usage']('a'),(10,20))
        self.assertEqual(ns['usage'](),(110,220))
    def test_limits(self):
        user=dict(id='a',enabled=True)
        self.assertTrue(ns['allowed'](user))
        ns['state']['default_limit']=0
        self.assertFalse(ns['allowed'](user))
        user['limit']=None
        self.assertTrue(ns['allowed'](user))
        user['archived']=True
        self.assertFalse(ns['allowed'](user))
    def test_invalid_limits(self):
        for value in ('nan','inf','-1','1000001'):
            with self.assertRaises(ValueError): ns['gib'](value)
        self.assertEqual(ns['gib']('1'),1073741824)
    def test_counter_deltas_and_restart(self):
        class Response:
            raw=b''
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return self.raw
        reply=Response()
        ns.update(urllib=__import__('urllib'),previous={})
        with patch.object(urllib.request,'urlopen',return_value=reply):
            reply.raw=b'shadowsocks_data_bytes{dir="p>t",access_key="a"} 100'
            ns['collect']()
            ns['collect']()
            self.assertEqual(ns['usage']('a'),(100,0))
            reply.raw=b'shadowsocks_data_bytes{dir="p>t",access_key="a"} 150'
            ns['collect']()
            self.assertEqual(ns['usage']('a'),(150,0))
            reply.raw=b'shadowsocks_data_bytes{dir="p>t",access_key="a"} 20'
            ns['collect']()
            self.assertEqual(ns['usage']('a'),(170,0))

if __name__ == '__main__':
    unittest.main()
