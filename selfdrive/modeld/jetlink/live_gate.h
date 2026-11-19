#pragma once

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace eon_jetlink {
struct LiveControl { char magic[4]; uint32_t version; uint64_t revision, reserved; uint32_t enabled, tail; };
struct LiveResult {
  char magic[4]; uint32_t version; uint64_t revision, session; uint32_t sequence, frame;
  uint64_t started, completed, camera_ns; uint32_t size, flags;
};
struct LiveState {
  char magic[4]; uint32_t version; uint64_t stamp; uint32_t frame, flags, selected_frame, sequence;
  uint64_t session; uint32_t reason, reserved; uint64_t native_finished, revision;
};
static_assert(sizeof(LiveControl) == 32 && sizeof(LiveResult) == 64 && sizeof(LiveState) == 64, "EON Link live IPC ABI");
constexpr uint32_t LIVE_NATIVE_OK = 1, LIVE_ENGAGED = 2, LIVE_CALIBRATED = 4,
                   LIVE_EXTERNAL = 8, LIVE_LATCHED = 16, LIVE_CAN_OK = 32;
enum class LiveReason : uint32_t { Native = 0, Waiting = 1, Late = 2, Invalid = 3, Reentry = 4, External = 5, NativeInvalid = 6 };

inline bool read_live_file(const char *path, void *head, size_t size, std::vector<uint64_t> *body = nullptr) {
  int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  if (fd < 0) return false;
  struct stat st = {};
  bool ok = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && size_t(st.st_size) >= size && size_t(st.st_size) <= size + (body ? 131072 : 0);
  if (ok) ok = ::read(fd, head, size) == ssize_t(size);
  if (ok && body) {
    const size_t count = st.st_size - size;
    ok = count > 0 && count % 8 == 0;
    if (ok) { body->resize(count / 8); ok = ::read(fd, body->data(), count) == ssize_t(count); }
  } else if (ok) ok = size_t(st.st_size) == size;
  close(fd);
  return ok;
}

inline bool write_live_state(const char *path, const LiveState &state) {
  char temp[256];
  if (snprintf(temp, sizeof(temp), "%s.%d.tmp", path, int(getpid())) >= int(sizeof(temp))) return false;
  int fd = open(temp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
  if (fd < 0) return false;
  const bool ok = write(fd, &state, sizeof(state)) == ssize_t(sizeof(state));
  close(fd);
  if (ok && rename(temp, path) == 0) return true;
  unlink(temp); return false;
}

class LiveGate {
public:
  explicit LiveGate(const char *control = "/data/eonlink-control", const char *result = "/dev/shm/eonlink-result")
      : control_path_(control), result_path_(result) {}
  LiveControl control() const {
    LiveControl c = {};
    if (!read_live_file(control_path_, &c, sizeof(c)) || memcmp(c.magic, "ELC1", 4) || c.version != 1 || !c.revision || c.reserved || c.tail || c.enabled > 1)
      return {};
    return c;
  }
  // No waits, sockets or replacement of the original model's recurrent state.
  bool candidate(uint32_t current_frame, uint64_t now, bool native_ok, bool can_valid, bool calibrated, bool engaged) {
    const auto c = control();
    if (!engaged) latched_ = false;
    reason_ = c.enabled ? LiveReason::Waiting : LiveReason::Native;
    bool valid = native_ok && can_valid && calibrated;
    if (!native_ok) reason_ = LiveReason::NativeInvalid;
    if (c.enabled && valid && !latched_) {
      if (read_live_file(result_path_, &packet_, sizeof(packet_), &payload_) &&
          !memcmp(packet_.magic, "ELR1", 4) && packet_.version == 1 && packet_.revision == c.revision &&
          packet_.session && packet_.sequence && packet_.camera_ns && packet_.flags == 1 && packet_.size == payload_.size() * 8 &&
          packet_.size <= 131072 && packet_.started && packet_.started <= packet_.completed &&
          packet_.completed <= now && packet_.completed - packet_.started <= 50000000ULL &&
          now - packet_.started <= 100000000ULL && uint32_t(current_frame - packet_.frame) <= 1 &&
          !(packet_.session == last_session_ && packet_.sequence <= last_sequence_)) {
        if (engaged && (!active_ || (last_session_ && packet_.session != last_session_))) { reason_ = LiveReason::Reentry; return fallback(engaged); }
        ready_ = true; return true;
      }
      reason_ = LiveReason::Late;
    }
    return fallback(engaged);
  }
  bool fallback(bool engaged) {
    if (active_ && engaged) latched_ = true;
    active_ = ready_ = false;
    return false;
  }
  void reject(bool engaged) { reason_ = LiveReason::Invalid; fallback(engaged); }
  void applied() {
    last_session_ = packet_.session; last_sequence_ = packet_.sequence;
    active_ = true; ready_ = false; reason_ = LiveReason::External;
  }
  const LiveResult &packet() const { return packet_; }
  const std::vector<uint64_t> &payload() const { return payload_; }
  bool active() const { return active_; }
  bool latched() const { return latched_; }
  LiveReason reason() const { return reason_; }
private:
  const char *control_path_, *result_path_;
  LiveResult packet_ = {};
  std::vector<uint64_t> payload_;
  uint64_t last_session_ = 0;
  uint32_t last_sequence_ = 0;
  bool active_ = false, ready_ = false, latched_ = false;
  LiveReason reason_ = LiveReason::Native;
};
}  // namespace eon_jetlink
