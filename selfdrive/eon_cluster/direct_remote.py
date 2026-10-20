"""EON Bluetooth HID -> local command journal. No Wi-Fi command transport.

Only kernel input nodes with Bluetooth bus type 0005 are considered. A learned
device identity and explicit mapping are required before any command is sent.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import select
import struct
import time

from common.params import Params
from cereal import messaging
from selfdrive.eon_cluster.direct_remote_core import DirectRemoteGestures, axis_direction
from selfdrive.eon_cluster.hud_remote import ALL_COMMANDS, RemoteCommandSync, allowed_commands

EVENT = struct.Struct('@llHHi')
EVIOCGRAB = 0x40044590
EVIOCSCLOCKID = 0x400445A0
TOKEN = re.compile(r'^(?:key:(?:[0-9]{1,3})|(?:hat|axis):[xy]:[+-])@(short|long|double)$')
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
  def optional(path):
    try:
      return path.read_text().strip()
    except OSError:
      return ''
  for entry in root.glob('event*'):
    device = entry / 'device'
    try:
      if (device / 'id/bustype').read_text().strip().lower() != '0005':
        continue
      name = (device / 'name').read_text().strip()
      uniq = optional(device / 'uniq')
      phys = re.sub(r'/input[0-9]+$', '', optional(device / 'phys'))
      identity = uniq or phys
      if not identity:
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
  return found


class InputNode:
  def __init__(self, path, identity, name, joystick, emit):
    self.path, self.identity, self.name, self.joystick = path, identity, name, joystick
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
        self.gestures.key(f'key:{code}', state, stamp)
      elif kind == 3 and code in (16, 17):  # Standard HID D-pad hat.
        axis = 'x' if code == 16 else 'y'
        old, new = self.hats[axis], max(-1, min(1, state))
        if old != new:
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
          if old:
            self.gestures.key(f'axis:{axis}:{"+" if old > 0 else "-"}', 0, stamp)
          if new:
            self.gestures.key(f'axis:{axis}:{"+" if new > 0 else "-"}', 1, stamp)
          self.axes[axis] = new


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
      request = learn_request(request_raw)
      if not enabled and not request:
        for node in nodes.values():
          node.close()
        nodes.clear()
        next_scan = 0.0
        status = 'EON 직접 리모컨 사용 안 함'
        if status != published_status:
          params.put('HudRemoteDirectStatus', status)
          published_status = status
        time.sleep(.25)
        continue
      sm.update(0)
      if now >= next_scan:
        available = bluetooth_nodes()
        for path, node in list(nodes.items()):
          if path not in available or available[path] != (node.identity, node.name, node.joystick):
            node.close()
            del nodes[path]
            status = '리모컨 연결 끊김'
        for path, (identity, name, joystick) in available.items():
          if path not in nodes:
            try:
              nodes[path] = InputNode(path, identity, name, joystick, emit)
            except OSError:
              status = f'입력 장치 열기 실패: {name}'
        next_scan = now + SCAN_S
      for node in list(nodes.values()):
        should_grab = (bool(request) and params.get_bool('IsOffroad') and
                       (not selected or node.identity == selected)) or (enabled and not request and node.identity == selected and bool(mapping))
        try:
          node.grab(should_grab)
          node.gestures.mapping = mapping if node.identity == selected else {}
          node.gestures.learn_gesture = request[1] if request and (not selected or node.identity == selected) else None
        except OSError:
          status = f'입력 장치 독점 실패: {node.name}'
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
