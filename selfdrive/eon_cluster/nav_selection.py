"""Small, idempotent navigation selection requests on the HUD reply socket."""
import uuid


class NavSelectionSync:
  def __init__(self, params):
    self.params = params
    self.session = uuid.uuid4().hex
    self.ack = ""
    self.applied = {}

  def selected(self):
    try:
      return 2 if int(self.params.get("EonClusterHudNavApp") or 1) == 2 else 1
    except (ValueError, TypeError):
      return 1

  def receive(self, data, address):
    # Only the HUD's bound reply socket and this EON process's session qualify.
    if address[1] != 7210 or len(data) > 128:
      return False
    try:
      kind, session, request, app = data.decode("ascii").split(" ")
    except (UnicodeDecodeError, ValueError):
      return False
    if (kind != "HUDNAV1" or session != self.session or app not in ("1", "2") or
        len(request) != 32 or any(c not in "0123456789abcdef" for c in request)):
      return False
    if request in self.applied:
      if self.applied[request] != app:
        return False
    else:
      try:
        if self.selected() != int(app):
          self.params.put("EonClusterHudNavApp", app)
        if self.selected() != int(app):
          return False
      except Exception:
        return False
      if len(self.applied) >= 32:
        del self.applied[next(iter(self.applied))]
      self.applied[request] = app
    self.ack = request
    return True

  def telemetry(self):
    return {"hudNavSession": self.session, "hudNavRequestAck": self.ack,
            "hudNavApp": self.selected()}
