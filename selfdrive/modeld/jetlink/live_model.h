#pragma once
#include <algorithm>
#include <time.h>
#include <cmath>
#include <capnp/serialize.h>
#include "cereal/messaging/messaging.h"
#include "selfdrive/modeld/jetlink/live_gate.h"

namespace eon_jetlink {
template <typename List>
inline bool finite_list(List values, size_t size, bool nonnegative = false, bool probability = false) {
  if (values.size() != size) return false;
  for (float v : values) if (!std::isfinite(v) || (nonnegative && v < 0) || (probability && (v < 0 || v > 1))) return false;
  return true;
}
inline bool live_points(cereal::ModelDataV2::XYZTData::Reader p, bool exact_time = false) {
  if (!finite_list(p.getX(), 33) || !finite_list(p.getY(), 33) || !finite_list(p.getZ(), 33) || !finite_list(p.getT(), 33, true)) return false;
  float previous = 0;
  for (float t : p.getT()) { if (t < previous || t > 10.00001f) return false; previous = t; }
  if (exact_time) for (size_t i = 0; i < 33; ++i) {
    const float expected = 10.f * float(i * i) / 1024.f;
    if (std::abs(p.getT()[i] - expected) > 1e-6f) return false;
  }
  return true;
}
inline bool validate_live_model(cereal::ModelDataV2::Reader m) {
  if (!live_points(m.getPosition(), true) || !live_points(m.getVelocity(), true) || !live_points(m.getAcceleration(), true) ||
      !live_points(m.getOrientation(), true) || !live_points(m.getOrientationRate(), true)) return false;
  auto p = m.getPosition();
  if (!finite_list(p.getXStd(), 33, true) || !finite_list(p.getYStd(), 33, true) || !finite_list(p.getZStd(), 33, true)) return false;
  if (m.getLaneLines().size() != 4 || m.getRoadEdges().size() != 2 || m.getLeadsV3().size() != 3) return false;
  for (auto line : m.getLaneLines()) if (!live_points(line)) return false;
  for (auto edge : m.getRoadEdges()) if (!live_points(edge)) return false;
  if (!finite_list(m.getLaneLineProbs(), 4, false, true) || !finite_list(m.getLaneLineStds(), 4, true) || !finite_list(m.getRoadEdgeStds(), 2, true)) return false;
  size_t lead_index = 0;
  for (auto lead : m.getLeadsV3()) {
    if (lead.getProbTime() != float(lead_index++ * 2)) return false;
    for (size_t i = 0; i < lead.getT().size(); ++i) if (lead.getT()[i] != float(i * 2)) return false;
    if (!std::isfinite(lead.getProb()) || lead.getProb() < 0 || lead.getProb() > 1 || !std::isfinite(lead.getProbTime())) return false;
    if (!finite_list(lead.getT(), 6, true) || !finite_list(lead.getX(), 6) || !finite_list(lead.getY(), 6) || !finite_list(lead.getV(), 6) || !finite_list(lead.getA(), 6) ||
        !finite_list(lead.getXStd(), 6, true) || !finite_list(lead.getYStd(), 6, true) || !finite_list(lead.getVStd(), 6, true) || !finite_list(lead.getAStd(), 6, true)) return false;
  }
  auto meta = m.getMeta();
  if (!std::isfinite(meta.getEngagedProb()) || meta.getEngagedProb() < 0 || meta.getEngagedProb() > 1 ||
      !finite_list(meta.getDesireState(), 8, false, true) || !finite_list(meta.getDesirePrediction(), 32, false, true)) return false;
  float sum = 0;
  for (float v : meta.getDesireState()) sum += v;
  if (std::abs(sum - 1.f) > 1e-5f) return false;
  for (size_t row = 0; row < 4; ++row) {
    sum = 0;
    for (size_t i = 0; i < 8; ++i) sum += meta.getDesirePrediction()[row * 8 + i];
    if (std::abs(sum - 1.f) > 1e-5f) return false;
  }
  auto d = meta.getDisengagePredictions();
  for (size_t i = 0; i < d.getT().size(); ++i) if (d.getT()[i] != float((i + 1) * 2)) return false;
  return finite_list(d.getT(), 5, true) && finite_list(d.getGasDisengageProbs(), 5, false, true) &&
         finite_list(d.getBrakeDisengageProbs(), 5, false, true) && finite_list(d.getSteerOverrideProbs(), 5, false, true) &&
         finite_list(d.getBrake3MetersPerSecondSquaredProbs(), 5, false, true) &&
         finite_list(d.getBrake4MetersPerSecondSquaredProbs(), 5, false, true) && finite_list(d.getBrake5MetersPerSecondSquaredProbs(), 5, false, true);
}

// Parse/copy only after the cheap native gate passed. The original native
// prediction is still present and is published by the caller on any failure.
inline bool build_live_message(LiveGate &gate, MessageBuilder &out, uint32_t current_frame,
                               uint64_t current_camera_ns, uint32_t previous_frame, uint64_t previous_camera_ns,
                               uint64_t now_ns, bool engaged) {
  try {
    const auto &h = gate.packet();
    const auto control = gate.control();
    if (!control.enabled || control.revision != h.revision ||
        !((h.frame == current_frame && h.camera_ns == current_camera_ns) ||
          (h.frame == previous_frame && h.camera_ns == previous_camera_ns))) {
      gate.reject(engaged); return false;
    }
    capnp::ReaderOptions options;
    options.traversalLimitInWords = 32768;
    options.nestingLimit = 32;
    const auto &bytes = gate.payload();
    capnp::FlatArrayMessageReader reader(kj::arrayPtr(reinterpret_cast<const capnp::word*>(bytes.data()), bytes.size()), options);
    auto event = reader.getRoot<cereal::Event>();
    if (!event.isModelV2() || !event.getValid()) { gate.reject(engaged); return false; }
    auto model = event.getModelV2();
    if (model.getFrameId() != h.frame || model.getTimestampEof() != h.camera_ns || !validate_live_model(model)) {
      gate.reject(engaged); return false;
    }
    out.initEvent(true).setModelV2(model);
    auto copy = out.getRoot<cereal::Event>().getModelV2();
    copy.setFrameAge(uint32_t(current_frame - h.frame));
    copy.setModelExecutionTime(float(double(now_ns - h.started) / 1e9));
    return true;
  } catch (const kj::Exception &) {
    gate.reject(engaged); return false;
  }
}
struct PublicationContext {
  uint32_t frame, previous_frame;
  uint64_t camera_ns, previous_camera_ns, now_ns;
  bool enabled, native_ok, can_valid, calibrated, engaged;
};

template <typename EmitExternal, typename EmitNative>
bool publish_selected_model(LiveGate &gate, const PublicationContext &c, EmitExternal external, EmitNative native) {
  if (c.enabled && gate.candidate(c.frame, c.now_ns, c.native_ok, c.can_valid, c.calibrated, c.engaged)) {
    MessageBuilder message;
    if (build_live_message(gate, message, c.frame, c.camera_ns, c.previous_frame, c.previous_camera_ns, c.now_ns, c.engaged)) {
      if (external(message) >= 0) { gate.applied(); return true; }
      gate.reject(c.engaged);
    }
  } else if (!c.enabled) gate.fallback(c.engaged);
  native();
  return false;
}
}  // namespace eon_jetlink
