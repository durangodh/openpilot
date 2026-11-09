"""MDPS fault recorder: the 2 s before and 1 s after each MDPS fault, for finding why it faulted.

controlsd calls update() once per control frame with the MDPS12 fault bits and one row of
numbers. A row is kept in a fixed-size ring; nothing is written until a fault bit turns on.
The file write runs in a background thread so the 100 Hz loop never waits on storage.

Log: /data/media/0/mdps_log/mdps_fault_MMDD.log (http://EON_IP:7714/trace "MDPS 폴트 기록").
Each row: time, loop interval ms, speed km/h, angle deg, angle rate deg/s, driver torque,
MDPS output torque, commanded torque (apply), latActive, cut pulse, fault bits
(ToiUnavail ToiFlt FailStat).
"""
import collections
import os
import queue
import threading
import time

LOG_DIR = "/data/media/0/mdps_log"   # trace_http.py TRACE_DIRS["mdps"]
PRE_FRAMES = 200    # 2 s before the fault
POST_FRAMES = 100   # 1 s after
HEADER = "time         dt_ms  kph   angle   rate  drvTq  epsTq  apply lat cut  unav flt fail"


def format_row(row):
  t, dt_ms, kph, angle, rate, drv, eps, apply_tq, lat, cut, bits = row
  return "%s.%03d %6.1f %5.1f %7.1f %6.0f %6.0f %6.1f %6d %3d %3d  %4d %3d %4d" % (
    time.strftime("%H:%M:%S", time.localtime(t)), int((t % 1.0) * 1000), dt_ms, kph, angle, rate,
    drv, eps, apply_tq, int(lat), int(cut), bits[0], bits[1], bits[2])


class MdpsFaultLog:
  def __init__(self, log_dir=LOG_DIR, start_thread=True):
    self.log_dir = log_dir
    self.ring = collections.deque(maxlen=PRE_FRAMES)
    self.block = None      # rows of the fault being recorded
    self.trigger = None
    self.post_left = 0
    self.prev_fault = False
    self.count = 0
    self.out = queue.Queue(maxsize=8)
    if start_thread:
      threading.Thread(target=self._writer, daemon=True).start()

  def update(self, bits, row):
    """bits: (ToiUnavail, ToiFlt, FailStat) ints; row: tuple as in format_row without bits."""
    full = tuple(row) + (tuple(int(b) for b in bits),)
    fault = any(full[-1])
    if self.block is not None:
      self.block.append(full)
      self.post_left -= 1
      if self.post_left <= 0:
        self._flush()
    elif fault and not self.prev_fault:
      self.count += 1
      self.block = list(self.ring) + [full]
      self.trigger = full
      self.post_left = POST_FRAMES
    self.ring.append(full)
    self.prev_fault = fault

  def _flush(self):
    block, self.block = self.block, None
    try:
      self.out.put_nowait((self.count, block, self.trigger))
    except queue.Full:
      pass

  def text(self, count, block, trigger):
    # The frame whose fault bit turned on (rows before it can hold earlier, shorter faults).
    lines = ["== MDPS fault #%d at %s" % (count, format_row(trigger).split()[0]), HEADER]
    lines += [format_row(r) for r in block]
    return "\n".join(lines) + "\n"

  def _writer(self):
    while True:
      count, block, trigger = self.out.get()
      try:
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, "mdps_fault_%s.log" % time.strftime("%m%d"))
        with open(path, "a") as f:
          f.write(self.text(count, block, trigger))
      except OSError:
        pass
