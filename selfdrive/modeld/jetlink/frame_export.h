#pragma once

// Opt-in, non-blocking snapshot for N16 diagnostics or live inference. This never reads a
// remote model result or changes modeld's recurrent/output buffers.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

namespace eon_jetlink {
constexpr size_t VIEW_SIZE = 6 * 128 * 256;
constexpr size_t IMAGE_SIZE = 2 * VIEW_SIZE;
struct Header {
  char magic[4];
  uint32_t version;
  uint64_t generation;
  uint64_t input_started_ns;
  uint32_t frame_id;
  uint32_t flags;  // bit 0: single camera; bit 1: live calibration seen
  float desire[8];
  float traffic[2];
  uint64_t camera_timestamp_ns;
  uint8_t padding[16];
};
static_assert(sizeof(Header) == 96, "benchmark frame header ABI");
constexpr size_t FRAME_SIZE = sizeof(Header) + IMAGE_SIZE;
inline uint64_t monotonic_ns() {
  struct timespec ts = {};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return uint64_t(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}

class FrameExport {
public:
  FrameExport(const char *demand = "/dev/shm/eon-n16-demand",
              const char *frames = "/dev/shm/eon-n16-frame") : demand_(demand), frames_(frames) {}
  ~FrameExport() {
    if (mapping_) munmap(mapping_, FRAME_SIZE);
    if (fd_ >= 0) close(fd_);
  }
  FrameExport(const FrameExport&) = delete;
  FrameExport& operator=(const FrameExport&) = delete;

  bool wanted() {
    uint64_t now = monotonic_ns();
    if (now < next_check_) return wanted_;
    next_check_ = now + 250000000ULL;
    struct stat st = {};
    struct timespec wall = {};
    clock_gettime(CLOCK_REALTIME, &wall);
    double age = 1e10;
    if (stat(demand_, &st) == 0 && S_ISREG(st.st_mode)) {
#ifdef __APPLE__
      const auto modified = st.st_mtimespec;
#else
      const auto modified = st.st_mtim;
#endif
      age = double(wall.tv_sec - modified.tv_sec) + double(wall.tv_nsec - modified.tv_nsec) / 1e9;
    }
    wanted_ = age >= -0.1 && age < 2.0;
    return wanted_;
  }

  bool publish(const float *main, const float *wide, const float *desire,
               const float *traffic, uint32_t frame_id, uint64_t started_ns,
               uint64_t camera_ns, uint32_t flags) {
    if (!main || !wide || !desire || !traffic || !wanted()) return false;
    if (!mapping_ && !open_mapping()) return false;
    // A slow diagnostic reader must never stall the internal model.
    if (flock(fd_, LOCK_EX | LOCK_NB) != 0) return false;
    Header *h = static_cast<Header*>(mapping_);
    h->generation = ++generation_;
    h->input_started_ns = started_ns;
    h->frame_id = frame_id;
    h->flags = flags;
    h->camera_timestamp_ns = camera_ns;
    memcpy(h->desire, desire, sizeof(h->desire));
    memcpy(h->traffic, traffic, sizeof(h->traffic));
    uint8_t *images = reinterpret_cast<uint8_t*>(mapping_) + sizeof(Header);
    bool valid = true;
    for (size_t i = 0; i < IMAGE_SIZE; ++i) {
      float value = i < VIEW_SIZE ? main[i] : wide[i - VIEW_SIZE];
      if (!std::isfinite(value)) { valid = false; break; }
      images[i] = static_cast<uint8_t>(std::max(0.0f, std::min(255.0f, value)));
    }
    if (valid) h->generation = ++generation_;
    else ++generation_;  // leave this frame odd/invalid
    flock(fd_, LOCK_UN);
    return valid;
  }

private:
  bool open_mapping() {
    fd_ = open(frames_, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd_ < 0) return false;
    if (flock(fd_, LOCK_EX | LOCK_NB) != 0) { close(fd_); fd_ = -1; return false; }
    struct stat st = {};
    if (fstat(fd_, &st) != 0 || !S_ISREG(st.st_mode) || ftruncate(fd_, FRAME_SIZE) != 0) {
      close(fd_); fd_ = -1; return false;
    }
    void *p = mmap(nullptr, FRAME_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
    if (p == MAP_FAILED) { close(fd_); fd_ = -1; return false; }
    mapping_ = p;
    Header *h = static_cast<Header*>(p);
    memset(h, 0, sizeof(*h));
    memcpy(h->magic, "JLE1", 4);
    h->version = 1;
    flock(fd_, LOCK_UN);
    return true;
  }
  const char *demand_;
  const char *frames_;
  int fd_ = -1;
  void *mapping_ = nullptr;
  uint64_t generation_ = 0, next_check_ = 0;
  bool wanted_ = false;
};
}  // namespace eon_jetlink
