import selfdrive.eon_cluster.usb_display as usb_display_module
from selfdrive.eon_cluster.usb_display import (
  CMD_BRIGHTNESS,
  CMD_FRAME_RATE,
  CMD_UPLOAD_JPEG,
  TurzxDisplay,
  _command_packet,
)


def test_vendor_command_packet_has_expected_envelope():
  packet = _command_packet(CMD_UPLOAD_JPEG, {8: 1, 11: 2})
  assert len(packet) == 512
  assert packet[-2:] == b"\xa1\x1a"
  assert packet != bytes(512)


def test_live_display_settings_send_only_when_changed(monkeypatch):
  display = TurzxDisplay(brightness=65, frame_rate=10)
  commands = []
  monkeypatch.setattr(display, "_send_optional_command",
                      lambda command, fields=None, **kwargs: commands.append((command, fields)))
  assert display.set_brightness(42)
  assert display.set_frame_rate(12)
  assert not display.set_brightness(42)
  assert not display.set_frame_rate(12)
  assert commands == [(CMD_BRIGHTNESS, {8: 42}), (CMD_FRAME_RATE, {8: 12})]


def test_jpeg_drains_stale_reply_before_next_frame(monkeypatch):
  display = TurzxDisplay()
  events = []
  monkeypatch.setattr(usb_display_module, "_command_packet", lambda *_args, **_kwargs: b"header")
  monkeypatch.setattr(display, "_drain_input",
                      lambda attempts, timeout_ms: events.append(("drain", attempts, timeout_ms)))
  monkeypatch.setattr(display, "_write_no_ack",
                      lambda payload, timeout_ms: events.append(("write", payload, timeout_ms)))
  display.send_jpeg(b"jpeg")
  assert events == [
    ("drain", 2, 2),
    ("write", b"headerjpeg", 2000),
  ]
