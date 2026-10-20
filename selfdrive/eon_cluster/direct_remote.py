"""EON Bluetooth HID or R1 BLE -> local command journal. No Wi-Fi commands.

Only kernel input nodes with Bluetooth bus type 0005 are considered. A learned
device identity and explicit mapping are required before any command is sent.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import select
import secrets
import socket
import struct
import subprocess
import time

from common.params import Params
from cereal import messaging
from selfdrive.eon_cluster.ble_r1 import BLE_PORT, R1Report, authenticated_packet
from selfdrive.eon_cluster.direct_remote_core import DirectRemoteGestures, axis_direction
from selfdrive.eon_cluster.hud_remote import ALL_COMMANDS, RemoteCommandSync, allowed_commands

EVENT = struct.Struct('@llHHi')
EVIOCGRAB = 0x40044590
EVIOCSCLOCKID = 0x400445A0
TOKEN = re.compile(r'^(?:key:(?:[0-9]{1,3})|(?:hat|axis):[xy]:[+-]|blebtn:(?:A|B|C|D|OK|BACK)|blejoy:(?:up|down|left|right))@(short|long|double)$')
SCAN_S = 1.0


def value(params, name):
  raw = params.get(name)
  return raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else (raw or '')


def mappings(raw):
  try:
    data = json.loads(raw or '{}')
    if not isinstance(data, dict) or len(data) > 96:
      return {}
    return {key: action for key, action in data.items()
            if isinstance(key, str) and TOKEN.fullmatch(key) and action in ALL_COMMANDS}
  except (TypeError, ValueError):
    return {}


def learn_request(raw):
  parts = raw.split('|')
  return (parts[0], parts[1], parts[2]) if (len(parts) == 3 and
          parts[0] in ALL_COMMANDS and parts[1] in ('short', 'long', 'double') and
          re.fullmatch(r'[0-9a-f]{32}', parts[2])) else None


def bluetooth_nodes(root=Path('/sys/class/input')):
  found = {}
  entries = list(root.glob('event*'))
  bluetooth_count = 0
  missing_identity = 0
  candidates = []
  def optional(path):
    try:
      return path.read_text().strip()
    except OSError:
      return ''
  for entry in entries:
    device = entry / 'device'
    try:
      bus = (device / 'id/bustype').read_text().strip().lower()
      name = (device / 'name').read_text().strip()
      if 'magicsee' in name.lower() or name.lower() == 'r1':
        candidates.append(f'{name}:bus={bus}')
      if bus != '0005':
        continue
      bluetooth_count += 1
      uniq = optional(device / 'uniq')
      phys = re.sub(r'/input[0-9]+$', '', optional(device / 'phys'))
      identity = uniq or phys
      if not identity:
        missing_identity += 1
        continue  # A model name alone cannot distinguish another identical remote.
      try:
        properties = int(''.join((device / 'properties').read_text().split()) or '0', 16)
      except OSError:
        properties = 0
      try:
        relative = int(''.join((device / 'capabilities/rel').read_text().split()) or '0', 16)
      except OSError:
        relative = 0
      # Android mouse/touch nodes must never turn cursor movement into a command.
      joystick = (properties & 0x3) == 0 and (relative & 0x3) == 0
      found[str(Path('/dev/input') / entry.name)] = (identity, name, joystick)
    except OSError:
      continue
  summary = f'입력 {len(entries)}개 / Bluetooth HID {bluetooth_count}개 / 고유 ID 없음 {missing_identity}개'
  if candidates:
    summary += ' / R1 후보 ' + ', '.join(candidates[:2])
  return found, summary


class InputNode:
  def __init__(self, path, identity, name, joystick, emit, observe):
    self.path, self.identity, self.name, self.joystick = path, identity, name, joystick
    self.observe = observe
    self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
      fcntl.ioctl(self.fd, EVIOCSCLOCKID, struct.pack('i', 1))  # CLOCK_MONOTONIC
      self.monotonic_events = True
    except OSError:
      # Older Android kernels can lack this ioctl. Their default may be
      # realtime or monotonic, so accept only a fresh timestamp on either clock.
      self.monotonic_events = False
    self.grabbed = False
    self.buffer = b''
    self.hats = {'x': 0, 'y': 0}
    self.axes = {'x': 0, 'y': 0}
    self.axis_ranges = {}
    if joystick:
      for code, axis in ((0, 'x'), (1, 'y')):
        try:
          raw = fcntl.ioctl(self.fd, 0x80184540 + code, bytes(24))  # EVIOCGABS
          _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack('iiiiii', raw)
          if maximum > minimum:
            self.axis_ranges[axis] = (minimum, maximum)
        except OSError:
          pass
    self.gestures = DirectRemoteGestures({}, lambda token, gesture: emit(self, token, gesture))

  def grab(self, enabled):
    if enabled != self.grabbed:
      fcntl.ioctl(self.fd, EVIOCGRAB, 1 if enabled else 0)
      self.grabbed = enabled
      self.gestures.reset()

  def close(self):
    try:
      try:
        self.grab(False)
      except OSError:
        pass
    finally:
      os.close(self.fd)

  def read(self, now):
    data = os.read(self.fd, EVENT.size * 64)
    if not data:
      raise OSError('input device disconnected')
    self.buffer += data
    while len(self.buffer) >= EVENT.size:
      item, self.buffer = self.buffer[:EVENT.size], self.buffer[EVENT.size:]
      sec, usec, kind, code, state = EVENT.unpack(item)
      if kind == 0 and code == 3:  # SYN_DROPPED: discard the entire device stream.
        raise OSError('dropped Bluetooth input frame')
      stamp = sec + usec / 1e6
      ages = (now - stamp,) if self.monotonic_events else (now - stamp, time.time() - stamp)
      if not any(0 <= age <= .4 for age in ages):
        self.gestures.reset()
        continue
      if kind == 1 and (0 < code < 256 or 304 <= code <= 318 or 352 <= code <= 767) and state in (0, 1):
        self.observe(self, f'key:{code}', state)
        self.gestures.key(f'key:{code}', state, stamp)
      elif kind == 3 and code in (16, 17):  # Standard HID D-pad hat.
        axis = 'x' if code == 16 else 'y'
        old, new = self.hats[axis], max(-1, min(1, state))
        if old != new:
          self.observe(self, f'hat:{axis}', new)
          if old:
            self.gestures.key(f'hat:{axis}:{"+" if old > 0 else "-"}', 0, stamp)
          if new:
            self.gestures.key(f'hat:{axis}:{"+" if new > 0 else "-"}', 1, stamp)
          self.hats[axis] = new
      elif kind == 3 and code in (0, 1) and self.joystick:
        axis = 'x' if code == 0 else 'y'
        bounds = self.axis_ranges.get(axis)
        if bounds is None:
          continue
        minimum, maximum = bounds
        center = (minimum + maximum) / 2.0
        position = (state - center) / ((maximum - minimum) / 2.0)
        old = self.axes[axis]
        new = axis_direction(position, old)
        if old != new:
          self.observe(self, f'axis:{axis}', new)
          if old:
            self.gestures.key(f'axis:{axis}:{"+" if old > 0 else "-"}', 0, stamp)
          if new:
            self.gestures.key(f'axis:{axis}:{"+" if new > 0 else "-"}', 1, stamp)
          self.axes[axis] = new


class BleInputNode:
  def __init__(self, address, emit, observe):
    self.identity = 'ble:' + address
    self.name = 'Magicsee R1 BLE'
    self.observe = observe
    self.report = R1Report()
    self.gestures = DirectRemoteGestures({}, lambda token, gesture: emit(self, token, gesture))
    self.last_report = 0.0

  def read(self, data, now):
    changes = self.report.transitions(data)
    if len(data) >= 5 and data[0] == 0x1b:
      self.last_report = now
    for token, state in changes:
      self.observe(self, token, state)
      self.gestures.key(token, state, now)

  def reset(self):
    self.report.reset()
    self.gestures.reset()


class BleBridge:
  SERVICE = 'ai.comma.eonbleremote/.BleService'

  def __init__(self, emit, observe):
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.bind(('127.0.0.1', BLE_PORT))
    self.sock.setblocking(False)
    self.secret = secrets.token_hex(16)
    self.counters = {}
    self.node = None
    self.emit, self.observe = emit, observe
    self.next_renew = 0.0
    self.running = False
    self.status = 'BLE 수신기 대기'
    self.last_status = 0.0
    self.permission_granted = False
    self.am_error = ''

  @staticmethod
  def _android_env():
    env = os.environ.copy()
    # EON's Android command wrappers must load system libraries, not the
    # openpilot native libraries inherited from manager.py.
    env['LD_LIBRARY_PATH'] = ''
    return env

  def _am(self, *args):
    try:
      result = subprocess.run(['/system/bin/am', *args], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=8, check=False,
                              env=self._android_env())
      output = result.stdout.decode('utf-8', 'replace').strip().replace('\n', ' ')
      self.am_error = output[-140:] if result.returncode else ''
      if result.returncode and not self.am_error:
        self.am_error = '종료 코드 %d' % result.returncode
      return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
      self.am_error = str(error)[-140:]
      return False

  def start(self, now, selected):
    if now < self.next_renew:
      return
    self.next_renew = now + 8.0
    if not self.permission_granted:
      grant = subprocess.run(['/system/bin/pm', 'grant', 'ai.comma.eonbleremote',
                              'android.permission.ACCESS_COARSE_LOCATION'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=8, check=False, env=self._android_env())
      self.permission_granted = grant.returncode == 0
    target = selected[4:] if selected.startswith('ble:') else ''
    if not self._am('startservice', '-n', self.SERVICE, '--es', 'key', self.secret,
                    '--es', 'target', target):
      self.status = 'BLE 앱 시작 오류: ' + self.am_error
      return
    self.running = True
    if not self.last_status:
      self.status = 'R1 BLE 검색 중 (Android 페어링 해제 필요)'

  def stop(self):
    if self.running:
      try:
        self._am('stopservice', '-n', self.SERVICE)
      except (OSError, subprocess.TimeoutExpired):
        pass
      self.secret = secrets.token_hex(16)
      self.counters.clear()
    self.running = False
    self.next_renew = 0.0
    if self.node:
      self.node.reset()
      self.node = None

  def poll(self, now, selected, mapping, request):
    for _ in range(64):
      try:
        raw, _address = self.sock.recvfrom(513)
      except BlockingIOError:
        break
      authenticated = authenticated_packet(raw, self.secret, self.counters)
      if not authenticated:
        continue
      address, data = authenticated
      if address == 'STATUS':
        self.status = 'BLE: ' + data.decode('utf-8', 'replace')
        self.last_status = now
        if any(word in self.status for word in ('끊김', '실패', '없음')) and self.node:
          self.node.reset()
        continue
      if not self.node or self.node.identity != 'ble:' + address:
        if self.node:
          self.node.reset()
        self.node = BleInputNode(address, self.emit, self.observe)
      self.node.gestures.mapping = mapping if self.node.identity == selected else {}
      self.node.gestures.learn_gesture = request[1] if request and (not selected or self.node.identity == selected) else None
      self.node.read(data, now)
      self.status = 'BLE 입력 감지: Magicsee R1'
      self.last_status = now
    if self.node and self.node.last_report and now - self.node.last_report > 1.2:
      self.node.reset()  # Never keep a driving button held after BLE reports stop.

  def close(self):
    self.stop()
    self.sock.close()


def vehicle_allowed(sm):
  started = sm.alive['deviceState'] and sm['deviceState'].started
  valid = sm.alive['carState'] and sm.valid['carState'] and sm['carState'].canValid
  car = sm['carState']
  return allowed_commands(started, valid, car.gearShifter == 'drive', car.brakePressed, car.gasPressed)


def main():
  params = Params()
  sm = messaging.SubMaster(['carState', 'deviceState'])
  writer = RemoteCommandSync(params)
  nodes = {}
  next_scan = next_status = 0.0
  selected = mapping_raw = request_raw = ''
  mapping = {}
  enabled = False
  status = 'Bluetooth 입력 장치 대기'
  published_status = None
  last_emitted = {}
  ble = BleBridge(emit=None, observe=None)

  def observe(node, token, state):
    nonlocal status
    if learn_request(request_raw):
      status = f'입력 감지: {node.name} / {token} / {state}'

  def emit(node, token, gesture):
    nonlocal status, published_status, selected, mapping, mapping_raw, request_raw
    request = learn_request(request_raw)
    if request:
      action, expected, _nonce = request
      if gesture != expected or not params.get_bool('IsOffroad'):
        return
      # The first learned button fixes the exact device identity. Other paired
      # Bluetooth keyboards cannot inject commands thereafter.
      if selected and node.identity != selected:
        return
      selected = node.identity
      params.put('HudRemoteDirectDevice', selected)
      mapping[token + '@' + gesture] = action
      mapping_raw = json.dumps(mapping, separators=(',', ':'))
      params.put('HudRemoteDirectMapping', mapping_raw)
      status = f'학습 완료: {node.name} / {token} / {gesture} → {action}'
      params.put('HudRemoteDirectStatus', status)
      published_status = status
      params.remove('HudRemoteDirectLearn')
      request_raw = ''
      node.gestures.reset()
      return
    if not enabled or node.identity != selected:
      return
    action = mapping.get(token + '@' + gesture, 'none')
    key = (node.identity, token, gesture)
    now = time.monotonic()
    if now - last_emitted.get(key, -100.0) < .12:
      return  # Some HID devices expose duplicate keyboard/media input nodes.
    if action in ALL_COMMANDS and writer.apply_local(action, vehicle_allowed(sm)):
      last_emitted[key] = now
      status = f'{node.name}: {action}'

  ble.emit, ble.observe = emit, observe

  try:
    while True:
      now = time.monotonic()
      enabled = params.get_bool('HudRemoteDirectEnabled')
      new_selected = value(params, 'HudRemoteDirectDevice')
      new_mapping_raw = value(params, 'HudRemoteDirectMapping')
      new_request_raw = value(params, 'HudRemoteDirectLearn')
      if new_selected != selected or new_mapping_raw != mapping_raw or new_request_raw != request_raw:
        selected, mapping_raw, request_raw = new_selected, new_mapping_raw, new_request_raw
        mapping = mappings(mapping_raw)
        for node in nodes.values():
          node.gestures.reset()
        if ble.node:
          ble.node.reset()
        if learn_request(request_raw):
          status = '학습 요청 확인: Bluetooth 입력 장치 검색 중'
      request = learn_request(request_raw)
      if not enabled and not request:
        for node in nodes.values():
          node.close()
        nodes.clear()
        ble.stop()
        next_scan = 0.0
        status = 'EON 직접 리모컨 사용 안 함'
        if status != published_status:
          params.put('HudRemoteDirectStatus', status)
          published_status = status
        time.sleep(.25)
        continue
      sm.update(0)
      try:
        ble.start(now, selected)
        ble.poll(now, selected, mapping, request)
      except (OSError, subprocess.TimeoutExpired):
        ble.status = 'BLE 앱 통신 실패'
      if ble.node:
        ble.node.gestures.mapping = mapping if ble.node.identity == selected else {}
        ble.node.gestures.learn_gesture = request[1] if request and (not selected or ble.node.identity == selected) else None
        if enabled and not request:
          ble.node.gestures.cancel_disallowed(vehicle_allowed(sm))
        ble.node.gestures.tick(now)
      if now >= next_scan:
        available, scan_summary = bluetooth_nodes()
        if request and not available:
          status = ble.status + ' / ' + scan_summary
        for path, node in list(nodes.items()):
          if path not in available or available[path] != (node.identity, node.name, node.joystick):
            node.close()
            del nodes[path]
            status = '리모컨 연결 끊김'
        for path, (identity, name, joystick) in available.items():
          if path not in nodes:
            try:
              nodes[path] = InputNode(path, identity, name, joystick, emit, observe)
            except OSError as error:
              status = f'입력 장치 열기 실패: {name} (errno {error.errno})'
        if request and nodes and status.startswith(('학습 요청', '인식 가능한 Bluetooth 입력 장치 없음')):
          status = 'Bluetooth HID 입력 대기: ' + ', '.join(node.name for node in nodes.values())
        next_scan = now + SCAN_S
      for node in list(nodes.values()):
        should_grab = (bool(request) and params.get_bool('IsOffroad') and
                       (not selected or node.identity == selected)) or (enabled and not request and node.identity == selected and bool(mapping))
        try:
          node.grab(should_grab)
          node.gestures.mapping = mapping if node.identity == selected else {}
          node.gestures.learn_gesture = request[1] if request and (not selected or node.identity == selected) else None
        except OSError as error:
          status = f'입력 장치 독점 실패: {node.name} (errno {error.errno})'
          node.close()
          del nodes[node.path]
      try:
        readable, _, _ = select.select([node.fd for node in nodes.values()], [], [], .05) if nodes else ([], [], [])
      except (OSError, ValueError):
        for node in nodes.values():
          node.close()
        nodes.clear()
        status = 'Bluetooth 입력 장치 다시 연결 중'
        continue
      if not nodes:
        time.sleep(.05)
      for node in list(nodes.values()):
        if node.fd in readable:
          try:
            node.read(time.monotonic())
          except OSError:
            node.close()
            del nodes[node.path]
        if node.path in nodes:
          if enabled and not request:
            node.gestures.cancel_disallowed(vehicle_allowed(sm))
          node.gestures.tick(time.monotonic())
      if now >= next_status and status != published_status:
        params.put('HudRemoteDirectStatus', status)
        published_status = status
        next_status = now + 1.0
  finally:
    for node in nodes.values():
      node.close()
    ble.close()
