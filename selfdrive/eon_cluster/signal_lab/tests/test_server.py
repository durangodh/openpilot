import importlib.util
import json
import math
from pathlib import Path
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signal_server', ROOT / 'server.py')
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

class Clock:
  def __init__(self): self.now = 10.0
  def __call__(self): return self.now

class ResultTests(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.r = srv.Results(clock=self.clock)
  def message(self):
    return {'session':self.r.session,'frame_id':self.r.issue(),'model_sha256':srv.MODEL_SHA,
            'infer_ms':50,'detections':[{'label':'red','score':.9,'box':[.2,.1,.5,.2]}]}
  def test_accept_observer_only(self):
    result=self.r.accept(self.message())
    self.assertTrue(result['observer_only']);self.assertFalse(result['ego_signal_confirmed'])
    self.assertEqual(self.r.snapshot()['status'],'candidate')
  def test_expired_result_rejected(self):
    m=self.message();self.clock.now+=.86
    with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_duplicate_rejected(self):
    m=self.message();self.r.accept(m)
    with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_previous_session_rejected(self):
    m=self.message();m['session']='old'
    with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_model_identity_rejected(self):
    m=self.message();m['model_sha256']='different'
    with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_out_of_order(self):
    first=self.message();second=self.message();self.r.accept(second)
    with self.assertRaises(srv.BadResult):self.r.accept(first)
  def test_stale_clears_display(self):
    self.r.accept(self.message());self.clock.now+=1.21
    self.assertEqual(self.r.snapshot()['detections'],[])
  def test_empty_detection_clears_red(self):
    self.r.accept(self.message());m=self.message();m['detections']=[];self.r.accept(m)
    self.assertEqual(self.r.snapshot()['status'],'none')
  def test_red_and_left_kept_separate(self):
    m=self.message();m['detections'].append({'label':'left','score':.8,'box':[.2,.1,.5,.2]})
    self.assertEqual(len(self.r.accept(m)['detections']),2)
  def test_malformed_values(self):
    for field,value in [('score',float('nan')),('box',[0,0,float('inf'),1]),('box',[.5,0,.2,1]),('label','speed_sign')]:
      m=self.message();m['detections'][0][field]=value
      with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_unsolicited_result(self):
    m=self.message();m['frame_id']=999
    with self.assertRaises(srv.BadResult):self.r.accept(m)
  def test_negative_clock(self):
    m=self.message();self.clock.now-=1
    with self.assertRaises(srv.BadResult):self.r.accept(m)

class FakeCamera:
  def frame(self):return b'\xff\xd8test\xff\xd9',640,480

class HttpTests(unittest.TestCase):
  def setUp(self):
    self.server=srv.Server(('127.0.0.1',0),'0234',FakeCamera(),srv.Results())
    self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    self.url='http://127.0.0.1:%d'%self.server.server_port
  def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
  def request(self,path,body=None,auth=True):
    headers={'Authorization':'Bearer '+'0234'} if auth else {}
    data=None if body is None else json.dumps(body).encode()
    return urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers=headers),timeout=2)
  def test_auth_required(self):
    for p in ['/frame.jpg','/state']:
      with self.assertRaises(urllib.error.HTTPError) as e:self.request(p,auth=False)
      self.assertEqual(e.exception.code,401)
  def test_http_pin_lockout(self):
    for attempt in range(5):
      req=urllib.request.Request(self.url+'/state',headers={'Authorization':'Bearer 9999'})
      with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req,timeout=2)
      self.assertEqual(e.exception.code,429 if attempt==4 else 401)
    with self.assertRaises(urllib.error.HTTPError) as e:self.request('/state')
    self.assertEqual(e.exception.code,429)
    self.assertEqual(e.exception.headers['Retry-After'],'30')
  def test_frame_to_phone_to_eon_roundtrip(self):
    with self.request('/frame.jpg') as response:
      frame=int(response.headers['X-Signal-Frame']);session=response.headers['X-Signal-Session']
      self.assertEqual(response.headers['Content-Type'],'image/jpeg');response.read()
    payload={'session':session,'frame_id':frame,'model_sha256':srv.MODEL_SHA,'infer_ms':25,
             'detections':[{'label':'left','score':.8,'box':[.2,.1,.4,.2]}]}
    with self.request('/result',payload) as response:self.assertTrue(json.load(response)['ok'])
    with self.request('/state') as response:
      result=json.load(response);self.assertEqual(result['detections'][0]['label'],'left')
      self.assertTrue(result['observer_only']);self.assertFalse(result['ego_signal_confirmed'])
  def test_large_body_rejected(self):
    req=urllib.request.Request(self.url+'/result',data=b'x'*33000,headers={'Authorization':'Bearer '+'0234'})
    with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req,timeout=2)
    self.assertEqual(e.exception.code,413)

if __name__=='__main__':unittest.main()
