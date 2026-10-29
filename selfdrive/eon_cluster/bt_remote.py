#!/usr/bin/env python3
"""EON Bluetooth remote: classic Bluetooth HID input read straight from the kernel.

The remote (e.g. 8BitDo Micro in keyboard mode) is paired once in Android's
Bluetooth settings. Android's HID host then creates /dev/input/eventN with bus
type 0005; this process grabs that node exclusively, so Android never sees the
keys, and turns learned presses into hud_remote commands. No phone, no Wi-Fi
and no Android service is involved.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import time

from selfdrive.eon_cluster.bt_remote_core import Gestures, LinkTracker, axis_direction, learning_allowed
from selfdrive.eon_cluster.hud_remote import (ALL_COMMANDS, STATUS_FILE, RemoteCommandSync, allowed_commands,
                                              atomic_json)

EVENT = struct.Struct('@llHHi')
EVIOCGRAB = 0x40044590
EVIOCSCLOCKID = 0x400445A0
TOKEN = re.compile(r'^(?:key:(?:[0-9]{1,3})|(?:hat|axis):[xy]:[+-])@(short|long|double)$')
SCAN_S = .5
STATUS_S = 1.0
MAX_EVENT_AGE_S = .4
LOG_FILE = '/data/media/0/bt_remote.log'
LOG_MAX_BYTES = 512 * 1024


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


class Log:
  """Small append-only log on EON's user storage, rotated once at LOG_MAX_BYTES."""

  def __init__(self, path=LOG_FILE):
    self.path = path

  def write(self, line):
    try:
      if os.path.getsize(self.path) > LOG_MAX_BYTES:
        os.replace(self.path, self.path + '.1')
    except OSError:
      pass
    try:
      with open(self.path, 'a') as f:
        f.write('%s %s\n' % (time.strftime('%m-%d %H:%M:%S'), line))
    except OSError:
      pass


def bluetooth_nodes(root=Path('/sys/class/input')):
  """Bluetooth (bus 0005) input nodes: path -> (identity, name, joystick), plus a scan summary."""
  found = {}
  entries = list(root.glob('event*'))
  bluetooth_count = 0
  missing_identity = 0

  def optional(path):
    try:
      return path.read_text().strip()
    except OSError:
      return ''

  for entry in entries:
    device = entry / 'device'
    try:
      if (device / 'id/bustype').read_text().strip().lower() != '0005':
        continue
      name = (device / 'name').read_text().strip()
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
  return found, summary


class InputNode:
  def __init__(self, path, identity, name, joystick, emit, observe, now):
    self.path, self.identity, self.name, self.joystick = path, identity, name, joystick
    self.observe = observe
    self.opened = now
    self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
      fcntl.ioctl(self.fd, EVIOCSCLOCKID, struct.pack('i', 1))  # CLOCK_MONOTONIC
      self.monotonic_events = True
    except OSError:
      self.monotonic_events = False  # Older kernels stamp with the realtime clock.
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
    self.gestures = Gestures({}, lambda token, gesture, at: emit(self, token, gesture, at))

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

  def _press(self, token, state, at):
    self.observe(self, token, state, at)
    self.gestures.key(token, state, at)

  def read(self, now):
    data = os.read(self.fd, EVENT.size * 64)
    if not data:
      raise OSError('input device disconnected')
    self.buffer += data
    # Realtime stamps are moved to the monotonic clock with one offset per read, so
    # the events of one read keep their order.
    offset = 0.0 if self.monotonic_events else now - time.time()
    while len(self.buffer) >= EVENT.size:
      item, self.buffer = self.buffer[:EVENT.size], self.buffer[EVENT.size:]
      sec, usec, kind, code, state = EVENT.unpack(item)
      if kind == 0 and code == 3:  # SYN_DROPPED: discard the entire device stream.
        raise OSError('dropped Bluetooth input frame')
      stamp = sec + usec / 1e6
      at = stamp + offset
      if not 0 <= now - at <= MAX_EVENT_AGE_S:
        self.gestures.reset()
        continue
      if kind == 1 and (0 < code < 256 or 304 <= code <= 318 or 352 <= code <= 767) and state in (0, 1):
        self._press(f'key:{code}', state, at)
      elif kind == 3 and code in (16, 17):  # Standard HID D-pad hat.
        axis = 'x' if code == 16 else 'y'
        old, new = self.hats[axis], max(-1, min(1, state))
        if old != new:
          if old:
            self._press(f'hat:{axis}:{"+" if old > 0 else "-"}', 0, at)
          if new:
            self._press(f'hat:{axis}:{"+" if new > 0 else "-"}', 1, at)
          self.hats[axis] = new
      elif kind == 3 and code in (0, 1) and self.joystick:
        axis = 'x' if code == 0 else 'y'
        bounds = self.axis_ranges.get(axis)
        if bounds is None:
          continue
        minimum, maximum = bounds
        center = (minimum + maximum) / 2.0
        old = self.axes[axis]
        new = axis_direction((state - center) / ((maximum - minimum) / 2.0), old)
        if old != new:
          if old:
            self._press(f'axis:{axis}:{"+" if old > 0 else "-"}', 0, at)
          if new:
            self._press(f'axis:{axis}:{"+" if new > 0 else "-"}', 1, at)
          self.axes[axis] = new


