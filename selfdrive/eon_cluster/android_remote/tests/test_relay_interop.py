#!/usr/bin/env python3
"""Run the phone's RelayProtocol.java against EON's hud_remote.py end to end."""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, ROOT)
from selfdrive.eon_cluster import hud_remote as hr  # noqa: E402

KEY = "0a1b2c3d"


class Params:
  def __init__(self):
    self.store = {"HudRemoteKey": KEY.encode(), "EonClusterHudNavApp": b"1"}

  def get(self, name):
    return self.store.get(name)

  def put(self, name, value):
    self.store[name] = value.encode()


class Clock:
  now = 50.0

  def __call__(self):
    return self.now


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--java-home", default=os.environ.get("JAVA_HOME", "/usr"))
  ap.add_argument("--work", default=tempfile.mkdtemp())
  args = ap.parse_args()
  javac = os.path.join(args.java_home, "bin", "javac")
  java = os.path.join(args.java_home, "bin", "java")
  src = os.path.join(HERE, "..", "app", "src", "main", "java", "ai", "comma", "remotekey", "RelayProtocol.java")
  subprocess.check_call([javac, "-d", args.work, src, os.path.join(HERE, "RelayDriver.java")])
  proc = subprocess.Popen([java, "-cp", args.work, "ai.comma.remotekey.RelayDriver", KEY],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

  def phone(line):
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    return proc.stdout.readline().strip()

  clock = Clock()
  tmp = tempfile.mkdtemp()
  eon = hr.RemoteCommandSync(Params(), os.path.join(tmp, "cmd.json"), os.path.join(tmp, "lane.json"), clock)
  drive = hr.allowed_commands(True, True, True, False, False)

  def to_eon(datagram):
    reply = eon.handle(datagram.encode(), drive)
    return reply.decode() if reply else None

  # A press before EON answers is queued, sent once a session arrives, and
  # given up after GIVE_UP_MS if EON never answers.
  assert phone("submit res") == "queued"
  assert phone("command") == "-"
  phone("advance 1600")
  assert phone("command") == "-"  # the relay loop polls this; it expires the press
  assert phone("state").startswith("idle|res · EON 연결 안 됨"), phone("state")
  assert phone("submit set") == "queued"
  sess = to_eon(phone("hello"))
  assert sess and sess.startswith("SESS3 "), sess
  assert phone("receive " + sess) == "eon"
  ack = to_eon(phone("command"))
  assert " accepted " in ack, ack
  assert phone("receive " + ack) == "eon"
  phone("advance 2100")
  sess = to_eon(phone("hello"))
  assert sess and sess.startswith("SESS3 "), sess
  assert phone("receive " + sess) == "eon"
  assert phone("submit res") == "queued"
  first = phone("command")
  assert first.startswith("CMD3 "), first
  # First datagram lost on Wi-Fi: the phone resends after RETRY_MS, EON applies once.
  phone("advance 160")
  second = phone("command")
  assert second == first, (first, second)
  ack = to_eon(second)
  assert ack.endswith(ack.split(" ")[-1]) and " accepted " in ack, ack
  assert phone("receive " + ack) == "eon"
  state = phone("state")
  assert state.startswith("idle|res · EON 수신"), state
  # A late duplicate of the first datagram is answered but not applied twice.
  assert " accepted " in to_eon(first)
  assert [e["cmd"] for e in eon.events].count("res") == 1, eon.events

  # Token expiry: EON answers 'stale', the phone re-hellos and resends the same request.
  assert phone("submit lane_left") == "queued"
  cmd = phone("command")
  clock.now += hr.TOKEN_TTL_S + 1
  stale = to_eon(cmd)
  assert stale.split(" ")[2] == "stale", stale
  assert phone("receive " + stale) == "eon"
  assert phone("command") == "-"
  phone("advance 300")  # fast HELLO pacing while a command waits for a session
  sess = to_eon(phone("hello"))
  assert phone("receive " + sess) == "eon"
  ack = to_eon(phone("command"))
  assert " accepted " in ack, ack
  phone("receive " + ack)
  assert phone("state").startswith("idle|lane_left · EON 수신")

  # Remote disconnected: the next HELLO goes out at once and the HUD shows it.
  assert eon.hud_status()["link"] == 2
  sess = to_eon(phone("hello0"))
  assert sess and sess.startswith("SESS3 "), sess
  assert eon.hud_status()["link"] == 1, eon.hud_status()

  # Wrong key on the phone: EON stays silent.
  bad = subprocess.run([java, "-cp", args.work, "ai.comma.remotekey.RelayDriver", "ffffffff"],
                       input="hello\n", capture_output=True, text=True).stdout.strip()
  assert to_eon(bad) is None
  proc.stdin.close()
  proc.wait(5)
  print("relay interop: phone RelayProtocol <-> EON hud_remote OK")


if __name__ == "__main__":
  main()
