from pathlib import Path

p = Path('selfdrive/controls/controlsd.py')
s = p.read_text()
old = '    self.carrot_lat_learner = CarrotLatLearner()'
new = '    self.carrot_lat_learner = CarrotLatLearner(self.CP)'
assert old in s
s = s.replace(old, new, 1)
old = "    self.carrot_lat_learner.tick(CS, CC.latActive, getattr(self, 'desired_curvature', 0.0))"
new = "    self.carrot_lat_learner.tick(CS, CC.latActive, getattr(self, 'desired_curvature', 0.0), self.sm['modelV2'])"
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)

p = Path('selfdrive/controls/lib/latcontrol_torque.py')
s = p.read_text()
start = s.index('  def read_torque_params(self, force=False):')
end = s.index('\n  def update_live_torque_params', start)
new_block = '''  def read_torque_params(self, force=False):
    custom = int(self._pget("LateralTorqueCustom", 0))
    carrot_active = self.params.get_bool("CarrotLearningActive")
    prev_carrot_active = getattr(self, "carrot_learning_active", False)

    if custom > 0:
      # Highest priority: explicit manual torque tuning.
      self.torque_params.latAccelFactor = self._pget("LateralTorqueAccelFactor", 2700) * 0.001
      self.torque_params.friction = self._pget("LateralTorqueFriction", 80) * 0.001
      self.pid._k_p = [[0], [self._pget("LateralTorqueKpV", 10) * 0.01]]
      self.pid._k_i = [[0], [self._pget("LateralTorqueKiV", 10) * 0.01]]
      self.pid.k_f = self._pget("LateralTorqueKf", 100) * 0.01
      self.pid._k_d = [[0], [self._pget("LateralTorqueKd", 0) * 0.01]]
    elif carrot_active:
      # Full Carrot Phase 2 auto-tune owns torque parameters while enabled.
      self.torque_params.latAccelFactor = self._pget("LateralTorqueAccelFactor", self.latAccelFactor_default * 1000.0) * 0.001
      self.torque_params.friction = self._pget("LateralTorqueFriction", self.friction_default * 1000.0) * 0.001
      self.pid._k_p = [[0], [self._pget("LateralTorqueKpV", self.kp_default * 100.0) * 0.01]]
      self.pid._k_i = [[0], [self._pget("LateralTorqueKiV", self.ki_default * 100.0) * 0.01]]
      self.pid.k_f = self._pget("LateralTorqueKf", self.kf_default * 100.0) * 0.01
      self.pid._k_d = [[0], [self._pget("LateralTorqueKd", self.kd_default * 100.0) * 0.01]]
    elif self.lateral_torque_custom > 0 or prev_carrot_active or force:
      self.torque_params.latAccelFactor = self.latAccelFactor_default
      self.torque_params.friction = self.friction_default
      self.pid._k_p = [[0], [self.kp_default]]
      self.pid._k_i = [[0], [self.ki_default]]
      self.pid.k_f = self.kf_default
      self.pid._k_d = [[0], [self.kd_default]]
    self.lateral_torque_custom = custom
    self.carrot_learning_active = carrot_active

    self.lat_accel_friction_factor = self._pget("LatAccelFrictionFactor", 70) * 0.01
    self.lat_jerk_friction_factor = self._pget("LatJerkFrictionFactor", 40) * 0.01
    self.desired_lat_jerk_time = max(
      0.1, self._pget("SteerActuatorDelay", 10) * 0.01 + 0.3)
    self.friction_upper_idx = next(
      (i for i, t in enumerate(T_IDXS) if t > max(self.desired_lat_jerk_time, 0.1)),
      len(T_IDXS))
'''
s = s[:start] + new_block + s[end:]
old = '    if self.lateral_torque_custom > 0:\n      return\n    self.torque_params.latAccelFactor = latAccelFactor'
new = '    if self.lateral_torque_custom > 0 or getattr(self, "carrot_learning_active", False):\n      return\n    self.torque_params.latAccelFactor = latAccelFactor'
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)

p = Path('selfdrive/ui/qt/onroad.cc')
s = p.read_text()
old = '''  static const QMap<QString, QString> kLabels = {
    {"SteerActuatorDelay", "조향 지연 보정 (SteerActuatorDelay)"},
    {"CustomSteerRatio", "조향비 (CustomSteerRatio)"},
  };'''
new = '''  static const QMap<QString, QString> kLabels = {
    {"SteerActuatorDelay", "조향 지연 보정 (SteerActuatorDelay)"},
    {"CustomSteerRatio", "조향비 (SteerRatioRate→CustomSteerRatio)"},
    {"OffsetTotal", "차선 중심 오프셋 (PathOffset→OffsetTotal)"},
    {"LateralTorqueAccelFactor", "토크 AccelFactor"},
    {"LateralTorqueKf", "토크 Kf"},
    {"LateralTorqueFriction", "토크 Friction"},
    {"LateralTorqueKpV", "토크 Kp"},
    {"LateralTorqueKiV", "토크 Ki"},
  };'''
assert old in s
s = s.replace(old, new, 1)
old = '''    msg += QString("%1: %2 → %3\\n")
      .arg(label)
      .arg(entry.value("current").toInt())
      .arg(entry.value("recommend").toInt());'''
new = '''    if (it.key() == "OffsetTotal") {
      msg += QString("%1: %2 → %3 m\\n")
        .arg(label)
        .arg(entry.value("current").toDouble(), 0, 'f', 2)
        .arg(entry.value("recommend").toDouble(), 0, 'f', 2);
    } else {
      msg += QString("%1: %2 → %3\\n")
        .arg(label)
        .arg(entry.value("current").toInt())
        .arg(entry.value("recommend").toInt());
    }'''
assert old in s
s = s.replace(old, new, 1)
old = '''    static const QMap<QString, QPair<int, int>> kBounds = {
      {"SteerActuatorDelay", {15, 40}},
      {"CustomSteerRatio", {1000, 2000}},
    };
    for (auto it = rec.constBegin(); it != rec.constEnd(); ++it) {
      if (!kBounds.contains(it.key())) continue;
      int v = it.value().toObject().value("recommend").toInt();
      QPair<int, int> bounds = kBounds[it.key()];
      v = std::max(bounds.first, std::min(bounds.second, v));
      params.put(it.key().toStdString(), std::to_string(v));
    }'''
new = '''    static const QMap<QString, QPair<int, int>> kBounds = {
      {"SteerActuatorDelay", {15, 40}},
      {"CustomSteerRatio", {1200, 1900}},
      {"LateralTorqueAccelFactor", {1000, 6000}},
      {"LateralTorqueKf", {0, 200}},
      {"LateralTorqueFriction", {10, 300}},
      {"LateralTorqueKpV", {30, 200}},
      {"LateralTorqueKiV", {0, 50}},
    };
    for (auto it = rec.constBegin(); it != rec.constEnd(); ++it) {
      if (it.key() == "OffsetTotal") {
        double v = it.value().toObject().value("recommend").toDouble();
        v = std::max(-0.30, std::min(0.30, v));
        params.put("OffsetTotal", QString::number(v, 'f', 2).toStdString());
        continue;
      }
      if (!kBounds.contains(it.key())) continue;
      int v = it.value().toObject().value("recommend").toInt();
      QPair<int, int> bounds = kBounds[it.key()];
      v = std::max(bounds.first, std::min(bounds.second, v));
      params.put(it.key().toStdString(), std::to_string(v));
    }'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)
