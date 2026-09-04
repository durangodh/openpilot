#include "selfdrive/modeld/models/vehicle_detector.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <sstream>
#include <utility>

#include <DlContainer/IDlContainer.hpp>
#include <DlSystem/DlError.hpp>
#include <DlSystem/IUserBuffer.hpp>
#include <DlSystem/IUserBufferFactory.hpp>
#include <DlSystem/UserBufferMap.hpp>
#include <SNPE/SNPE.hpp>
#include <SNPE/SNPEBuilder.hpp>
#include <SNPE/SNPEFactory.hpp>

#include "json11.hpp"
#include "selfdrive/common/timing.h"
#include "selfdrive/common/util.h"

namespace {

size_t tensor_size(const zdl::DlSystem::TensorShape &shape) {
  size_t size = 1;
  for (size_t i = 0; i < shape.rank(); ++i) size *= shape[i];
  return size;
}

std::vector<size_t> tensor_strides(const zdl::DlSystem::TensorShape &shape) {
  std::vector<size_t> strides(shape.rank());
  if (strides.empty()) return strides;
  strides.back() = sizeof(float);
  for (size_t i = shape.rank() - 1; i > 0; --i) {
    strides[i - 1] = strides[i] * shape[i];
  }
  return strides;
}

std::string lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

float clamp01(float value) {
  return std::max(0.0f, std::min(1.0f, value));
}

bool has_class(const VehicleDetectorConfig &config, int class_id) {
  return std::find(config.vehicle_class_ids.begin(), config.vehicle_class_ids.end(),
                   class_id) != config.vehicle_class_ids.end();
}

float intersection_over_union(const VehicleDetection &a, const VehicleDetection &b) {
  const float left = std::max(a.left, b.left);
  const float top = std::max(a.top, b.top);
  const float right = std::min(a.right, b.right);
  const float bottom = std::min(a.bottom, b.bottom);
  const float intersection = std::max(0.0f, right - left) * std::max(0.0f, bottom - top);
  const float area_a = std::max(0.0f, a.right - a.left) * std::max(0.0f, a.bottom - a.top);
  const float area_b = std::max(0.0f, b.right - b.left) * std::max(0.0f, b.bottom - b.top);
  const float total = area_a + area_b - intersection;
  return total > 0.0f ? intersection / total : 0.0f;
}

}  // namespace

bool load_vehicle_detector_config(const std::string &path,
                                  VehicleDetectorConfig *config,
                                  std::string *error) {
  if (config == nullptr) return false;
  const std::string data = util::read_file(path);
  if (data.empty()) {
    if (error != nullptr) *error = "empty or missing detector config";
    return false;
  }
  std::string parse_error;
  const json11::Json root = json11::Json::parse(data, parse_error);
  if (!parse_error.empty() || !root.is_object()) {
    if (error != nullptr) *error = "invalid detector config: " + parse_error;
    return false;
  }

  VehicleDetectorConfig parsed;
  parsed.input_width = root["input_width"].int_value();
  parsed.input_height = root["input_height"].int_value();
  parsed.max_detections = root["max_detections"].int_value();
  parsed.input_mean = static_cast<float>(root["input_mean"].number_value());
  parsed.input_scale = static_cast<float>(root["input_scale"].number_value());
  parsed.confidence_threshold = static_cast<float>(root["confidence_threshold"].number_value());
  parsed.boxes_tensor = root["boxes_tensor"].string_value();
  parsed.scores_tensor = root["scores_tensor"].string_value();
  parsed.classes_tensor = root["classes_tensor"].string_value();
  parsed.count_tensor = root["count_tensor"].string_value();
  parsed.boxes_format = lower(root["boxes_format"].string_value());
  parsed.vehicle_class_ids.clear();
  for (const auto &item : root["vehicle_class_ids"].array_items()) {
    parsed.vehicle_class_ids.push_back(item.int_value());
  }

  const bool valid = parsed.input_width >= 160 && parsed.input_width <= 640 &&
                     parsed.input_height >= 160 && parsed.input_height <= 640 &&
                     parsed.max_detections >= 1 && parsed.max_detections <= 100 &&
                     std::isfinite(parsed.input_mean) && std::isfinite(parsed.input_scale) &&
                     parsed.input_scale > 0.0f &&
                     parsed.confidence_threshold >= 0.1f &&
                     parsed.confidence_threshold <= 0.99f &&
                     !parsed.boxes_tensor.empty() && !parsed.scores_tensor.empty() &&
                     !parsed.classes_tensor.empty() &&
                     (parsed.boxes_format == "yxyx" || parsed.boxes_format == "xyxy") &&
                     !parsed.vehicle_class_ids.empty();
  if (!valid) {
    if (error != nullptr) *error = "detector config values are outside safe limits";
    return false;
  }
  *config = std::move(parsed);
  return true;
}

