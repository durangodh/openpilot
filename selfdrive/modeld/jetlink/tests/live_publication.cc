#include <fstream>
#include <iostream>
#include "selfdrive/modeld/jetlink/live_model.h"

int main(int argc, char **argv) {
  if (argc != 4) return 2;
  eon_jetlink::LiveGate gate(argv[1], argv[2]);
  eon_jetlink::PublicationContext c{};
  int send_ok;
  while (std::cin >> c.frame >> c.previous_frame >> c.camera_ns >> c.previous_camera_ns >> c.now_ns
                  >> c.enabled >> c.native_ok >> c.can_valid >> c.calibrated >> c.engaged >> send_ok) {
    int native_calls = 0, external_calls = 0;
    bool applied = eon_jetlink::publish_selected_model(gate, c, [&](MessageBuilder &message) {
      ++external_calls;
      if (!send_ok) return -1;
      auto bytes = message.toBytes();
      std::ofstream out(argv[3], std::ios::binary | std::ios::trunc);
      out.write(reinterpret_cast<const char*>(bytes.begin()), bytes.size());
      return int(bytes.size());
    }, [&]() { ++native_calls; });
    std::cout << applied << ' ' << native_calls << ' ' << external_calls << ' '
              << gate.latched() << ' ' << uint32_t(gate.reason()) << std::endl;
  }
}
