"""Unit checks for selfdrive.eon_cluster.hud_remote (run on a dev box or the EON)."""
import json
import os
import tempfile
import time

from selfdrive.eon_cluster.hud_remote import RemoteButtonSource, RemoteCommandSync, RemoteLaneChangeSource


class _Params:
  def __init__(self):
    self.store = {"EonClusterHudNavApp": "1"}

  def get(self, key):
    return self.store.get(key)

  def put(self, key, value):
    self.store[key] = value


def _req(sync, rid, cmd, session=None):
  return ("HUDCMD1 %s %s %s" % (session or sync.session, rid, cmd)).encode(), ("hud", 7210)


def main():
  path = os.path.join(tempfile.mkdtemp(), "cmd.json")
  params = _Params()
  lane_path = path + ".lane"
  sync = RemoteCommandSync(params, path, lane_path)
  lane = RemoteLaneChangeSource(lane_path)
  src = RemoteButtonSource(path)
  rid = "a" * 32
  assert not sync.receive(*_req(sync, rid, "res", session="x" * 32)), "wrong session must be rejected"
  assert not sync.receive(*_req(sync, rid, "steer_left")), "unknown command must be rejected"
  assert sync.receive(*_req(sync, rid, "res")) and sync.ack == rid
  assert sync.receive(*_req(sync, rid, "res")), "duplicate request id is acked"
  assert json.load(open(path))["seq"] == 1, "duplicate must not be re-applied"
  events = []
  for _ in range(12):
    events += [(e.type, e.pressed) for e in src.button_events()]
  assert [(str(t), p) for t, p in events] == [("accelCruise", True), ("accelCruise", False)], events
  for _ in range(20):
    assert not src.button_events(), "no repeat without a new command"
  assert sync.receive(*_req(sync, "b" * 32, "cancel"))
  events = []
  for _ in range(12):
    events += [(str(e.type), e.pressed) for e in src.button_events()]
  assert events == [("cancel", True), ("cancel", False)], events
  time.sleep(0.01)
  json.dump({"seq": 9, "cmd": "res", "ts": time.time() - 10}, open(path, "w"))
  events = []
  for _ in range(12):
    events += src.button_events()
  assert not events, "stale command must never fire"
  assert sync.receive(*_req(sync, "c" * 32, "nav_toggle")) and params.store["EonClusterHudNavApp"] == "2"
  t = sync.telemetry()
  assert t["hudCmdLast"] == "nav_toggle" and len(t["hudCmdSession"]) == 32
  # lane change request: held only while commands keep arriving (0.3 s each)
  assert lane.poll() == 0
  assert sync.receive(*_req(sync, "d" * 32, "lane_left"))
  d = 0
  for _ in range(4):
    d = lane.poll() or d
  assert d == -1, "remote left request"
  time.sleep(0.35)
  assert lane.poll() == 0, "request expires after the hold time"
  assert sync.receive(*_req(sync, "e" * 32, "lane_right"))
  d = 0
  for _ in range(4):
    d = lane.poll() or d
  assert d == 1
  print("hud_remote PASS")


if __name__ == "__main__":
  main()