class VehicleDetector::Impl {
public:
  struct OutputTensor {
    std::string name;
    std::vector<float> data;
    std::unique_ptr<zdl::DlSystem::IUserBuffer> buffer;
  };

  VehicleDetectorConfig config;
  std::string model_data;
  std::unique_ptr<zdl::SNPE::SNPE> snpe;
  zdl::DlSystem::UserBufferMap input_map;
  zdl::DlSystem::UserBufferMap output_map;
  std::vector<float> input;
  std::unique_ptr<zdl::DlSystem::IUserBuffer> input_buffer;
  std::vector<std::unique_ptr<OutputTensor>> outputs;

  const OutputTensor *find_output(const std::string &token) const {
    const std::string wanted = lower(token);
    for (const auto &output : outputs) {
      if (lower(output->name).find(wanted) != std::string::npos) return output.get();
    }
    return nullptr;
  }

  bool parse_outputs(std::vector<VehicleDetection> *detections, std::string *error) const {
    detections->clear();
    const OutputTensor *boxes = find_output(config.boxes_tensor);
    const OutputTensor *scores = find_output(config.scores_tensor);
    const OutputTensor *classes = find_output(config.classes_tensor);
    const OutputTensor *count = config.count_tensor.empty() ? nullptr : find_output(config.count_tensor);

    if (boxes != nullptr && scores != nullptr && classes != nullptr) {
      size_t available = std::min(scores->data.size(), classes->data.size());
      available = std::min(available, boxes->data.size() / 4);
      if (count != nullptr && !count->data.empty() && std::isfinite(count->data[0])) {
        const float reported = std::max(0.0f, count->data[0]);
        if (reported < static_cast<float>(available)) {
          available = static_cast<size_t>(reported);
        }
      }
      available = std::min(available, static_cast<size_t>(config.max_detections));
      for (size_t i = 0; i < available; ++i) {
        if (!std::isfinite(classes->data[i]) || std::abs(classes->data[i]) > 10000.0f) continue;
        VehicleDetection detection;
        detection.class_id = static_cast<int>(std::lround(classes->data[i]));
        detection.confidence = scores->data[i];
        if (config.boxes_format == "yxyx") {
          detection.top = boxes->data[i * 4];
          detection.left = boxes->data[i * 4 + 1];
          detection.bottom = boxes->data[i * 4 + 2];
          detection.right = boxes->data[i * 4 + 3];
        } else {
          detection.left = boxes->data[i * 4];
          detection.top = boxes->data[i * 4 + 1];
          detection.right = boxes->data[i * 4 + 2];
          detection.bottom = boxes->data[i * 4 + 3];
        }
        add_detection(&detection, detections);
      }
    } else if (outputs.size() == 1 && outputs[0]->data.size() >= 7) {
      // Caffe SSD DetectionOutput: image_id, class, score, xmin, ymin, xmax, ymax.
      const auto &packed = outputs[0]->data;
      const size_t available = std::min(packed.size() / 7,
                                        static_cast<size_t>(config.max_detections));
      for (size_t i = 0; i < available; ++i) {
        const float *row = &packed[i * 7];
        if (row[0] < 0.0f) break;
        if (!std::isfinite(row[1]) || std::abs(row[1]) > 10000.0f) continue;
        VehicleDetection detection;
        detection.class_id = static_cast<int>(std::lround(row[1]));
        detection.confidence = row[2];
        detection.left = row[3];
        detection.top = row[4];
        detection.right = row[5];
        detection.bottom = row[6];
        add_detection(&detection, detections);
      }
    } else {
      if (error != nullptr) {
        std::ostringstream stream;
        stream << "required detector outputs missing; found";
        for (const auto &output : outputs) stream << " " << output->name;
        *error = stream.str();
      }
      return false;
    }

    std::sort(detections->begin(), detections->end(),
              [](const VehicleDetection &a, const VehicleDetection &b) {
                return a.confidence > b.confidence;
              });
    std::vector<VehicleDetection> kept;
    for (const auto &detection : *detections) {
      bool duplicate = false;
      for (const auto &current : kept) {
        if (intersection_over_union(detection, current) > 0.70f) {
          duplicate = true;
          break;
        }
      }
      if (!duplicate) kept.push_back(detection);
    }
    *detections = std::move(kept);
    return true;
  }

private:
  void add_detection(VehicleDetection *detection,
                     std::vector<VehicleDetection> *detections) const {
    if (!std::isfinite(detection->confidence) ||
        detection->confidence < config.confidence_threshold ||
        !has_class(config, detection->class_id)) return;
    if (!std::isfinite(detection->left) || !std::isfinite(detection->top) ||
        !std::isfinite(detection->right) || !std::isfinite(detection->bottom)) return;
    detection->confidence = clamp01(detection->confidence);
    detection->left = clamp01(detection->left);
    detection->top = clamp01(detection->top);
    detection->right = clamp01(detection->right);
    detection->bottom = clamp01(detection->bottom);
    if (detection->right - detection->left < 0.01f ||
        detection->bottom - detection->top < 0.01f) return;
    detections->push_back(*detection);
  }
};

