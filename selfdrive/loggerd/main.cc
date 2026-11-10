#include "selfdrive/loggerd/loggerd.h"

#include <sys/resource.h>
#include <unistd.h>

int main(int argc, char** argv) {
  if (Hardware::EON()) {
    // Honor the web recording switch even when started outside the manager.
    if (access("/persist/comma/enable-eon-route-logging", F_OK) != 0) return 0;
    setpriority(PRIO_PROCESS, 0, -20);
  } else if (Hardware::TICI()) {
    int ret;
    ret = util::set_core_affinity({0, 1, 2, 3});
    assert(ret == 0);
    // TODO: why does this impact camerad timings?
    //ret = util::set_realtime_priority(1);
    //assert(ret == 0);
  }

  loggerd_thread();

  return 0;
}
