import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('server',Path(__file__).resolve().parents[1]/'server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class PairingTests(unittest.TestCase):
  def test_four_ascii_digits(self):
    for value in ['0000','0123','9999']:self.assertTrue(server.valid_code(value))
    for value in ['123','12345','１２３４','12ab','1234\n',1234,None]:self.assertFalse(server.valid_code(value))
  def test_generate_leading_zero_and_permissions(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'token'
      with patch.object(server.secrets,'randbelow',return_value=7):self.assertEqual(server.load_pairing_code(p),'0007')
      self.assertEqual(p.read_text(),'0007');self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
      self.assertEqual(server.pairing_info(p)['code'],'0007')
  def test_preserve_existing_code(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'token';p.write_text('0321')
      with patch.object(server.secrets,'randbelow',side_effect=AssertionError('must not rotate')):
        self.assertEqual(server.load_pairing_code(p),'0321')
  def test_migrate_old_token(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'token';p.write_text('a'*32)
      with patch.object(server.secrets,'randbelow',return_value=456):self.assertEqual(server.load_pairing_code(p),'0456')
      self.assertEqual(p.read_text(),'0456')
  def test_failed_attempt_lockout_and_recovery(self):
    now=[0.0];auth=server.PairingAuth('0123',clock=lambda:now[0])
    for _ in range(4):self.assertEqual(auth.status('phone','Bearer 9999'),401)
    self.assertEqual(auth.status('phone','Bearer 9999'),429)
    self.assertEqual(auth.status('phone','Bearer 0123'),429)
    self.assertEqual(auth.status('other','Bearer 0123'),200)
    now[0]=31;self.assertEqual(auth.status('phone','Bearer 0123'),200)
    self.assertEqual(auth.status('phone','Bearer 9999'),401)
  def test_unicode_header_is_rejected(self):
    self.assertEqual(server.PairingAuth('0123').status('phone','Bearer １２３４'),401)
  def test_success_resets_failures(self):
    a=server.PairingAuth('0123')
    for _ in range(4):self.assertEqual(a.status('phone','wrong'),401)
    self.assertEqual(a.status('phone','Bearer 0123'),200)
    self.assertEqual(a.status('phone','wrong'),401)
  def test_pairing_info_does_not_expose_old_token(self):
    with tempfile.TemporaryDirectory() as d:
      p=Path(d)/'token';p.write_text('a'*32);self.assertIsNone(server.pairing_info(p)['code'])

if __name__=='__main__':unittest.main()
