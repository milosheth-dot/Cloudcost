import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from app import Handler, Store

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.store=Store(Path(cls.tmp.name)/'db.sqlite3')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def request(self,path,body=None,headers=None):
        if body is not None:
            raw=json.dumps(body).encode();h={'Content-Type':'application/json'}
        else:raw=None;h={}
        h.update(headers or {})
        return urlopen(Request(self.url+path,data=raw,headers=h),timeout=3)
    def test_dashboard_assets(self):
        for path in ['/','/app.js','/style.css']:
            with self.request(path) as r:self.assertEqual(r.status,200);self.assertTrue(r.read())
    def test_data_and_simulation(self):
        with self.request('/api/data?days=30') as r:data=json.load(r)
        self.assertEqual(data['summary']['rows'],150)
        with self.request('/api/simulate',{'days':30,'cost_factor':.5}) as r:result=json.load(r)
        self.assertAlmostEqual(result['projected']['cost'],data['summary']['cost']/2)
    def test_import_is_atomic(self):
        text='date,provider,service,region,resource_id,cost_usd,energy_kwh,intensity_g_per_kwh,pue\n2026-01-01,AWS,EC2,test,id,2,1,400,1.2\n'
        with self.request('/api/import',{'name':'test','csv':text}) as r:dataset=json.load(r)['dataset']
        with self.request('/api/data?dataset='+dataset) as r:self.assertEqual(json.load(r)['summary']['cost'],2)
        count=len(self.server.store.list())
        with self.assertRaises(HTTPError) as caught:self.request('/api/import',{'name':'bad','csv':text.replace(',2,1,',',-2,1,')})
        self.assertEqual(caught.exception.code,400)
        self.assertEqual(len(self.server.store.list()),count)
    def test_origin_and_host_protection(self):
        with self.assertRaises(HTTPError) as caught:self.request('/api/simulate',{}, {'Origin':'https://example.com'})
        self.assertEqual(caught.exception.code,403)
        with self.assertRaises(HTTPError) as caught:self.request('/api/data',headers={'Host':'example.com'})
        self.assertEqual(caught.exception.code,403)
    def test_no_directory_traversal(self):
        with self.assertRaises(HTTPError) as caught:self.request('/../app.py')
        self.assertEqual(caught.exception.code,404)
    def test_daily_drilldown(self):
        with self.request('/api/data') as r:data=json.load(r)
        day=data['summary']['timeline'][0]['date']
        with self.request('/api/data?date='+day) as r:detail=json.load(r)
        self.assertEqual(detail['summary']['rows'],5)

if __name__=='__main__':unittest.main()
