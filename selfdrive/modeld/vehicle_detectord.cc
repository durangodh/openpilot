#include <sys/resource.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include <eigen3/Eigen/Dense>

#include "cereal/messaging/messaging.h"
#include "cereal/visionipc/visionipc_client.h"
#include "selfdrive/common/modeldata.h"
#include "selfdrive/common/params.h"
#include "selfdrive/common/swaglog.h"
#include "selfdrive/common/timing.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/modeld/models/vehicle_detector.h"

namespace {

constexpr char MODEL_PATH[] = "../../models/vehicle_detector.dlc";
constexpr char CONFIG_PATH[] = "../../models/vehicle_detector.json";
constexpr char OUTPUT_PATH[] = "/dev/shm/vision_vehicle_objects.json";
constexpr char OUTPUT_TEMP_PATH[] = "/dev/shm/vision_vehicle_objects.json.tmp";
constexpr float CAMERA_TO_BUMPER = 1.52f;
constexpr double PARAM_REFRESH_MS = 1000.0;
constexpr double RETRY_MS = 10000.0;
constexpr double TRACK_HOLD_MS = 750.0;
constexpr float THERMAL_PAUSE_C = 82.0f;
constexpr float THERMAL_RESUME_C = 78.0f;

ExitHandler do_exit;

int param_int(Params &params, const std::string &key, int fallback,
              int minimum, int maximum) {
  const std::string raw = params.get(key);
  if (raw.empty()) return fallback;
  char *end = nullptr;
  const long value = std::strtol(raw.c_str(), &end, 10);
  if (end == raw.c_str() || *end != '\0') return fallback;
  return static_cast<int>(std::max<long>(minimum, std::min<long>(maximum, value)));
}

int64_t unix_millis() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count();
}

void clear_output() {
  unlink(OUTPUT_TEMP_PATH);
  unlink(OUTPUT_PATH);
}

class StatusWriter {
public:
  explicit StatusWriter(Params *params_in) : params_(params_in) {}

  void set(const std::string &value) {
    if (value == previous) return;
    params_->put("EonClusterHudVisionDetectorStatus", value);
    previous = value;
    LOGW("vehicle detector status: %s", value.c_str());
  }

private:
  Params *params_;
  std::string previous;
};

struct GroundDetection {
  float distance = 0.0f;
  float lateral = 0.0f;
  float confidence = 0.0f;
  int class_id = -1;
};

struct Track {
  int id = 0;
  float distance = 0.0f;
  float lateral = 0.0f;
  float confidence = 0.0f;
  int class_id = -1;
  double last_seen_ms = 0.0;
  bool matched = false;
};

class VehicleTracker {
public:
  void update(const std::vector<GroundDetection> &detections, double now_ms) {
    for (auto &track : tracks) track.matched = false;
    for (const auto &detection : detections) {
      Track *best = nullptr;
      float best_cost = std::numeric_limits<float>::infinity();
      for (auto &track : tracks) {
        if (track.matched) continue;
        const float distance_limit = std::max(4.0f, detection.distance * 0.16f);
        const float distance_error = std::abs(track.distance - detection.distance);
        const float lateral_error = std::abs(track.lateral - detection.lateral);
        if (distance_error > distance_limit || lateral_error > 2.2f) continue;
        const float cost = distance_error / distance_limit + lateral_error / 2.2f;
        if (cost < best_cost) {
          best = &track;
          best_cost = cost;
        }
      }
      if (best == nullptr) {
        tracks.push_back({next_id++, detection.distance, detection.lateral,
                          detection.confidence, detection.class_id, now_ms, true});
      } else {
        constexpr float alpha = 0.62f;
        best->distance += (detection.distance - best->distance) * alpha;
        best->lateral += (detection.lateral - best->lateral) * alpha;
        best->confidence = std::max(detection.confidence,
                                    best->confidence * 0.82f);
        best->class_id = detection.class_id;
        best->last_seen_ms = now_ms;
        best->matched = true;
      }
    }
    tracks.erase(std::remove_if(tracks.begin(), tracks.end(),
                                [now_ms](const Track &track) {
                                  return now_ms - track.last_seen_ms > TRACK_HOLD_MS;
                                }),
                 tracks.end());
  }

  const std::vector<Track> &get() const { return tracks; }

private:
  int next_id = 1;
  std::vector<Track> tracks;
};