def enable_bluetooth(log):
  """IBluetoothManager.enable() (transaction 6 on Android 6); a native binder call, no Java runtime."""
  env = os.environ.copy()
  env['LD_LIBRARY_PATH'] = ''  # Android's command wrappers must not load openpilot's native libraries.
  try:
    subprocess.run(['/system/bin/service', 'call', 'bluetooth_manager', '6'], env=env, timeout=5,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    log.write('bluetooth enable requested')
  except (OSError, subprocess.TimeoutExpired) as error:
    log.write('bluetooth enable failed: %s' % error)


def vehicle_state(sm):
  started = sm.alive['deviceState'] and sm['deviceState'].started
  valid = sm.alive['carState'] and sm.valid['carState'] and sm['carState'].canValid
  car = sm['carState']
  return started, valid, car.gearShifter == 'drive', car.brakePressed, car.gasPressed


def remote_learning_allowed(sm, params):
  if params.get_bool('IsOffroad'):
    return True
  car = sm['carState']
  return learning_allowed(False, sm.alive['carState'], sm.valid['carState'],
                          car.canValid, car.standstill, car.gearShifter == 'park')


def main():
  # Imported here so the reader and its tests load without the compiled cereal/params modules.
  from cereal import messaging
  from common.params import Params
  params = Params()
  sm = messaging.SubMaster(['carState', 'deviceState'])
  writer = RemoteCommandSync(params)
  link = LinkTracker()
  log = Log()
  nodes = {}
  next_scan = next_status = next_settings = 0.0
  selected = mapping_raw = request_raw = ''
  mapping = {}
  enabled = None
  status = 'Bluetooth 입력 장치 대기'
  published_status = None
  published_hud = None
  last_emitted = {}
  allowed = frozenset()
  log.write('start')

  def observe(node, token, state, at):
    nonlocal status
    if learn_request(request_raw):
      status = f'입력 감지: {node.name} / {token} / {state}'
    elif node.identity == selected and state == 1:
      link.input(at)

  def emit(node, token, gesture, at):
    nonlocal status, published_status, selected, mapping, mapping_raw, request_raw
    request = learn_request(request_raw)
    if request:
      action, expected, _nonce = request
      sm.update(0)
      if gesture != expected or not remote_learning_allowed(sm, params):
        return
      # The first learned button fixes the exact device identity. Other paired
      # Bluetooth keyboards cannot inject commands thereafter.
      if selected and node.identity != selected:
        return
      selected = node.identity
      params.put('BtRemoteDevice', selected)
      mapping[token + '@' + gesture] = action
      mapping_raw = json.dumps(mapping, separators=(',', ':'))
      params.put('BtRemoteMapping', mapping_raw)
      status = f'학습 완료: {node.name} / {token} / {gesture} → {action}'
      params.put('BtRemoteStatus', status)
      published_status = status
      params.remove('BtRemoteLearn')
      request_raw = ''
      node.gestures.reset()
      log.write('learned %s %s@%s -> %s' % (node.name, token, gesture, action))
      return
    if not enabled or node.identity != selected:
      return
    action = mapping.get(token + '@' + gesture, 'none')
    if action not in ALL_COMMANDS:
      return
    if link.guarded(at):
      link.wake_drop()
      status = f'{node.name}: 깨우기 입력 무시 ({action})'
      log.write('wake press ignored %s %.2fs after connect' % (action, at - link.connected_at))
      return
    key = (node.identity, token, gesture)
    now = time.monotonic()
    if now - last_emitted.get(key, -100.0) < .12:
      return  # Some HID devices expose duplicate keyboard/media input nodes.
    last_emitted[key] = now
    result = writer.apply(action, allowed)
    latency = int((time.monotonic() - at) * 1000)
    link.latency(latency)
    status = f'{node.name}: {action} {result}'
    log.write('cmd %s %s latency=%dms' % (action, result, latency))

  try:
    while True:
      now = time.monotonic()
      new_selected, new_mapping_raw, new_request_raw = selected, mapping_raw, request_raw
      if now >= next_settings:
        new_enabled = params.get_bool('BtRemoteEnabled')
        if new_enabled and not enabled:
          enable_bluetooth(log)
        enabled = new_enabled
        new_selected = value(params, 'BtRemoteDevice')
        new_mapping_raw = value(params, 'BtRemoteMapping')
        new_request_raw = value(params, 'BtRemoteLearn')
        next_settings = now + .5
      if new_selected != selected or new_mapping_raw != mapping_raw or new_request_raw != request_raw:
        selected, mapping_raw, request_raw = new_selected, new_mapping_raw, new_request_raw
        mapping = mappings(mapping_raw)
        for node in nodes.values():
          node.gestures.reset()
        if learn_request(request_raw):
          status = '학습 요청 확인: Bluetooth 입력 장치 검색 중'
      request = learn_request(request_raw)
      if not enabled and not request:
        for node in nodes.values():
          node.close()
        nodes.clear()
        line = link.disconnect(now)
        if line:
          log.write(line)
        next_scan = 0.0
        status = '블루투스 리모컨 사용 안 함'
        if status != published_status:
          params.put('BtRemoteStatus', status)
          published_status = status
        if published_hud is not None:
          try:
            os.remove(STATUS_FILE)
          except OSError:
            pass
          published_hud = None
        time.sleep(.25)
        continue
      sm.update(0)
      vehicle = vehicle_state(sm)
      writer.vehicle = vehicle
      allowed = allowed_commands(*vehicle)
      if now >= next_scan:
        available, scan_summary = bluetooth_nodes()
        if request and not available:
          status = '인식 가능한 Bluetooth 입력 장치 없음 / ' + scan_summary
        for path, node in list(nodes.items()):
          if path not in available or available[path] != (node.identity, node.name, node.joystick):
            node.close()
            del nodes[path]
        for path, (identity, name, joystick) in available.items():
          if path not in nodes:
            try:
              nodes[path] = InputNode(path, identity, name, joystick, emit, observe, now)
            except OSError as error:
              status = f'입력 장치 열기 실패: {name} (errno {error.errno})'
        if request and nodes and status.startswith(('학습 요청', '인식 가능한 Bluetooth 입력 장치 없음')):
          status = 'Bluetooth HID 입력 대기: ' + ', '.join(node.name for node in nodes.values())
        next_scan = now + SCAN_S
      present = bool(selected) and any(node.identity == selected for node in nodes.values())
      line = link.connect(now) if present else link.disconnect(now)
      if line:
        log.write(line + ' / ' + link.summary())
        if not request:
          status = '리모컨 연결됨' if present else '리모컨 연결 끊김'
      for node in list(nodes.values()):
        should_grab = (bool(request) and remote_learning_allowed(sm, params) and
                       (not selected or node.identity == selected)) or \
                      (enabled and not request and node.identity == selected and bool(mapping))
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
            node.gestures.cancel_disallowed(allowed)
          node.gestures.tick(time.monotonic())
      now = time.monotonic()
      hud = {'link': link.link, 'event': writer.event_status(now)} if enabled and selected else None
      if hud != published_hud or (hud is not None and now >= next_status):
        try:
          if hud is None:
            os.remove(STATUS_FILE)
          else:
            atomic_json(STATUS_FILE, dict(hud, ts=now))
        except OSError:
          pass
        published_hud = hud
      display_status = status + ('\n' + link.summary() if link.connects else '')
      if now >= next_status:
        if display_status != published_status:
          params.put('BtRemoteStatus', display_status)
          published_status = display_status
        next_status = now + STATUS_S
  finally:
    for node in nodes.values():
      node.close()
    log.write('stop / ' + link.summary())


if __name__ == '__main__':
  main()
