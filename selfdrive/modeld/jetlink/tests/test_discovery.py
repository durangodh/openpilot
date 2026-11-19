"""Regress Android/Termux builds that omit socket.if_nameindex."""
import io
import socket

import pytest

from selfdrive.modeld.jetlink import eonlink as link


def test_native_interface_enumeration(monkeypatch):
  monkeypatch.setattr(socket, 'if_nameindex', lambda: [(1, 'lo'), (2, 'wlan0')], raising=False)
  assert link.interface_names() == ['lo', 'wlan0']


@pytest.mark.parametrize('failure', ['missing', 'oserror', 'unimplemented'])
def test_android_falls_back_to_sysfs(monkeypatch, failure):
  monkeypatch.delattr(socket, 'if_nameindex', raising=False)
  if failure != 'missing':
    def unavailable():
      raise OSError('not supported') if failure == 'oserror' else NotImplementedError()
    monkeypatch.setattr(socket, 'if_nameindex', unavailable, raising=False)
  monkeypatch.setattr(link.os, 'listdir', lambda path: ['wlan0', 'lo'])
  assert link.interface_names() == ['lo', 'wlan0']


def test_android_falls_back_to_procfs(monkeypatch):
  monkeypatch.delattr(socket, 'if_nameindex', raising=False)
  def restricted(_): raise PermissionError('SELinux')
  monkeypatch.setattr(link.os, 'listdir', restricted)
  monkeypatch.setattr('builtins.open', lambda *a, **kw: io.StringIO(
    'Inter-| Receive | Transmit\n face | bytes | packets\n lo: 1 2\n wlan0: 3 4\n'))
  assert link.interface_names() == ['lo', 'wlan0']


def test_global_broadcast_survives_restricted_android_files(monkeypatch):
  monkeypatch.delattr(socket, 'if_nameindex', raising=False)
  def restricted(*args, **kwargs): raise PermissionError('SELinux')
  monkeypatch.setattr(link.os, 'listdir', restricted)
  monkeypatch.setattr('builtins.open', restricted)
  assert link.broadcasts() == ['255.255.255.255']


def test_eon_broadcast_ioctl_uses_real_interface_mask(monkeypatch):
  import fcntl
  monkeypatch.delattr(socket, 'if_nameindex', raising=False)
  monkeypatch.setattr(link.os, 'listdir', lambda _: ['lo', 'wlan0'])
  queried = []
  def ioctl(fd, command, request):
    name = request.split(b'\0', 1)[0]
    queried.append(name)
    assert command == 0x8919
    if name == b'lo': raise OSError('no broadcast')
    return bytes(20) + socket.inet_aton('192.168.79.255') + bytes(232)
  monkeypatch.setattr(fcntl, 'ioctl', ioctl)
  assert link.broadcasts() == ['192.168.79.255', '255.255.255.255']
  assert queried == [b'lo', b'wlan0']


def test_interface_ioctl_failure_keeps_global_broadcast(monkeypatch):
  import fcntl
  monkeypatch.setattr(link, 'interface_names', lambda: ['wlan0'])
  def failed(*args): raise OSError('interface disappeared')
  monkeypatch.setattr(fcntl, 'ioctl', failed)
  assert link.broadcasts() == ['255.255.255.255']