VehicleDetector::VehicleDetector() : impl(std::make_unique<Impl>()) {}
VehicleDetector::~VehicleDetector() = default;

bool VehicleDetector::load(const std::string &model_path,
                           const VehicleDetectorConfig &config,
                           std::string *error) {
  impl = std::make_unique<Impl>();
  impl->config = config;
  if (!zdl::SNPE::SNPEFactory::isRuntimeAvailable(zdl::DlSystem::Runtime_t::DSP)) {
    if (error != nullptr) *error = "SNPE DSP runtime is unavailable";
    return false;
  }
  impl->model_data = util::read_file(model_path);
  if (impl->model_data.empty()) {
    if (error != nullptr) *error = "missing vehicle detector DLC";
    return false;
  }
  auto container = zdl::DlContainer::IDlContainer::open(
      reinterpret_cast<const uint8_t *>(impl->model_data.data()), impl->model_data.size());
  if (!container) {
    if (error != nullptr) *error = zdl::DlSystem::getLastErrorString();
    return false;
  }
  impl->snpe = zdl::SNPE::SNPEBuilder(container.get())
      .setOutputLayers({})
      .setRuntimeProcessor(zdl::DlSystem::Runtime_t::DSP)
      .setCPUFallbackMode(false)
      .setUseUserSuppliedBuffers(true)
      .setPerformanceProfile(zdl::DlSystem::PerformanceProfile_t::POWER_SAVER)
      .build();
  if (!impl->snpe) {
    if (error != nullptr) *error = zdl::DlSystem::getLastErrorString();
    return false;
  }

  const auto input_names_optional = impl->snpe->getInputTensorNames();
  const auto output_names_optional = impl->snpe->getOutputTensorNames();
  if (!input_names_optional || !output_names_optional) {
    if (error != nullptr) *error = "detector must have one input and at least one output";
    return false;
  }
  const auto &input_names = *input_names_optional;
  const auto &output_names = *output_names_optional;
  if (input_names.size() != 1 || output_names.size() == 0) {
    if (error != nullptr) *error = "detector must have one input and at least one output";
    return false;
  }
  const char *input_name = input_names.at(0);
  const auto input_shape_optional = impl->snpe->getInputDimensions(input_name);
  if (!input_shape_optional) {
    if (error != nullptr) *error = "detector input dimensions are unavailable";
    return false;
  }
  const auto &input_shape = *input_shape_optional;
  if (input_shape.rank() != 4 || input_shape[0] != 1 ||
      input_shape[1] != static_cast<size_t>(config.input_height) ||
      input_shape[2] != static_cast<size_t>(config.input_width) ||
      input_shape[3] != 3) {
    if (error != nullptr) *error = "detector input must be NHWC [1,height,width,3]";
    return false;
  }

  zdl::DlSystem::UserBufferEncodingFloat float_encoding;
  auto &factory = zdl::SNPE::SNPEFactory::getUserBufferFactory();
  impl->input.resize(tensor_size(input_shape));
  impl->input_buffer = factory.createUserBuffer(
      impl->input.data(), impl->input.size() * sizeof(float),
      tensor_strides(input_shape), &float_encoding);
  if (!impl->input_buffer) {
    if (error != nullptr) *error = "failed to create detector input buffer";
    return false;
  }
  impl->input_map.add(input_name, impl->input_buffer.get());

  impl->outputs.reserve(output_names.size());
  for (size_t i = 0; i < output_names.size(); ++i) {
    const char *name = output_names.at(i);
    const auto attributes = impl->snpe->getInputOutputBufferAttributes(name);
    if (!attributes) {
      if (error != nullptr) *error = "missing output attributes for " + std::string(name);
      return false;
    }
    auto output = std::make_unique<Impl::OutputTensor>();
    output->name = name;
    const zdl::DlSystem::TensorShape shape = (*attributes)->getDims();
    const size_t elements = tensor_size(shape);
    // Reject malformed or unrelated DLCs before they can consume excessive
    // memory. SSD post-NMS outputs are normally only tens to thousands long.
    if (elements == 0 || elements > 1024 * 1024) {
      if (error != nullptr) *error = "detector output is outside safe size limits";
      return false;
    }
    output->data.resize(elements);
    output->buffer = factory.createUserBuffer(
        output->data.data(), output->data.size() * sizeof(float),
        tensor_strides(shape), &float_encoding);
    if (!output->buffer) {
      if (error != nullptr) *error = "failed to create detector output buffer";
      return false;
    }
    impl->output_map.add(name, output->buffer.get());
    impl->outputs.push_back(std::move(output));
  }
  return true;
}

