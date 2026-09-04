#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct VehicleDetectorConfig {
  int input_width = 300;
  int input_height = 300;
  int max_detections = 24;
  float input_mean = 128.0f;
  float input_scale = 1.0f / 128.0f;
  float confidence_threshold = 0.55f;
  std::string boxes_tensor = "boxes";
  std::string scores_tensor = "scores";
  std::string classes_tensor = "classes";
  std::string count_tensor = "num_detections";
  std::string boxes_format = "yxyx";
  std::vector<int> vehicle_class_ids = {2, 3, 4, 6, 8};
};

struct VehicleDetection {
  float left = 0.0f;
  float top = 0.0f;
  float right = 0.0f;
  float bottom = 0.0f;
  float confidence = 0.0f;
  int class_id = -1;
};

struct VehicleLetterbox {
  float scale = 1.0f;
  float pad_x = 0.0f;
  float pad_y = 0.0f;
  int source_width = 0;
  int source_height = 0;
};

bool load_vehicle_detector_config(const std::string &path,
                                  VehicleDetectorConfig *config,
                                  std::string *error);

class VehicleDetector {
public:
  VehicleDetector();
  ~VehicleDetector();

  bool load(const std::string &model_path, const VehicleDetectorConfig &config,
            std::string *error);
  bool loaded() const;
  bool preprocess_bgr(const uint8_t *image, int width, int height, int stride,
                      VehicleLetterbox *letterbox);
  bool execute(std::vector<VehicleDetection> *detections, double *execution_ms,
               std::string *error);

private:
  class Impl;
  std::unique_ptr<Impl> impl;
};
