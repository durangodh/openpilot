#!/usr/bin/env python3
"""Send one test text to the Genesis DH stock HUD over MM-CAN and see where it shows.

The AVN sends two kinds of text on 0x4A9 (ISO-TP, the HUD answers 0x4B9 30 00 05):
  name  F0 + UTF-16LE              the direction / road name under the turn picture
  dest  F4 00 + 22 UTF-16LE chars  the destination name, sent once when guidance starts
        (2026-10-31 08:21 "우리치과의원": 10 2F F4 00 B0 C6 AC B9 ..., cleared with zeros on
        arrival and reroute). Where the HUD draws it is unknown: this script is to find out.

Run parked, over SSH, with openpilot running, STOCK HUD NAVER TBT (M-CAN) on and no
destination in the navi app (so nothing else of ours goes to the HUD):
  python3 selfdrive/debug/mcan_hud_text.py 테스트                   # dest slot, no picture
  python3 selfdrive/debug/mcan_hud_text.py 테스트 --slot name       # name slot, text only
  python3 selfdrive/debug/mcan_hud_text.py 테스트 --picture start   # with the start screen
The text stays for --seconds (default 10), then it is cleared and the HUD is the AVN's again.
"""
import argparse
import time

from selfdrive.car import make_can_msg
from selfdrive.car.hyundai.mcan_hud import (ARRIVED_PAYLOAD, BAR_ADDR, BAR_PREFIX_DEFAULT, MCAN_BUS, TBT_ADDR,
                                            TEXT_ADDR, isotp_frames, tbt_payload, text_payload)

FLOW_ADDR = 0x4B9
DEST_CHARS = 22
# 0x115 frames the AVN sent itself: its start screen with the route length (10-31 14:49
# "20.0km"), the plain keep-going arrow, the arrival flag.
PICTURES = {
  "none": None,
  "start": bytes.fromhex("0200002d001410f0"),
  "straight": tbt_payload(11, 1000),
  "arrived": ARRIVED_PAYLOAD,
}


def dest_payload(text):
  """The AVN's destination-name message: F4 00, 22 UTF-16LE characters zero padded, 00."""
  raw = (text or "")[:DEST_CHARS].encode("utf-16-le")
  return b"\xf4\x00" + raw.ljust(DEST_CHARS * 2, b"\x00") + b"\x00"


def text_frames(slot, text):
  return isotp_frames(dest_payload(text) if slot == "dest" else text_payload(text))


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("text")
  parser.add_argument("--slot", choices=("dest", "name"), default="dest")
  parser.add_argument("--picture", choices=sorted(PICTURES), default="none")
  parser.add_argument("--seconds", type=float, default=10.0)
  args = parser.parse_args()

  import cereal.messaging as messaging
  from selfdrive.boardd.boardd import can_list_to_can_capnp

  sendcan = messaging.pub_sock('sendcan')
  logcan = messaging.sub_sock('can', conflate=False)
  time.sleep(1.0)

  def send(msgs):
    sendcan.send(can_list_to_can_capnp(msgs, msgtype='sendcan'))

  def flow_controls():
    n = 0
    for evt in messaging.drain_sock(logcan):
      n += sum(1 for c in evt.can if c.src == MCAN_BUS and c.address == FLOW_ADDR)
    return n

  picture = PICTURES[args.picture]
  # (time offset, frame): the first frame 150 ms after the picture like the AVN, the rest
  # 30 ms later and 10 ms apart; the clearing text at the end.
  schedule = []
  for at, frames in ((0.15, text_frames(args.slot, args.text)), (args.seconds, text_frames(args.slot, ""))):
    for i, frame in enumerate(frames):
      schedule.append((at + (0.03 + 0.01 * (i - 1) if i else 0.0), frame))

  flow_controls()
  answered = 0
  start = time.monotonic()
  next_picture = 0.0
  end = args.seconds + 0.5
  print(f"sending {args.slot} text {args.text!r}, picture {args.picture}, {args.seconds:.0f} s")
  while True:
    now = time.monotonic() - start
    if now > end:
      break
    msgs = []
    if picture is not None and now >= next_picture and now < args.seconds:
      next_picture = now + 0.05
      msgs.append(make_can_msg(TBT_ADDR, picture, MCAN_BUS))
      msgs.append(make_can_msg(BAR_ADDR, BAR_PREFIX_DEFAULT + b"\x00", MCAN_BUS))
    while schedule and schedule[0][0] <= now:
      msgs.append(make_can_msg(TEXT_ADDR, schedule.pop(0)[1], MCAN_BUS))
    if msgs:
      send(msgs)
    answered += flow_controls()
    time.sleep(0.005)

  # Every multi-frame text is answered once; none means the HUD never got the first frame
  # (M-CAN toggle off, openpilot not running, or the wiring).
  print(f"HUD flow control answers (0x4B9): {answered}")
  if answered == 0:
    print("no answer: the text did not reach the HUD (a short name-slot text needs none)")


if __name__ == "__main__":
  main()
