import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('observer',Path(__file__).resolve().parents[1]/'observer.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ObserverTests(unittest.TestCase):
  def read(self,change=None):
    data={'observer_only':True,'ego_signal_confirmed':False,'received_unix_ms':100000,'detections':[{'label':'red','score':.9}]}
    data['selection']={'version':1,'status':'stable','ego_signal_confirmed':False,'detections':data['detections']}
    if change:
      data.update(change)
      if 'detections' in change:data['selection']['detections']=change['detections']
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'result';p.write_text(json.dumps(data));return m.read_observer(str(p),now=lambda:100.5)
  def test_fresh_candidate(self):self.assertEqual(self.read()['detections'][0]['label'],'red')
  def test_expired(self):self.assertIsNone(self.read({'received_unix_ms':98000}))
  def test_future(self):self.assertIsNone(self.read({'received_unix_ms':101000}))
  def test_confirmed_input_rejected(self):self.assertIsNone(self.read({'ego_signal_confirmed':True}))
  def test_nan_rejected(self):self.assertIsNone(self.read({'detections':[{'label':'red','score':float('nan')}]}))
  def test_unknown_label(self):self.assertIsNone(self.read({'detections':[{'label':'go','score':.9}]}))
  def test_empty_removes_old_candidate(self):self.assertEqual(self.read({'detections':[]})['detections'],[])
  def test_unstable_never_exports_raw(self):
    self.assertEqual(self.read({'selection':{'version':1,'status':'warming','ego_signal_confirmed':False,'detections':[]}})['detections'],[])
  def test_legacy_raw_only_not_exported(self):self.assertIsNone(self.read({'selection':None}))
  def test_absent_file(self):self.assertIsNone(m.read_observer('/does/not/exist'))
if __name__=='__main__':unittest.main()
