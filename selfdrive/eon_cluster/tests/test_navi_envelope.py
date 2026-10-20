"""Run the real JSON receiver without EON native imports or background threads."""
import ast
from pathlib import Path
import threading
import time
import unittest
import uuid


def receiver():
  source = Path(__file__).resolve().parents[2] / "carrot_navi_server.py"
  tree = ast.parse(source.read_text())
  names = {"JSON_NAMES", "PROTOCOL_VERSION", "SOURCE_TMAP", "SOURCE_NAVER"}
  nodes = [node for node in tree.body if
           (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)) or
           (isinstance(node, ast.FunctionDef) and node.name == "accept_json_envelope") or
           (isinstance(node, ast.ClassDef) and node.name == "NaviState")]
  namespace = {"threading": threading, "time": time, "uuid": uuid}
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
  state = namespace["NaviState"].__new__(namespace["NaviState"])
  state.lock = threading.Lock()
  state.condition = threading.Condition(state.lock)
  state.values, state.updated_at, state.json_sequences = {}, {}, {}
  state.accepts = lambda source: source == "tmap"
  state.control_connected(object())
  return state


class TestNaviEnvelope(unittest.TestCase):
  def setUp(self):
    self.state = receiver()
    self.connection = {}

  def message(self, sequence=0, **changes):
    msg = dict(type="item_update", kind="json", name="guidance_current",
               session_id=self.state.session_id, manifest_revision=1,
               stream_handle=2, protocol_version=2, schema_version=1,
               sequence=sequence, present=True)
    msg.update(changes)
    return msg

  def send(self, msg, value=None, connection=None):
    self.state.update("tmap", msg.get("name", "guidance_current"), value, msg,
                      self.connection if connection is None else connection)

  def test_duplicate_and_reversed_updates_do_not_refresh_or_clear_guidance(self):
    self.send(self.message(4), {"distance_m": 400})
    timestamp = self.state.updated_at["guidance_current"]
    self.send(self.message(3), {"distance_m": 500})
    self.send(self.message(4, present=False))
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 400})
    self.assertEqual(self.state.updated_at["guidance_current"], timestamp)
    self.send(self.message(5, present=False))
    self.assertIsNone(self.state.values["guidance_current"])

  def test_new_control_session_rejects_old_data_and_restarts_sequence(self):
    old = self.message(99)
    self.send(old, {"distance_m": 400})
    self.state.control_connected(object())
    self.send(old, {"distance_m": 900})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 400})
    self.send(self.message(0), {"distance_m": 300}, {})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 300})
    self.state.control_disconnected(self.state.control_conn)
    self.send(self.message(1), {"distance_m": 800})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 300})

  def test_bad_metadata_cannot_poison_the_sequence(self):
    for changes in ({"session_id": "old"}, {"manifest_revision": 2},
                    {"stream_handle": 3}, {"sequence": True}, {"sequence": -1},
                    {"sequence": "1"}, {"kind": "image"}, {"protocol_version": 3},
                    {"schema_version": 2}):
      self.send(dict(self.message(100), **changes), {"distance_m": 999})
      self.assertEqual(self.state.values, {})
    self.send(self.message(), {"distance_m": 100})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 100})

  def test_sequences_are_independent_per_stream_and_shared_across_strict_sockets(self):
    self.send(self.message(20), {"distance_m": 300})
    self.send(self.message(19), {"distance_m": 999}, {})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 300})
    self.send(self.message(0, name="lane_current", stream_handle=4), {"count": 3})
    self.assertEqual(self.state.values["lane_current"], {"count": 3})

  def test_installed_legacy_bridges_keep_working(self):
    self.send({"name": "guidance_current"}, {"distance_m": 200})
    self.send({"name": "guidance_current"}, {"distance_m": 100})
    self.assertEqual(self.state.values["guidance_current"], {"distance_m": 100})
    self.send({"name": "guidance_current", "sequence": 10}, 10)
    self.send({"name": "guidance_current", "sequence": 9}, 9)
    self.assertEqual(self.state.values["guidance_current"], 10)
    self.send({"name": "guidance_current", "sequence": 0}, 0, {})
    self.assertEqual(self.state.values["guidance_current"], 0)

  def test_strict_socket_cannot_downgrade_or_send_partial_metadata(self):
    self.send(self.message(), 1)
    self.send({"name": "guidance_current"}, 2)
    self.assertEqual(self.state.values["guidance_current"], 1)
    self.send({"name": "guidance_current", "session_id": self.state.session_id}, 3, {})
    self.assertEqual(self.state.values["guidance_current"], 1)


if __name__ == "__main__":
  unittest.main()