bool VehicleDetector::loaded() const {
  return impl != nullptr && impl->snpe != nullptr;
}

bool VehicleDetector::preprocess_bgr(const uint8_t *image, int width, int height,
                                     int stride, VehicleLetterbox *letterbox) {
  if (!loaded() || image == nullptr || width <= 0 || height <= 0 || stride < width * 3) {
    return false;
  }
  const int dst_width = impl->config.input_width;
  const int dst_height = impl->config.input_height;
  const float scale = std::min(static_cast<float>(dst_width) / width,
                               static_cast<float>(dst_height) / height);
  const float scaled_width = width * scale;
  const float scaled_height = height * scale;
  const float pad_x = (dst_width - scaled_width) * 0.5f;
  const float pad_y = (dst_height - scaled_height) * 0.5f;
  const float black = (0.0f - impl->config.input_mean) * impl->config.input_scale;
  std::fill(impl->input.begin(), impl->input.end(), black);

  for (int y = 0; y < dst_height; ++y) {
    const float source_y = (y + 0.5f - pad_y) / scale - 0.5f;
    if (source_y < 0.0f || source_y > height - 1.0f) continue;
    const int y0 = static_cast<int>(source_y);
    const int y1 = std::min(y0 + 1, height - 1);
    const float fy = source_y - y0;
    for (int x = 0; x < dst_width; ++x) {
      const float source_x = (x + 0.5f - pad_x) / scale - 0.5f;
      if (source_x < 0.0f || source_x > width - 1.0f) continue;
      const int x0 = static_cast<int>(source_x);
      const int x1 = std::min(x0 + 1, width - 1);
      const float fx = source_x - x0;
      const uint8_t *p00 = image + y0 * stride + x0 * 3;
      const uint8_t *p01 = image + y0 * stride + x1 * 3;
      const uint8_t *p10 = image + y1 * stride + x0 * 3;
      const uint8_t *p11 = image + y1 * stride + x1 * 3;
      float *destination = &impl->input[(y * dst_width + x) * 3];
      for (int channel = 0; channel < 3; ++channel) {
        // camerad RGB buffers are stored B,G,R; the detector contract is RGB.
        const int source_channel = 2 - channel;
        const float top = p00[source_channel] + (p01[source_channel] - p00[source_channel]) * fx;
        const float bottom = p10[source_channel] + (p11[source_channel] - p10[source_channel]) * fx;
        const float value = top + (bottom - top) * fy;
        destination[channel] = (value - impl->config.input_mean) * impl->config.input_scale;
      }
    }
  }
  if (letterbox != nullptr) {
    letterbox->scale = scale;
    letterbox->pad_x = pad_x;
    letterbox->pad_y = pad_y;
    letterbox->source_width = width;
    letterbox->source_height = height;
  }
  return true;
}

bool VehicleDetector::execute(std::vector<VehicleDetection> *detections,
                              double *execution_ms, std::string *error) {
  if (!loaded() || detections == nullptr) return false;
  const double start = millis_since_boot();
  if (!impl->snpe->execute(impl->input_map, impl->output_map)) {
    if (error != nullptr) *error = zdl::DlSystem::getLastErrorString();
    return false;
  }
  if (execution_ms != nullptr) *execution_ms = millis_since_boot() - start;
  return impl->parse_outputs(detections, error);
}