bool update_road_from_pixel(SubMaster &sm, Eigen::Matrix3f *road_from_pixel) {
  if (!sm.alive("liveCalibration") || !sm.valid("liveCalibration")) return false;
  const auto extrinsic_values = sm["liveCalibration"].getLiveCalibration().getExtrinsicMatrix();
  if (extrinsic_values.size() != 12) return false;

  Eigen::Matrix<float, 3, 4, Eigen::RowMajor> extrinsic;
  for (size_t i = 0; i < 12; ++i) extrinsic.data()[i] = extrinsic_values[i];
  Eigen::Matrix3f intrinsics;
  for (int i = 0; i < 9; ++i) intrinsics.data()[i] = fcam_intrinsic_matrix.v[i];
  // mat3 is row-major while Eigen::Matrix3f is column-major.
  intrinsics.transposeInPlace();
  const Eigen::Matrix<float, 3, 4> projection = intrinsics * extrinsic;
  Eigen::Matrix3f pixel_from_road;
  pixel_from_road.col(0) = projection.col(0);
  pixel_from_road.col(1) = projection.col(1);
  pixel_from_road.col(2) = projection.col(3);
  if (!std::isfinite(pixel_from_road.determinant()) ||
      std::abs(pixel_from_road.determinant()) < 1e-6f) return false;
  *road_from_pixel = pixel_from_road.inverse();
  return road_from_pixel->allFinite();
}

std::vector<GroundDetection> to_ground_detections(
    const std::vector<VehicleDetection> &detections,
    const VehicleLetterbox &letterbox, int input_width, int input_height,
    const Eigen::Matrix3f &road_from_pixel) {
  std::vector<GroundDetection> ground;
  for (const auto &detection : detections) {
    const float model_x = (detection.left + detection.right) * 0.5f * input_width;
    const float model_y = detection.bottom * input_height;
    const float pixel_x = (model_x - letterbox.pad_x) / letterbox.scale;
    const float pixel_y = (model_y - letterbox.pad_y) / letterbox.scale;
    if (pixel_x < 0.0f || pixel_x >= letterbox.source_width ||
        pixel_y < 0.0f || pixel_y >= letterbox.source_height) continue;
    Eigen::Vector3f point = road_from_pixel * Eigen::Vector3f(pixel_x, pixel_y, 1.0f);
    if (!point.allFinite() || std::abs(point.z()) < 1e-5f) continue;
    point /= point.z();
    const float distance = point.x() - CAMERA_TO_BUMPER;
    const float lateral = point.y();
    if (!std::isfinite(distance) || !std::isfinite(lateral) ||
        distance < 2.0f || distance > 120.0f || std::abs(lateral) > 15.0f) continue;
    ground.push_back({distance, lateral, detection.confidence, detection.class_id});
  }
  return ground;
}

bool write_objects(const VehicleTracker &tracker, double inference_ms) {
  std::ofstream file(OUTPUT_TEMP_PATH, std::ios::out | std::ios::trunc);
  if (!file.is_open()) return false;
  file << std::fixed << std::setprecision(3)
       << "{\"updated_at_ms\":" << unix_millis()
       << ",\"inference_ms\":" << inference_ms << ",\"objects\":[";
  bool first = true;
  for (const auto &track : tracker.get()) {
    if (!first) file << ',';
    first = false;
    file << "{\"id\":" << track.id
         << ",\"d\":" << track.distance
         << ",\"y\":" << track.lateral
         << ",\"p\":" << track.confidence
         << ",\"class\":" << track.class_id << '}';
  }
  file << "]}\n";
  file.flush();
  if (!file.good()) {
    file.close();
    unlink(OUTPUT_TEMP_PATH);
    return false;
  }
  file.close();
  return std::rename(OUTPUT_TEMP_PATH, OUTPUT_PATH) == 0;
}

float maximum_cpu_temperature(SubMaster &sm) {
  if (!sm.alive("deviceState") || !sm.valid("deviceState")) return 0.0f;
  float maximum = 0.0f;
  for (const float value : sm["deviceState"].getDeviceState().getCpuTempC()) {
    if (std::isfinite(value)) maximum = std::max(maximum, value);
  }
  return maximum;
}

bool run_detector_session(Params &params, StatusWriter &status,
                          VehicleDetectorConfig config) {
  const int threshold_percent = param_int(params, "EonClusterHudVisionDetectorThreshold",
                                          55, 30, 90);
  config.confidence_threshold = threshold_percent * 0.01f;
  VehicleDetector detector;
  std::string error;
  status.set("loading");
  if (!detector.load(MODEL_PATH, config, &error)) {
    status.set("model_error:" + error.substr(0, 80));
    clear_output();
    return false;
  }

  VisionIpcClient camera("camerad", VISION_STREAM_RGB_ROAD, true);
  while (!do_exit && !camera.connect(false)) util::sleep_for(100);
  if (do_exit) return true;

  SubMaster sm({"liveCalibration", "deviceState"});
  VehicleTracker tracker;
  Eigen::Matrix3f road_from_pixel = Eigen::Matrix3f::Zero();
  bool calibration_valid = false;
  bool thermal_paused = false;
  double next_inference_ms = 0.0;
  double next_param_refresh_ms = 0.0;
  double cooldown_until_ms = 0.0;
  int fps = param_int(params, "EonClusterHudVisionDetectorFps", 2, 1, 3);
  int slow_streak = 0;

  while (!do_exit) {
    VisionIpcBufExtra extra = {};
    VisionBuf *buffer = camera.recv(&extra, 200);
    sm.update(0);
    const double now_ms = millis_since_boot();

    if (now_ms >= next_param_refresh_ms) {
      next_param_refresh_ms = now_ms + PARAM_REFRESH_MS;
      if (!params.getBool("EonClusterHud") ||
          !params.getBool("EonClusterHudVisionDetector")) {
        status.set("disabled");
        clear_output();
        return true;
      }
      const int new_threshold = param_int(params, "EonClusterHudVisionDetectorThreshold",
                                          55, 30, 90);
      if (new_threshold != threshold_percent) return true;  // reload config safely
      fps = param_int(params, "EonClusterHudVisionDetectorFps", 2, 1, 3);
    }
    if (sm.updated("liveCalibration")) {
      calibration_valid = update_road_from_pixel(sm, &road_from_pixel);
    }
    const float maximum_temperature = maximum_cpu_temperature(sm);
    if (!thermal_paused && maximum_temperature >= THERMAL_PAUSE_C) thermal_paused = true;
    if (thermal_paused && maximum_temperature > 0.0f &&
        maximum_temperature <= THERMAL_RESUME_C) thermal_paused = false;
    if (thermal_paused) {
      status.set("thermal_pause");
      clear_output();
      continue;
    }
    if (now_ms < cooldown_until_ms) continue;
    if (buffer == nullptr || now_ms < next_inference_ms) continue;
    next_inference_ms = now_ms + 1000.0 / fps;
    if (!calibration_valid) {
      status.set("waiting_calibration");
      clear_output();
      continue;
    }

    VehicleLetterbox letterbox;
    if (!detector.preprocess_bgr(static_cast<const uint8_t *>(buffer->addr),
                                 static_cast<int>(buffer->width),
                                 static_cast<int>(buffer->height),
                                 static_cast<int>(buffer->stride),
                                 &letterbox)) {
      status.set("preprocess_error");
      clear_output();
      return false;
    }
    std::vector<VehicleDetection> detections;
    double inference_ms = 0.0;
    if (!detector.execute(&detections, &inference_ms, &error)) {
      status.set("inference_error:" + error.substr(0, 80));
      clear_output();
      return false;
    }
    slow_streak = inference_ms > 250.0 ? slow_streak + 1 : 0;
    if (slow_streak >= 3) {
      status.set("slow_cooldown");
      clear_output();
      cooldown_until_ms = now_ms + 5000.0;
      slow_streak = 0;
      continue;
    }

    const auto ground = to_ground_detections(
        detections, letterbox, config.input_width, config.input_height,
        road_from_pixel);
    tracker.update(ground, now_ms);
    if (!write_objects(tracker, inference_ms)) {
      status.set("output_error");
      clear_output();
      return false;
    }
    status.set("running");
  }
  return true;
}

}  // namespace

int main() {
  setpriority(PRIO_PROCESS, 0, 10);
  Params params;
  StatusWriter status(&params);
  clear_output();

  while (!do_exit) {
    if (!Hardware::EON()) {
      status.set("unsupported_hardware");
      break;
    }
    if (!params.getBool("EonClusterHud") ||
        !params.getBool("EonClusterHudVisionDetector")) {
      status.set("disabled");
      clear_output();
      util::sleep_for(500);
      continue;
    }
    if (access(MODEL_PATH, R_OK) != 0) {
      status.set("model_missing");
      clear_output();
      util::sleep_for(static_cast<int>(RETRY_MS));
      continue;
    }
    VehicleDetectorConfig config;
    std::string error;
    if (!load_vehicle_detector_config(CONFIG_PATH, &config, &error)) {
      status.set("config_error:" + error.substr(0, 80));
      clear_output();
      util::sleep_for(static_cast<int>(RETRY_MS));
      continue;
    }
    run_detector_session(params, status, config);
    util::sleep_for(500);
  }
  clear_output();
  return 0;
}
